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
from rentcomp.storage.cache import (
    CORRUPT_MANIFEST_ERRORS,
    CacheMissError,
    manifest_fingerprint,
    raw_response_paths,
    read_manifest,
    read_raw_records,
)
from rentcomp.storage.config import Config

__all__ = [
    "FIXTURE_PULLS_ENV",
    "WS1_REAL_PULL_REF",
    "PullNotFoundError",
    "ShapedPull",
    "config_digest",
    "fixture_pulls_dir",
    "load_shaped_pull",
    "pull_exists",
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
    `(pull_ref, pull_digest, config, as_of)`; F0-S2 dropped the digest and keyed
    on `(store root, pull_ref, config)` on the premise that "a refresh writes a
    new ref, it never rewrites one".

    **F4-S9 restores the digest, because that premise is false.** Completing a
    partial pull (§5a: "a pull interrupted at 3/4 costs exactly 1 call to
    finish") appends new evidence *under the existing ref* — so a memo that
    ignores content keeps serving the pre-completion comps forever, and a
    missing cohort skews every number downstream (D24 §5a). What goes in the
    key is `_evidence_version` (below), not a content hash: a hash would mean
    re-reading and re-digesting every raw byte on the request path the memo
    exists to keep off disk.

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
    return _shaped_pull(fixture_pulls_dir(), pull_ref, config, _evidence_version(pull_ref))


def pull_exists(pull_ref: str) -> bool:
    """Is there evidence at this ref — **without shaping any of it** (F1-S2)?

    The recents index asks this once per row on every render of Home, so it has
    to be cheap: a `stat` and a manifest read, never `load_shaped_pull`. A
    workspace whose pull is gone is F1's "missing cache entry" edge — an error
    row offering refresh — and answering it by deriving would blow the epic's
    "<1s" budget to compute a boolean.

    Handles all three kinds of ref (WS-1's fixtures, a real cache key, a
    synthetic pull) here rather than at the caller, so the one place that knows
    how a ref resolves stays the one place that knows whether it resolves.

    **Total: it answers `False` rather than raising, for every shape of broken
    entry.** Its one caller is the recents index, which renders Home; an
    exception escaping here is not a bad row, it is no Home screen at all —
    the "never a crash" the AC forbids. `CORRUPT_MANIFEST_ERRORS` is imported
    rather than spelled out for the reason F2-S1 introduced it: a hand-written
    `(OSError, ValueError, KeyError)` does NOT catch the `TypeError` /
    `AttributeError` that a right-shape-wrong-types manifest raises inside
    coercion, which is how two corruption shapes escaped as anonymous 500s in
    F4-S9. Measured here before the tuple was used: a manifest with
    `"as_of": 17` raised `TypeError: fromisoformat: argument must be str`.

    False is also the honest answer for a corrupt entry, not merely the safe
    one: an entry that cannot be read cannot be derived from either, so the row
    is un-openable and gets the same error-and-offer-refresh treatment as one
    that is gone. Nothing here re-fetches to work around it (D24) — refresh
    stays the user's decision.
    """
    try:
        safe_ref = _safe_ref(pull_ref)
    except PullNotFoundError:
        return False

    if safe_ref == WS1_REAL_PULL_REF:
        return _WS1_ACTIVE_FIXTURE.is_file() and _WS1_INACTIVE_FIXTURE.is_file()

    if _looks_like_cache_key(safe_ref):
        try:
            read_manifest(safe_ref)
            # A manifest with no bytes behind it is exactly as "not there yet"
            # as no entry at all — the rule `_load_cache_backed_pull` applies.
            return bool(raw_response_paths(safe_ref))
        except (CacheMissError, *CORRUPT_MANIFEST_ERRORS):
            return False

    try:
        return (fixture_pulls_dir() / f"{safe_ref}.json").is_file()
    except OSError:
        return False


def _evidence_version(pull_ref: str) -> str:
    """A cheap fingerprint of everything a shaped pull is built from — the
    memo's staleness guard (see `load_shaped_pull`).

    Two halves, because `_load_cache_backed_pull` reads two things:

    * **the raw responses**, as `(name, size, mtime_ns)` per file rather than a
      digest of their contents. `cache/` is append-only immutable evidence by
      construction (ARCHITECTURE.md §5): the only way the shaped result can
      change is a file appearing, and that is visible in the name set alone.
      Size and mtime are belt-and-braces for an in-place rewrite, and cost two
      `stat` calls per file against the whole-file read a hash would need.
    * **the manifest** (F3-S4). `as_of` and `window` come from it and are handed
      straight to `shape_raw_pull`, so a manifest that changes while the bytes
      do not changes every censored comp's `effective_dom` — and used to be
      invisible here, leaving the process serving numbers derived from a date
      that is no longer on disk. Fixing `run_pull`'s restamp removes the way
      the *product* reaches that state; it does not remove the state (a
      restored backup, a hand-edit, a later story rewriting a manifest), and
      the failure is silent by construction. Digested by content rather than
      by mtime: `as_of` moving from one ISO date to another leaves the file
      exactly the same size, so size+mtime would rest entirely on the clock's
      resolution.

    Empty string for everything that is not a cache-backed ref (synthetic
    pulls, `ws1-real`): those are read-only fixtures nothing appends to, and
    probing them per request would put I/O back on the hot path for no gain.
    """
    try:
        if not _looks_like_cache_key(_safe_ref(pull_ref)):
            return ""
        parts = [f"manifest:{manifest_fingerprint(pull_ref)}"]
        for path in raw_response_paths(pull_ref):
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
        return "|".join(parts)
    except (PullNotFoundError, OSError):
        # An unreadable/absent entry versions as "nothing"; `_shaped_pull`
        # below is what turns that into the right typed error.
        return ""


@lru_cache(maxsize=4)
def _shaped_pull(
    root: Path, pull_ref: str, config: Config, evidence_version: str
) -> ShapedPull:
    # F3-S1 ordering gotcha (PM-relayed): the sanitizer runs FIRST, before any
    # special-case branch does a lookup. The WS-1 branch below used to run
    # ahead of `_safe_ref` because "ws1-real" is a fixed constant that can
    # never be a traversal string — that was safe only because it never
    # varied. Real cache keys are attacker-influenced input (a `pull_ref`
    # arrives from a request body), so the F3-S1 branch below must never be
    # reachable with an unsanitized ref, and neither must WS-1's now that
    # they share this function.
    safe_ref = _safe_ref(pull_ref)

    if safe_ref == WS1_REAL_PULL_REF:
        return _load_ws1_real_pull(config)

    if _looks_like_cache_key(safe_ref):
        try:
            return _load_cache_backed_pull(safe_ref, config)
        except CacheMissError as exc:
            raise PullNotFoundError(f"no pull found for ref {pull_ref!r}") from exc

    path = root / f"{safe_ref}.json"
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


#: `rentcomp.storage.cache.cache_key` only ever emits lowercase sha256 hex
#: digests (64 chars). A synthetic-pulls ref is a hyphenated word
#: (`"ws1-real"`, fixture filenames like `"synthetic-basic"`) and never
#: happens to be 64 hex characters, so this is a safe, cheap way to tell
#: "this ref is a real F3-S1 cache key" from "this ref is a synthetic-pulls
#: filename" without probing the filesystem twice. Deliberately not pinned
#: by QA's contract (its own docstring: "I do not pin *how* the loader
#: tells... only the resulting behaviour") — free to change later.
_CACHE_KEY_ALPHABET = frozenset("0123456789abcdef")


def _looks_like_cache_key(ref: str) -> bool:
    return len(ref) == 64 and _CACHE_KEY_ALPHABET.issuperset(ref)


def _load_cache_backed_pull(key: str, config: Config) -> ShapedPull:
    """F3-S1 + F4-S3's real cache, generalizing `_load_ws1_real_pull`'s
    shape: read whatever raw responses were filed under `key`
    (`storage.cache.write_raw_response`), shape them through the same
    `pipeline.shape.shape_raw_pull` chain, and return a `ShapedPull` whose
    `as_of` comes from the cache manifest — never from the caller or the
    wall clock (PM-relayed AC 3).

    Raises `CacheMissError` (translated to `PullNotFoundError` by the
    caller) if the manifest or every raw response is absent — a cache
    entry with a manifest but no bytes yet is exactly as "not there yet" as
    no entry at all.
    """
    manifest = read_manifest(key)  # raises CacheMissError if absent

    paths = raw_response_paths(key)
    if not paths:
        raise CacheMissError(f"cache entry {key!r} has a manifest but no raw responses")

    active: list[dict] = []
    inactive: list[dict] = []
    raw_blobs: list[bytes] = []
    for path in paths:
        records = read_raw_records(path)
        if records is None:
            # Present is not the same as usable: a response truncated by a
            # crash, emptied, or in a shape this parser does not recognise is
            # not evidence, and shaping must not choke on it or invent records
            # out of it. `pull_status` reports the same file as an open window,
            # so the two answers agree about what this pull holds (F3-S4).
            continue
        raw_blobs.append(path.read_bytes())
        # "inactive" contains "active" as a substring, so it must be checked
        # first — a sig like "y2025-inactive-off000" must not be misfiled.
        bucket = inactive if "inactive" in path.stem else active
        bucket.extend(records)

    comps = shape_raw_pull(active, inactive, config, manifest.as_of, *manifest.window)
    digest = sha256(b"".join(raw_blobs)).hexdigest()
    return ShapedPull(ref=key, as_of=manifest.as_of, digest=digest, comps=comps)


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
