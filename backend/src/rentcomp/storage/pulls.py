"""The `PullLoader` seam — how `pull_ref` becomes shaped comps (ADR-001 §1.1).

The request addresses evidence the *server* holds, by reference. Two reasons,
both structural: NORTH_STAR requires every number to trace to real observed
comps rather than to whatever a client uploaded, and re-posting ~40KB of comps
every 150ms during a slider drag is waste this design does not need.

Resolution goes through exactly one function so the thing behind it can be
replaced without an API change:

    F0-S2 (now)   a pre-shaped synthetic pull, read from `fixtures/synthetic/pulls/`
    WS-1          ONE named real ref (`WS1_REAL_PULL_REF`), routed through
                  the real record-shaping chain (`pipeline.shape.shape_raw_pull`)
                  over the two committed T-S3 gate fixtures
                  (`fixtures/live-samples/`) — see `_load_ws1_real_pull` below
    F3-S1 + F4-S3 the real cache (`~/.rentcomp/cache/<key>/`) — every pull,
                  general-purpose, resolved by content-hashed cache key

WS-1's WIRING (contract item 3, PM-confirmed — see the WS-1 handoff): a
`pull_ref` of `WS1_REAL_PULL_REF` ("ws1-real") is special-cased in
`_shaped_pull` before the pre-shaped-JSON path runs. It is not a
`fixtures/synthetic/pulls/*.json` file at all — it reads the two REAL raw
RentCast responses the T-S3 gate committed, shapes them for real, and
returns `ShapedPull` exactly like every other ref. This is deliberately
independent of `FIXTURE_PULLS_ENV`/`fixture_pulls_dir()`: `live-samples/` is
a fixed location relative to the repo, not a synthetic-pulls directory an
E2E harness redirects, so the Playwright spec that drives the real built app
against a fresh `RENTCOMP_HOME` finds it with zero extra seeding.

WHY THE I/O IS HERE AND NOT IN `pipeline/`
------------------------------------------
ADR-001 §4.2: no I/O below the API edge. Every pipeline input arrives as an
argument, which is what makes two identical requests impossible to separate by
anything that is not in the request. Reading files is a storage concern; the
memo that keeps it off the hot path lives here too, "at the storage edge —
never inside `pipeline/`" (§3).

Group A (record shaping) is memoized because it is I/O + parsing; Group B (the
per-request derivation) is not memoized and does not need to be — at n ≤ 100
it is well under a millisecond, and a memo there would import a
cache-invalidation bug surface into the exact code path whose entire purpose
is that staleness cannot exist.
"""

from __future__ import annotations

import json
import os
from datetime import date
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from rentcomp.models.domain import StitchedComp
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

__all__ = [
    "FIXTURE_PULLS_ENV",
    "WS1_REAL_PULL_REF",
    "PullNotFoundError",
    "ShapedPull",
    "config_digest",
    "fixture_pulls_dir",
    "load_shaped_pull",
]

#: Overrides where synthetic pulls are read from (tests, E2E harnesses).
FIXTURE_PULLS_ENV = "RENTCOMP_FIXTURE_PULLS_DIR"

#: WS-1's one named real pull (contract item 3 — see module docstring). The
#: frontend's hardcoded Results-view request uses exactly this ref.
WS1_REAL_PULL_REF = "ws1-real"

#: The T-S3 gate's committed raw fixtures WS1_REAL_PULL_REF shapes — the
#: exact pull that GO'd at 337 in-window comps (QUEUE.md row 6). Paths
#: resolved relative to this file, like `fixture_pulls_dir()`, so they are
#: found regardless of `RENTCOMP_HOME`/`FIXTURE_PULLS_ENV`.
_LIVE_SAMPLES_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "live-samples"
_WS1_ACTIVE_FIXTURE = _LIVE_SAMPLES_DIR / "fe9de5158f036802.json"
_WS1_INACTIVE_FIXTURE = _LIVE_SAMPLES_DIR / "6327600317b11d16.json"

#: PM scope ruling (QUEUE.md row 6) / the WS-1 handoff: `as_of` matches the
#: fixtures' own `lastSeenDate` timestamps, and the window is read literally
#: as the full calendar year (no month-day narrowing was specified).
_WS1_AS_OF = date(2026, 7, 27)
_WS1_WINDOW = ("01-01", "12-31")


class PullNotFoundError(LookupError):
    """No pull exists at that ref.

    A stale workspace pointing at a pull that is gone is a normal condition
    (F1's "corrupt/missing cache entry" edge), so the API answers 404 — never
    a 500, and never an empty-but-successful derive, which would look like a
    market with no comps in it.
    """


