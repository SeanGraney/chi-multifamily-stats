"""F3-S1 [BE] Cache key + raw-response store (ARCHITECTURE.md §5/§5a, D24).

**AC (story text, verbatim):** identical params re-hash identically across
sessions; changing any single param produces a different key; a pipeline
version bump does not invalidate the cache.

Two PM-relayed additional ACs this module owns:

    (2) the writer refuses to file a zero-byte response — a 204 is a 2xx
        with body `b""`, and filing it would let a later loader mistake "no
        response arrived" for "the pull came back empty" (that distinction
        stays real: `b"[]"` — a genuine "zero comps" answer — is written and
        kept, see `test_an_empty_json_array_is_not_a_zero_byte_response`).
    (3) `as_of`/window live ONLY in the manifest for a given key — never
        re-derivable from the caller or the wall clock, so a later loader
        can never accidentally serve a memo shaped under a stale window.

WHY THE KEY IS SEARCH-PARAMS ONLY (no `Config`, no pipeline version)
----------------------------------------------------------------------
This is the whole point of "a pipeline version bump does not invalidate the
cache" (ARCHITECTURE.md: "pipeline changes re-run free on cached data"). This
module stores RAW API responses, never pipeline output — `storage/pulls.py`'s
cache-backed loader is what runs the (versioned) shaping/pipeline code over
these bytes, on every read, for free. Compare `storage/pulls.py::config_digest`
+ its `_shaped_pull` memo key, which DOES include `Config` — that memo is
allowed to go stale on a knob change (F0-S5's "a knob change re-derives like
any other input"); this cache must never be invalidated by one.

ADDRESS NORMALIZATION — the story's own [DEFAULT], PM ruling: implement it
-----------------------------------------------------------------------------
The story text's [DEFAULT] names "normalized address casing/whitespace"
explicitly. Not literally required by the three AC sentences (case-sensitive
hashing alone satisfies "identical params re-hash identically"), but the
entire point of this story is protecting the 50-call/month budget (D24) — a
user retyping "Main St" as "MAIN ST" silently missing the cache defeats that
purpose. Normalization here is narrow and deliberate: only the `address`
field, lowercased and whitespace-collapsed. Every other field is hashed
as-is — a `bedrooms` of `"3:4"` vs `"3 : 4"` is a genuinely different query
shape this module has no business guessing about.

ON-DISK LAYOUT (this story's own [DEFAULT] — round-trip behaviour is what's
pinned, not the layout; ARCHITECTURE.md §5 sketches the same shape)
-----------------------------------------------------------------------------
    <RENTCOMP_HOME>/cache/<key>/
        manifest.json          # as_of + window (F3-S1), plus §5a's record of
                               # which planned queries are satisfied (F4-S9)
        raw/<sig>.json          # immutable raw response bytes, one per call
        raw/<sig>.meta.json      # the caller's `meta` for that call, informational

Atomic writes throughout (`.tmp` -> fsync -> rename), same durability
reasoning as `storage/ledger.py::save_ledger` — a crash mid-write must never
leave a cache entry that looks satisfied but is corrupt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from rentcomp.storage import rentcomp_home

__all__ = [
    "CORRUPT_MANIFEST_ERRORS",
    "CacheMissError",
    "Manifest",
    "QueryStatus",
    "cache_key",
    "manifest_fingerprint",
    "raw_response_paths",
    "read_manifest",
    "read_raw_meta",
    "read_raw_records",
    "write_manifest",
    "write_raw_response",
]

#: What `read_manifest` raises when an entry EXISTS but cannot be read back as a
#: manifest — as opposed to `CacheMissError`, which means there is no entry.
#:
#: Defined here, beside the function that raises them, and imported by every
#: caller rather than re-spelled at each `except`. Two routes need this list
#: (`api/search.py` must refuse a corrupt entry loudly; `api/plan.py` must
#: degrade to `miss` quietly) and they were widened out of step once already:
#: both shipped `(OSError, ValueError, KeyError)`, which covers breakage in the
#: manifest's *values* and misses breakage in its *types* —
#: `payload["as_of"]` on a non-mapping and `tuple(5)` raise `TypeError`, and a
#: `queries` list of non-records reaches `_query_status` where `.get` raises
#: `AttributeError`. Both surfaced as a 500. One name, so the next shape that
#: needs adding is added once and both routes get it.
CORRUPT_MANIFEST_ERRORS = (OSError, ValueError, KeyError, AttributeError, TypeError)


class CacheMissError(LookupError):
    """No cache entry exists at that key.

    Mirrors `storage.pulls.PullNotFoundError` — a stale reference to a
    missing cache entry is an ordinary condition (F1's "corrupt/missing
    cache entry" edge), never an unhandled `FileNotFoundError` reaching the
    API layer as a 500.
    """


@dataclass(frozen=True)
class QueryStatus:
    """One planned query's fate, as the manifest remembers it (F4-S9).

    `label` is the user-facing name of the window ("2025 Inactive") — the
    string F4-S6 renders when it says what is missing, stored rather than
    recomputed so that reading a gap back off disk never needs the plan (and
    therefore never needs the clock) that produced it.
    """

    label: str
    sig: str
    satisfied: bool
    error: str | None = None
    #: Would another RentCast call deliver something this window does not
    #: already hold? `None` means "not recorded", and falls back to the rule
    #: F4-S9 shipped: a window is owed exactly when it is not satisfied. Every
    #: manifest written before F3-S4 reads back that way, so widening this
    #: field cannot change what an existing entry costs to finish.
    #:
    #: The state that needs the third answer is a **2xx we cannot parse**: the
    #: call was made, the money is gone, the bytes are filed, and re-buying it
    #: returns the same bytes (D24). That window is neither satisfied (its
    #: evidence is unusable, so `missing` must still name it) nor owed (a call
    #: would buy nothing) — and `calls_to_complete` is what F3-S2's modal asks
    #: the user to consent to, so it may only ever count calls a resume would
    #: actually make.
    owed: bool | None = None

    @property
    def is_owed(self) -> bool:
        """Whether completing this pull should send a call for this window."""
        return (not self.satisfied) if self.owed is None else self.owed

    def as_json(self) -> dict:
        return {
            "label": self.label,
            "sig": self.sig,
            "satisfied": self.satisfied,
            "error": self.error,
            "owed": self.is_owed,
        }


@dataclass(frozen=True)
class Manifest:
    """The only place `as_of` and the search window live for a cache key.

    ARCHITECTURE.md §5a also makes this the pull's manifest proper: "planned
    queries, which are satisfied, which failed and why". F3-S1 wrote the first
    half (`as_of`/`window`); F4-S9 adds the second, because a gap that lives
    only in an in-memory `PullOutcome` is gone the moment the process ends and
    a user who reopens the workspace tomorrow must still be told what is
    absent — without paying a call to find out.

    `queries` covers the FETCHABLE queries only. A structurally-empty window
    (F4-S1 AC4) is planned and deliberately never sent, so it is not a gap:
    counting it here would show the user a permanent "incomplete" warning for
    evidence that cannot exist.
    """

    as_of: date
    window: tuple[str, str]
    #: Total queries the plan emitted, including structurally-empty ones.
    planned: int = 0
    #: The subset worth spending a call on (`planner.fetchable_queries`).
    fetchable: int = 0
    queries: tuple[QueryStatus, ...] = ()

    @property
    def missing(self) -> tuple[str, ...]:
        """Labels of the windows that never arrived. Empty when whole."""
        return tuple(q.label for q in self.queries if not q.satisfied)

    @property
    def calls_to_complete(self) -> int:
        """One call per window a resume would actually send — the F2-S3 unit,
        so the number shown and the number spent cannot drift.

        Not `len(self.missing)`: a window holding a 2xx this parser cannot use
        is missing (its evidence is unusable) and is *not* owed (the bytes are
        already paid for and a second call returns the same ones). Quoting a
        call there would take money the resume then refuses to spend.
        """
        return sum(1 for q in self.queries if q.is_owed)

    @property
    def complete(self) -> bool:
        """A manifest with no query records describes a pull that is whole by
        construction (every F3-S1-era entry, and every synthetic fixture)."""
        return not self.missing


#: A cache key (and any per-call `sig`) must never itself need sanitizing
#: before use as a path component — `cache_key` only ever emits hex sha256
#: digests, which fit comfortably, but `sig`/`key` are still validated here
#: because they reach this module as plain caller-supplied strings.
_SAFE_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


def _safe_component(value: str, *, what: str) -> str:
    if not value or value.startswith(".") or not _SAFE_ALPHABET.issuperset(value):
        raise ValueError(f"unsafe {what}: {value!r}")
    return value


def _normalize(params: Mapping) -> dict:
    """Canonicalize search params before hashing: sorted keys (via
    `json.dumps(sort_keys=True)` in `cache_key`) plus `address`
    casing/whitespace normalization (the story's [DEFAULT] — see module
    docstring)."""
    canon: dict = {}
    for k, v in params.items():
        if k == "address" and isinstance(v, str):
            v = " ".join(v.split()).lower()
        canon[k] = v
    return canon


def cache_key(params: Mapping) -> str:
    """Deterministic, collision-resistant key for a set of search params.

    Search params ONLY — see module docstring for why nothing else may ever
    enter this signature.
    """
    canon = _normalize(params)
    payload = json.dumps(canon, sort_keys=True, default=str)
    return sha256(payload.encode("utf-8")).hexdigest()


def _entry_dir(key: str) -> Path:
    safe_key = _safe_component(key, what="cache key")
    return rentcomp_home() / "cache" / safe_key


def _atomic_write_bytes(path: Path, body: bytes) -> None:
    """`write .tmp -> fsync -> rename` (ARCHITECTURE.md §5a) — rename is
    atomic on POSIX, so a crash mid-write can never leave a file that looks
    satisfied but is corrupt."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        _clear_directory_obstruction(path)
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def _clear_directory_obstruction(path: Path) -> None:
    """Remove an EMPTY directory standing where a response file belongs.

    Nothing this module writes is ever a directory, so one at a response path
    is debris — a half-finished sync, a restored backup, a file manager. Left
    in place it makes `os.replace` raise (`PermissionError` on Windows,
    `IsADirectoryError` on POSIX) and the paid-for bytes are lost, which is
    the one outcome D24 exists to prevent.

    Deliberately `rmdir`, not `rmtree`: an empty directory cannot be anyone's
    data, and a non-empty one is something this code has no business deleting
    — it raises here and the caller's `except OSError` handles it as a failed
    write.
    """
    if path.is_dir():
        os.rmdir(path)


def write_raw_response(key: str, sig: str, raw: bytes, *, meta: Mapping) -> Path | None:
    """Write-through, D24: raw bytes exactly as they arrived, filed under
    `key`/`sig`. Never re-serializes, never validates/parses first.

    Refuses a zero-byte body (`b""` — the same hazard as a 204 or a redirect
    page) and returns `None` without writing anything. A genuinely empty
    RESULT (`b"[]"`) is a real, paid-for answer and is written normally.
    """
    if not raw:
        return None
    safe_sig = _safe_component(sig, what="response signature")
    entry = _entry_dir(key)
    raw_path = entry / "raw" / f"{safe_sig}.json"
    _atomic_write_bytes(raw_path, raw)

    meta_path = entry / "raw" / f"{safe_sig}.meta.json"
    meta_body = json.dumps(dict(meta), sort_keys=True, default=str).encode("utf-8")
    _atomic_write_bytes(meta_path, meta_body)

    return raw_path


def write_manifest(
    key: str,
    *,
    as_of: date,
    window: tuple[str, str],
    planned: int = 0,
    fetchable: int = 0,
    queries: Iterable[QueryStatus] = (),
) -> Path:
    """The ONLY place `as_of`/window live for `key` (PM-relayed AC 3), and
    §5a's record of which planned queries are satisfied (F4-S9).

    `planned`/`fetchable`/`queries` default to "nothing recorded", so a caller
    that only owns the F3-S1 half writes exactly the file it always did.
    """
    entry = _entry_dir(key)
    payload = {
        "as_of": as_of.isoformat(),
        "window": list(window),
        "planned": planned,
        "fetchable": fetchable,
        "queries": [q.as_json() for q in queries],
    }
    path = entry / "manifest.json"
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write_bytes(path, body)
    return path


def read_manifest(key: str) -> Manifest:
    """Raises `CacheMissError` for an absent (or unsafe) key — never a bare
    `FileNotFoundError`."""
    try:
        entry = _entry_dir(key)
    except ValueError as exc:
        raise CacheMissError(f"no cache entry for key {key!r}") from exc

    path = entry / "manifest.json"
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        raise CacheMissError(f"no cache entry for key {key!r}") from exc

    payload = json.loads(raw)
    return Manifest(
        as_of=date.fromisoformat(payload["as_of"]),
        window=tuple(payload["window"]),
        planned=int(payload.get("planned") or 0),
        fetchable=int(payload.get("fetchable") or 0),
        queries=tuple(_query_status(entry) for entry in payload.get("queries") or ()),
    )


def _query_status(payload: Mapping) -> QueryStatus:
    owed = payload.get("owed")
    return QueryStatus(
        label=str(payload.get("label") or ""),
        sig=str(payload.get("sig") or ""),
        satisfied=bool(payload.get("satisfied")),
        error=payload.get("error") if isinstance(payload.get("error"), str) else None,
        # Absent in every manifest written before F3-S4 — `None` there means
        # "fall back to `not satisfied`", which is exactly what those entries
        # meant when they were written.
        owed=bool(owed) if isinstance(owed, bool) else None,
    )


def manifest_fingerprint(key: str) -> str:
    """A digest of the manifest bytes at `key`, or `""` when there is none.

    The memo in `storage/pulls.py` keys on the evidence a shaped pull was
    built from, and `as_of`/`window` — both read from this file — are inputs
    to `shape_raw_pull` every bit as much as the raw responses are. Content
    rather than `(size, mtime_ns)`, unlike the raw files: a manifest rewrite
    that only changes `as_of` from one ISO date to another leaves the size
    identical, so size+mtime would rest entirely on the clock's resolution.
    One small file read per derive, against re-shaping the whole pull.
    """
    try:
        body = (_entry_dir(key) / "manifest.json").read_bytes()
    except (OSError, ValueError):
        return ""
    return sha256(body).hexdigest()[:16]


def read_raw_records(path: Path) -> list[dict] | None:
    """The listing records inside one stored raw response, or `None`.

    `None` means "this file is not usable evidence" — it is missing,
    truncated, empty, a directory, or a 2xx body in a shape this parser does
    not recognise. That is a different fact from "the response was an empty
    market" (`b"[]"` → `[]`), and the two must never collapse into each other:
    one is a gap, the other is an answer.

    Mirrors `client.rentcast._extract_records`' notion of what a response is
    (a bare array, or `{"listings": [...]}`) but returns rather than raises,
    because every caller here is asking a question about disk state rather
    than handling a live response.
    """
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if isinstance(payload, dict):
        payload = payload.get("listings")
    if not isinstance(payload, list):
        return None
    return [record for record in payload if isinstance(record, dict)]


def read_raw_meta(raw_path: Path) -> dict:
    """The `meta` sidecar filed beside a raw response — `{}` if unreadable.

    The sidecar is the only durable record of *when* a given response
    arrived, which is what lets a pull whose manifest was lost rebuild
    `as_of` from the evidence instead of stamping the day of the repair on
    comps nobody re-observed.
    """
    try:
        payload = json.loads(raw_path.with_name(f"{raw_path.stem}.meta.json").read_bytes())
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def raw_response_paths(key: str) -> list[Path]:
    """All raw response files filed under `key`, sorted for determinism.

    Used by `storage/pulls.py`'s cache-backed loader. Excludes the
    `.meta.json` sidecar files written alongside each response.
    """
    try:
        entry = _entry_dir(key)
    except ValueError:
        return []
    raw_dir = entry / "raw"
    if not raw_dir.is_dir():
        return []
    return sorted(p for p in raw_dir.glob("*.json") if not p.name.endswith(".meta.json"))
