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
        manifest.json          # {"as_of": "...", "window": ["01-01","12-31"]}
        raw/<sig>.json          # immutable raw response bytes, one per call
        raw/<sig>.meta.json      # the caller's `meta` for that call, informational

Atomic writes throughout (`.tmp` -> fsync -> rename), same durability
reasoning as `storage/ledger.py::save_ledger` — a crash mid-write must never
leave a cache entry that looks satisfied but is corrupt.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from rentcomp.storage import rentcomp_home

__all__ = [
    "CacheMissError",
    "Manifest",
    "cache_key",
    "raw_response_paths",
    "read_manifest",
    "write_manifest",
    "write_raw_response",
]


class CacheMissError(LookupError):
    """No cache entry exists at that key.

    Mirrors `storage.pulls.PullNotFoundError` — a stale reference to a
    missing cache entry is an ordinary condition (F1's "corrupt/missing
    cache entry" edge), never an unhandled `FileNotFoundError` reaching the
    API layer as a 500.
    """


@dataclass(frozen=True)
class Manifest:
    """The only place `as_of` and the search window live for a cache key."""

    as_of: date
    window: tuple[str, str]


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
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


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


def write_manifest(key: str, *, as_of: date, window: tuple[str, str]) -> Path:
    """The ONLY place `as_of`/window live for `key` (PM-relayed AC 3)."""
    entry = _entry_dir(key)
    payload = {"as_of": as_of.isoformat(), "window": list(window)}
    path = entry / "manifest.json"
    body = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
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
    )


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