class ShapedPull(BaseModel):
    """One immutable pull, after record shaping — the graph's whole evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ref: str
    #: The manifest's fetch date. This is `meta.as_of`, and it is the ONLY
    #: "now" the pipeline has (owner ruling 1): censored means still active as
    #: of *this* date, not as of whenever the workspace was reopened.
    as_of: date
    #: Content digest of the raw bytes at `ref`. Changes whenever the evidence
    #: changes, which is what makes it usable as part of a memo key.
    digest: str
    comps: tuple[StitchedComp, ...]


class _PullDocument(BaseModel):
    """The on-disk shape of a synthetic pull (F0-S2 only — see the module docstring)."""

    model_config = ConfigDict(extra="ignore")

    fetched_at: date
    comps: tuple[StitchedComp, ...]


def fixture_pulls_dir() -> Path:
    """Where synthetic pulls live — `fixtures/synthetic/pulls/` in the repo.

    Resolved at call time, not at import (the same rule as `rentcomp_home`):
    tests and E2E harnesses point the override at a temp directory after the
    package has already been imported.
    """
    override = os.environ.get(FIXTURE_PULLS_ENV)
    if override:
        return Path(override).expanduser()
    # __file__ = <repo>/backend/src/rentcomp/storage/pulls.py → parents[4] = <repo>
    return Path(__file__).resolve().parents[4] / "fixtures" / "synthetic" / "pulls"


def load_shaped_pull(pull_ref: str, config: Config) -> ShapedPull:
    """Resolve `pull_ref` to shaped comps. Raises `PullNotFoundError` if absent.

    Memoized per ADR-001 §3 (~4 workspaces). The ADR's key is
    `(pull_ref, pull_digest, config, as_of)`; the cache below keys on
    `(store root, pull_ref, config)` instead, because the digest and the fetch
    date are *functions of the file at that ref*, and `cache/` is immutable raw
    responses by construction (ARCHITECTURE.md §5) — a refresh writes a new
    ref, it never rewrites one. Logged as a [DEFAULT] deviation in the F0-S2
    handoff.

    The **root is in the key** and is resolved on every call, not at import:
    an E2E harness (or a Layer-2 test) points the store at a temp directory
    after this module has been imported, and a memo that ignored the root
    would keep serving the pull from wherever the process first looked.

    `config` is in the key deliberately: F0-S5's load-bearing clause is "a knob
    change re-derives like any other input", and a memo that ignored config
    would silently break it. `Config` being hashable at all is ADR-001 §3.1.

    A pure function cache: same key ⇒ same value, so it cannot affect
    idempotence. Tests that rewrite a store in place call `.cache_clear()`.
    """
    return _shaped_pull(fixture_pulls_dir(), pull_ref, config)


@lru_cache(maxsize=4)
def _shaped_pull(root: Path, pull_ref: str, config: Config) -> ShapedPull:
    if pull_ref == WS1_REAL_PULL_REF:
        return _load_ws1_real_pull(config)

    path = root / f"{_safe_ref(pull_ref)}.json"
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        raise PullNotFoundError(f"no pull found for ref {pull_ref!r}") from exc

    try:
        document = _PullDocument.model_validate_json(raw)
    except ValidationError as exc:
        raise ValueError(f"pull {pull_ref!r} at {path} is not a valid pull: {exc}") from exc

    return ShapedPull(
        ref=pull_ref,
        as_of=document.fetched_at,
        digest=sha256(raw).hexdigest(),
        comps=document.comps,
    )


def _load_ws1_real_pull(config: Config) -> ShapedPull:
    """WS-1's real pull (contract item 3): the two committed T-S3 gate raw
    fixtures, routed through the real record-shaping chain.

    Raises `PullNotFoundError` if the fixtures are missing (same failure
    contract as every other ref) rather than a raw `FileNotFoundError`.
    """
    try:
        active_raw = _WS1_ACTIVE_FIXTURE.read_bytes()
        inactive_raw = _WS1_INACTIVE_FIXTURE.read_bytes()
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        raise PullNotFoundError(
            f"no pull found for ref {WS1_REAL_PULL_REF!r} (live-samples fixtures missing)"
        ) from exc

    active = json.loads(active_raw)
    inactive = json.loads(inactive_raw)
    comps = shape_raw_pull(active, inactive, config, _WS1_AS_OF, *_WS1_WINDOW)
    digest = sha256(active_raw + inactive_raw).hexdigest()
    return ShapedPull(ref=WS1_REAL_PULL_REF, as_of=_WS1_AS_OF, digest=digest, comps=comps)


#: The memo's controls, exposed on the public entry point so callers never
#: reach for the private cached function (and so QA's `clear_pipeline_caches`
#: sweep finds them wherever it looks).
load_shaped_pull.cache_clear = _shaped_pull.cache_clear
load_shaped_pull.cache_info = _shaped_pull.cache_info


#: Everything a legal ref may contain. F3-S1's cache keys are hex digests and
#: the synthetic refs are hyphenated words, so both fit comfortably.
_REF_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


def _safe_ref(pull_ref: str) -> str:
    """Reject anything that could escape the pulls directory.

    A ref reaches this process from a request body, so `../../etc/passwd` is a
    thing a client can ask for. A whitelist rather than a blacklist of
    separators: it also rules out the null byte (which would otherwise reach
    `open()` and surface as a 500 instead of a 404) and every other encoding
    trick, and it cannot be defeated by a separator this code did not think of.
    A leading dot is rejected separately so `..` cannot survive the alphabet.
    """
    if (
        not pull_ref
        or pull_ref.startswith(".")
        or not _REF_ALPHABET.issuperset(pull_ref)
    ):
        raise PullNotFoundError(f"no pull found for ref {pull_ref!r}")
    return pull_ref


def config_digest(config: Config) -> str:
    """A stable digest of the knobs a derive ran under.

    Reported in `meta.config_digest` so that "a knob change re-derives like any
    other input" is *checkable* from outside: two derives whose digests differ
    ran under different knobs, and any memo keyed on it cannot serve one for
    the other. Canonical JSON (sorted keys) so the digest depends on values,
    never on field order.
    """
    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return sha256(payload.encode("utf-8")).hexdigest()
