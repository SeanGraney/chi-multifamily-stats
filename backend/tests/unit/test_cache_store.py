"""F3-S1 [BE] Cache key + raw-response store — QA-authored, written RED before
any implementation exists (AGENT_QA.md protocol).

THE AC THIS FILE OWNS (story text, verbatim)
---------------------------------------------
    "identical params re-hash identically across sessions; changing any
     single param produces a different key; pipeline version bump does not
     invalidate cache."

Plus the PM-relayed additional ACs folded into the F3-S1 dispatch:
    (2) the cache writer refuses to file a zero-byte response (a 204 is a 2xx
        with body b"" — the same hazard as caching a redirect page).
    (3) the memo/cache key's `as_of` and search-window are functions of the
        manifest/ref itself, never separately suppliable by a caller — so a
        later loader can never derive them from something else and silently
        serve a memo shaped under a stale window.

WHY LAYER 1 (AGENT_QA.md decision procedure)
---------------------------------------------
Step 1: every assertion here imports a function and calls it with plain
values (bytes, dicts, Path). Nothing needs HTTP or a browser — same
reasoning as `test_live_call_guard.py`'s structural guards and
`storage/pulls.py`'s existing `config_digest`/`_safe_ref` unit coverage.

CONTRACT PINNED HERE (QA-PROPOSED — the story text names no module; the PM
confirms before it counts as locked, exactly the pattern
`test_rentcast_client_surface.py` uses for F0-S4):

    rentcomp.storage.cache
        cache_key(params: Mapping) -> str
            Deterministic, collision-resistant digest of *search params
            only* — never `Config`, never a pipeline/code version, never
            wall-clock time. [DEFAULT: sha256 hex of canonicalized JSON,
            sorted keys] — any scheme satisfying the AC below is acceptable
            per the story's [DEFAULT] tag; what's pinned here is the
            OUTCOME (three tests below), not the technique.
        write_raw_response(key: str, sig: str, raw: bytes, *, meta: Mapping)
            -> Path | None
            Write-through, D24: raw bytes exactly as they arrived, filed
            under the cache key. Returns None (refuses to write) for a
            zero-byte body — never files something a later loader could
            mistake for a satisfied pull.
        write_manifest(key: str, *, as_of: date, window: tuple[str, str])
            -> Path
        read_manifest(key: str) -> object with `.as_of: date`,
            `.window: tuple[str, str]`
            The manifest is the ONLY place `as_of`/window live for a given
            cache key — AC (3) above.
        CacheMissError(LookupError)
            Raised by `read_manifest` for an absent key (mirrors
            `storage/pulls.py`'s `PullNotFoundError` — a stale reference to a
            missing entry is an ordinary condition, not a crash).

I don't pin `write_raw_response`'s on-disk layout (single file vs.
`raw/<sig>.json`, `meta.json` vs. a manifest object) — that's the story's own
[DEFAULT] to choose; only round-trip behaviour is asserted.

EXPECTED RED STATE TODAY: `test_cache_store_module_exists` FAILS with the
ImportError detail naming what's missing; everything else SKIPS.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

try:
    from rentcomp.storage.cache import (
        CacheMissError,
        cache_key,
        read_manifest,
        write_manifest,
        write_raw_response,
    )

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F3-S1 lands
    _IMPORT_ERROR = _exc

needs_cache = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.storage.cache is not importable yet (F3-S1 red): {_IMPORT_ERROR}",
)


def test_cache_store_module_exists() -> None:
    """Legible red: one failure that names exactly what is missing."""
    assert _IMPORT_ERROR is None, (
        "Cannot import rentcomp.storage.cache (QA-proposed path — see this "
        f"file's docstring): {_IMPORT_ERROR}"
    )


#: A realistic search-params dict — the shape F2's search form assembles and
#: F4-S1's query planner would key its pull on. Field NAMES are illustrative;
#: what's under test is the hashing behaviour, not this exact schema.
BASE_PARAMS = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "propertyType": "Multi-Family|Apartment|Townhouse|Single Family|Condo",
    "years_back": 3,
}


# ---------------------------------------------------------------------------
# AC 1 — identical params re-hash identically across sessions
# ---------------------------------------------------------------------------


@needs_cache
def test_identical_params_produce_identical_keys() -> None:
    a = cache_key(dict(BASE_PARAMS))
    b = cache_key(dict(BASE_PARAMS))
    assert a == b


@needs_cache
def test_identical_params_hash_identically_across_a_fresh_process_call() -> None:
    """"Across sessions" — re-derived from a brand-new dict literal each time
    (no shared mutable state, no object identity, nothing the interpreter's
    dict/hash randomization could vary run to run)."""
    first = cache_key(
        {
            "address": "3651 S Wood St, Chicago, IL 60609",
            "radius": 2.0,
            "bedrooms": "3:4",
            "bathrooms": None,
            "propertyType": "Multi-Family|Apartment|Townhouse|Single Family|Condo",
            "years_back": 3,
        }
    )
    second = cache_key(json.loads(json.dumps(BASE_PARAMS)))
    assert first == second


@needs_cache
def test_key_does_not_depend_on_dict_insertion_order() -> None:
    """Canonicalization is part of "identical params" — a dict built with the
    same key/value pairs in a different order is still the same search."""
    reordered = dict(reversed(list(BASE_PARAMS.items())))
    assert cache_key(reordered) == cache_key(dict(BASE_PARAMS))


@needs_cache
def test_the_key_is_a_string_safe_for_a_directory_name() -> None:
    """`storage/pulls.py`'s existing `_safe_ref` alphabet (this module's own
    doc: "F3-S1's cache keys are hex digests ... fit comfortably") — the key
    must never itself need sanitizing before it can be used as a path
    component."""
    key = cache_key(dict(BASE_PARAMS))
    assert key and all(c.isalnum() or c in "-_." for c in key)


# ---------------------------------------------------------------------------
# AC 2 — changing any single param produces a different key
# ---------------------------------------------------------------------------


@needs_cache
@pytest.mark.parametrize(
    "field,new_value",
    [
        ("address", "3651 S Wood St, Chicago, IL 60609 Unit 2"),
        ("radius", 2.5),
        ("bedrooms", "2:4"),
        ("bathrooms", "1.5:2"),
        ("propertyType", "Single Family"),
        ("years_back", 4),
    ],
)
def test_changing_any_single_param_changes_the_key(field: str, new_value) -> None:
    baseline = cache_key(dict(BASE_PARAMS))
    changed = dict(BASE_PARAMS)
    changed[field] = new_value
    assert cache_key(changed) != baseline, (
        f"changing {field!r} alone did not change the cache key — a collision "
        "here would silently serve a different search's cache"
    )


@needs_cache
def test_an_added_or_removed_field_changes_the_key() -> None:
    baseline = cache_key(dict(BASE_PARAMS))
    added = dict(BASE_PARAMS)
    added["max_distance_mi"] = 1.0
    assert cache_key(added) != baseline

    removed = dict(BASE_PARAMS)
    del removed["years_back"]
    assert cache_key(removed) != baseline


# ---------------------------------------------------------------------------
# AC 3 — a pipeline version bump does not invalidate the cache
# ---------------------------------------------------------------------------


@needs_cache
def test_cache_key_takes_only_search_params_no_config_or_version_argument() -> None:
    """Structural guard against the most direct way to violate the AC: if
    `cache_key` accepted a `Config`/pipeline-version argument at all, a
    version bump could change the key by construction. Search params are the
    entire signature (ARCHITECTURE.md: "Cache key = SHA256 of canonicalized
    search params" — deliberately NOT `(params, config)` the way
    `storage/pulls.py`'s pull-loader memo key is)."""
    import inspect

    signature = inspect.signature(cache_key)
    # exactly one required positional/keyword parameter (the params mapping)
    required = [
        p
        for p in signature.parameters.values()
        if p.default is inspect._empty
        and p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    assert len(required) == 1, (
        f"cache_key{signature} takes {len(required)} required parameters — expected exactly "
        "one (the search-params mapping). A second required parameter is a way for a "
        "non-search input (config, pipeline version) to enter the key."
    )


@needs_cache
def test_a_pipeline_version_bump_does_not_change_the_key(monkeypatch) -> None:
    """Direct behavioural proof, not just a signature check: bump the real
    `PIPELINE_VERSION` constant this build ships and confirm the same search
    params still hash to the same key."""
    import rentcomp.pipeline.derive as derive_module

    before = cache_key(dict(BASE_PARAMS))
    monkeypatch.setattr(derive_module, "PIPELINE_VERSION", "99.0.0-a-completely-different-build")
    after = cache_key(dict(BASE_PARAMS))
    assert before == after, (
        "the cache key changed when PIPELINE_VERSION changed — a code release would silently "
        "evict every cached pull, defeating F3-S1's entire purpose (pipeline changes re-run "
        "free on cached data)"
    )


# ---------------------------------------------------------------------------
# raw-response store — write raw bytes, not pipeline output (D24 / NORTH_STAR)
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    return root


@needs_cache
def test_raw_bytes_round_trip_unmodified(home: Path) -> None:
    """The literal AC: "store RAW API responses, not pipeline output". A
    round trip through the writer must return byte-identical content — no
    re-serialization, no key reordering, no whitespace normalization (the
    same property `test_rentcast_client.py::
    test_fixture_responses_are_byte_identical_to_the_saved_originals`
    protects on the read side)."""
    key = cache_key({"address": "1 Main St", "years_back": 1})
    raw = b'[{"id": "listing-1", "price": 1999,   "status":  "Active"}]'
    path = write_raw_response(key, "y2025-active-off000", raw, meta={"status": 200})
    assert path is not None
    assert Path(path).read_bytes() == raw


@needs_cache
def test_raw_write_never_parses_or_validates_the_body(home: Path) -> None:
    """D24: "Write raw bytes before validating." Even a body that is not
    valid JSON at all must still be written — a parse failure downstream must
    never cost the already-spent call."""
    key = cache_key({"address": "1 Main St", "years_back": 1})
    garbage = b"not json at all {{{"
    path = write_raw_response(key, "y2025-active-off000", garbage, meta={"status": 200})
    assert path is not None
    assert Path(path).read_bytes() == garbage


@needs_cache
def test_a_zero_byte_response_is_refused(home: Path) -> None:
    """PM-relayed AC (2): a 204 is a 2xx with body b"" — filing it would let a
    later loader find *something* at a valid cache signature and treat the
    pull as satisfied on nothing, the same hazard a redirect page would be.
    "Is this response worth filing" is this story's decision, not the
    client's."""
    key = cache_key({"address": "1 Main St", "years_back": 1})
    before = set(home.rglob("*"))
    result = write_raw_response(key, "y2025-active-off000", b"", meta={"status": 204})
    assert result is None, "a zero-byte response must not be reported as written"
    after = set(home.rglob("*"))
    new_files = {p for p in after - before if p.is_file()}
    assert not new_files, f"a zero-byte response left a file on disk: {new_files}"


@needs_cache
def test_an_empty_json_array_is_not_a_zero_byte_response(home: Path) -> None:
    """The refusal is about an EMPTY BODY, not an empty result. `b"[]"` is a
    real, paid-for "zero comps here" answer (exactly like six of the gate's
    eight committed fixtures) and must be written and kept."""
    key = cache_key({"address": "1 Main St", "years_back": 1})
    path = write_raw_response(key, "y2025-active-off000", b"[]", meta={"status": 200})
    assert path is not None
    assert Path(path).read_bytes() == b"[]"


# ---------------------------------------------------------------------------
# manifest — as_of and the search window are functions of the ref itself
# ---------------------------------------------------------------------------


@needs_cache
def test_manifest_round_trips_as_of_and_window(home: Path) -> None:
    key = cache_key({"address": "1 Main St", "years_back": 1})
    write_manifest(key, as_of=date(2026, 7, 27), window=("01-01", "12-31"))
    manifest = read_manifest(key)
    assert manifest.as_of == date(2026, 7, 27)
    assert manifest.window == ("01-01", "12-31")


@needs_cache
def test_two_different_cache_entries_keep_independent_as_of_and_window(home: Path) -> None:
    """Nothing global: two different searches pulled on different days with
    different windows must never bleed into each other — each manifest is
    scoped to its own cache key."""
    key_a = cache_key({"address": "1 Main St", "years_back": 1})
    key_b = cache_key({"address": "2 Other Ave", "years_back": 2})
    write_manifest(key_a, as_of=date(2026, 1, 1), window=("01-01", "12-31"))
    write_manifest(key_b, as_of=date(2026, 6, 15), window=("03-01", "09-30"))

    manifest_a = read_manifest(key_a)
    manifest_b = read_manifest(key_b)
    assert manifest_a.as_of == date(2026, 1, 1)
    assert manifest_a.window == ("01-01", "12-31")
    assert manifest_b.as_of == date(2026, 6, 15)
    assert manifest_b.window == ("03-01", "09-30")


@needs_cache
def test_reading_a_manifest_for_an_absent_key_is_a_typed_miss(home: Path) -> None:
    """Mirrors `storage/pulls.py`'s `PullNotFoundError` contract — a stale
    reference to a missing cache entry is an ordinary condition (F1's
    "corrupt/missing cache entry" edge), never an unhandled `FileNotFoundError`
    reaching the API layer as a 500."""
    with pytest.raises(CacheMissError):
        read_manifest("0" * 64)
