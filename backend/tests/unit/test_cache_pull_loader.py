"""F3-S1 [BE] — generalizing `storage/pulls.py`'s `PullLoader` seam onto the
real cache. QA-authored, written RED before any implementation exists
(AGENT_QA.md protocol).

WHAT THIS FILE OWNS
--------------------
`storage/pulls.py`'s own module docstring names this story explicitly:

    "F3-S1 + F4-S3 the real cache (`~/.rentcomp/cache/<key>/`) — every pull,
     general-purpose, resolved by content-hashed cache key"

and its `_load_ws1_real_pull` is called out in the F3-S1 dispatch as "a
clean worked example of the real loader this story generalizes" — same
returned shape (`ShapedPull`), same `PullNotFoundError` on absence, same
"digest the raw bytes" pattern. This file holds the generalized version to
that shape, plus the one ordering gotcha the dispatch flags explicitly:

    "the WS-1 special-case branch currently runs *before* `_safe_ref`'s
     path-sanitizer. Once refs are real hex digests (this story's job), the
     sanitizer must run first."

WHY LAYER 1
-----------
Every assertion here calls `load_shaped_pull(pull_ref, config)` (already a
plain importable function, `storage/pulls.py`) with plain values, no HTTP, no
browser — same reasoning as the rest of that module's existing (implicit)
coverage and `test_live_call_guard.py`'s structural guards.

CONTRACT PINNED HERE (QA-PROPOSED, on top of the already-locked
`storage/pulls.py` contract — PM confirms before it counts):

  A `pull_ref` that is a real F3-S1 cache key (i.e. what
  `rentcomp.storage.cache.cache_key(...)` produces) resolves through the real
  cache instead of `fixtures/synthetic/pulls/`: `load_shaped_pull` reads the
  raw responses `rentcomp.storage.cache.write_raw_response` filed under that
  key, shapes them via the same `pipeline.shape.shape_raw_pull` chain
  `_load_ws1_real_pull` already uses, and returns a `ShapedPull` whose
  `as_of`/digest come from the cache manifest / raw bytes — never from
  anything the caller supplies directly.

  I do not pin *how* the loader tells "this ref is a cache key" from "this
  ref is a synthetic-pulls filename" (a length/alphabet check, a directory
  probe, a prefix) — only the resulting behaviour. The one test that is
  layer-agnostic to that choice is the ordering test below, which needs no
  real cache entry to exist at all.

EXPECTED RED STATE TODAY: `test_cache_module_and_loader_are_importable`
FAILS naming what's missing (`rentcomp.storage.cache` — F3-S1 red); the
ordering test is written against `storage/pulls.py` alone, which already
exists, so it runs for real today and is expected to FAIL until the
generalized cache branch is added ahead of (not instead of) the sanitizer.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rentcomp.storage.config import Config
from rentcomp.storage.pulls import PullNotFoundError, load_shaped_pull

try:
    from rentcomp.storage.cache import cache_key, write_manifest, write_raw_response

    _CACHE_IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F3-S1 lands
    _CACHE_IMPORT_ERROR = _exc

needs_cache = pytest.mark.skipif(
    _CACHE_IMPORT_ERROR is not None,
    reason=f"rentcomp.storage.cache is not importable yet (F3-S1 red): {_CACHE_IMPORT_ERROR}",
)


def test_cache_module_and_loader_are_importable() -> None:
    assert _CACHE_IMPORT_ERROR is None, (
        "Cannot import rentcomp.storage.cache — the F3-S1 cache-backed pull loader "
        f"needs it before it can generalize storage/pulls.py: {_CACHE_IMPORT_ERROR}"
    )


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    load_shaped_pull.cache_clear()
    yield root
    load_shaped_pull.cache_clear()


ACTIVE_RECORD = {
    "id": "listing-cache-1",
    "formattedAddress": "1 Cache Test St, Chicago, IL 60609",
    "price": 2100,
    "listedDate": "2026-02-01T00:00:00.000Z",
    "status": "Active",
    "squareFootage": 900,
    "bedrooms": 2,
}
INACTIVE_RECORD = {
    "id": "listing-cache-2",
    "formattedAddress": "2 Cache Test St, Chicago, IL 60609",
    "price": 1950,
    "listedDate": "2025-11-01T00:00:00.000Z",
    "removedDate": "2025-12-15T00:00:00.000Z",
    "status": "Inactive",
    "squareFootage": 850,
    "bedrooms": 2,
}


@needs_cache
def test_a_real_cache_key_resolves_through_the_cache_not_synthetic_pulls(home: Path) -> None:
    """The generalized loader shape: same `ShapedPull` contract
    `_load_ws1_real_pull` already returns, sourced from real cache entries
    instead of the two hardcoded T-S3 fixtures."""
    import json

    key = cache_key({"address": "1 Cache Test St, Chicago, IL 60609", "years_back": 1})
    write_raw_response(key, "y2026-active-off000", json.dumps([ACTIVE_RECORD]).encode(), meta={"status": "Active"})
    write_raw_response(key, "y2025-inactive-off000", json.dumps([INACTIVE_RECORD]).encode(), meta={"status": "Inactive"})
    write_manifest(key, as_of=date(2026, 7, 27), window=("01-01", "12-31"))

    pull = load_shaped_pull(key, Config())
    assert pull.ref == key
    assert pull.as_of == date(2026, 7, 27)
    assert len(pull.comps) >= 1
    assert pull.digest, "digest must be populated from the raw cache bytes, like every other ref"


@needs_cache
def test_as_of_comes_from_the_manifest_never_from_the_caller_or_wall_clock(home: Path) -> None:
    """PM-relayed AC (3): as_of is a function of the ref's own manifest. Two
    cache entries pulled "on different days" (different `as_of`) must
    resolve to their OWN as_of, not today's real date and not each other's.

    Fixture note (QA self-fix, F3-S1 verify pass): the record used here must
    have been "listed" before the manifest's own as_of, or `_build_comp`'s
    `effective_dom = as_of - listed` goes negative and `StitchedComp`'s
    `effective_dom >= 0` constraint raises before this test's actual
    assertion ever runs. `ACTIVE_RECORD` is listed 2026-02-01, so this test
    uses its own copy listed well before the manifest's as_of (2020-01-01)
    instead — the point (as_of comes from the manifest, not wall clock or
    the caller) is unchanged; as_of=2020-01-01 is unambiguously not today's
    real date either way.
    """
    import json

    as_of_only_record = {**ACTIVE_RECORD, "id": "listing-cache-as-of", "listedDate": "2019-06-01T00:00:00.000Z"}

    key = cache_key({"address": "3 Cache Test St, Chicago, IL 60609", "years_back": 1})
    write_raw_response(
        key, "y2026-active-off000", json.dumps([as_of_only_record]).encode(), meta={"status": "Active"}
    )
    write_raw_response(key, "y2025-inactive-off000", json.dumps([]).encode(), meta={"status": "Inactive"})
    write_manifest(key, as_of=date(2020, 1, 1), window=("01-01", "12-31"))

    pull = load_shaped_pull(key, Config())
    assert pull.as_of == date(2020, 1, 1), (
        "as_of must come from the cache manifest tied to this ref, not from date.today() or any "
        "other ambient source"
    )


@needs_cache
def test_a_missing_cache_key_raises_pull_not_found_not_a_bare_filesystem_error(home: Path) -> None:
    """Same failure contract as every other ref (module docstring,
    `_load_ws1_real_pull`'s own comment): F1's "corrupt/missing cache entry"
    edge is a 404, never an unhandled 500."""
    fake_key = "a" * 64
    with pytest.raises(PullNotFoundError):
        load_shaped_pull(fake_key, Config())


# ---------------------------------------------------------------------------
# ordering gotcha — sanitize BEFORE any special-case branch does a lookup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "malicious_ref",
    [
        "../../../../etc/passwd",
        "..%2F..%2Fetc%2Fpasswd",
        "../secret",
        "..",
        "a/../../secret",
    ],
)
def test_a_path_traversal_shaped_ref_is_rejected_before_any_file_lookup(
    home: Path, malicious_ref: str, tmp_path
) -> None:
    """PM-relayed ordering gotcha, encoded as a test rather than left to a
    comment: `storage/pulls.py`'s WS-1 special case currently runs BEFORE
    `_safe_ref`'s sanitizer. Once cache-backed refs are real (this story),
    any new special-case branch added ahead of the sanitizer would let a
    traversal-shaped ref reach a file lookup before it is ever rejected.

    Proven two ways: (1) the call must raise `PullNotFoundError`, never a
    raw `FileNotFoundError`/`IsADirectoryError`/`PermissionError` escaping
    from an unsanitized `open()`; (2) a secret planted just outside the
    fixture/cache roots is never read (its content never surfaces in any
    exception message, which is the only place a naive implementation could
    leak it).
    """
    secret = tmp_path / "outside-everything" / "secret.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("do-not-leak-me", encoding="utf-8")

    with pytest.raises(PullNotFoundError) as excinfo:
        load_shaped_pull(malicious_ref, Config())
    assert "do-not-leak-me" not in str(excinfo.value)


def test_a_ref_with_a_null_byte_is_rejected_before_any_file_lookup(home: Path) -> None:
    """The same guard `_safe_ref`'s docstring already calls out by name: a
    null byte reaching `open()` surfaces as an OS-level 500, not a 404."""
    with pytest.raises(PullNotFoundError):
        load_shaped_pull("evil\x00ref", Config())
