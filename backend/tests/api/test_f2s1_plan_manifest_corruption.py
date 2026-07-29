"""F2-S3 QA VERIFY (Layer 2) — the preview route against a corrupt manifest.

QA-authored *after* the developer's diff, on the PM's request: the preview
route shipped with **zero** corruption-shape coverage, where the two comparable
routes have six (`test_f4s9_search_route_verify.py`) and nine
(F1-S2). Nothing here is a new acceptance criterion — this file exists to
convert two claims the route makes about itself into regressions.

WHAT THIS ROUTE PROMISES ABOUT A BROKEN CACHE FILE
---------------------------------------------------
`api/plan.py::_cache_status`'s own docstring:

    A *corrupt* entry deliberately reads as `miss` here rather than raising:
    the preview's job is to keep a live-updating form honest, and taking the
    whole form down over an unreadable cache file would be a worse answer than
    "this looks like it needs a pull".

That is the right design call, and it is the one this file pins. It is also, as
shipped, **not what the code does for every shape** — `except (OSError,
ValueError, KeyError)` does not catch the `AttributeError` and `TypeError` that
`storage/cache.py::read_manifest` raises when the manifest's *types* are wrong
rather than its values. Those shapes are marked `xfail` below (non-strict, so
the follow-up fix turns them green without a test edit) rather than asserted
against today's behaviour — pinning a 500 that the module's own docstring
disowns would cement the defect instead of recording it.

WHY IT MATTERS MORE THAN THE EXCEPTION LIST SUGGESTS
------------------------------------------------------
This route backs a **debounced, live-typing form** (F2 step 3). A shape that
escapes `_cache_status` is therefore not one 500 — it is one 500 per keystroke,
for as long as the broken file is on disk, with the preview line dark the whole
time. The user's route out is F2 step 4: submit anyway, at which point
`POST /api/search` refuses loudly (F4-S9) and still spends nothing.

MONEY — THE INVARIANT THAT MUST HOLD FOR *EVERY* SHAPE, BROKEN OR NOT
-----------------------------------------------------------------------
`test_no_shape_of_corrupt_manifest_lets_the_preview_spend` is the load-bearing
test in this file and it is asserted unconditionally, for all nine shapes,
including the ones whose status code is xfailed. F2-S3's [INVARIANT] is about
the *count*; the structural guarantee behind it is that the preview cannot buy
anything however its inputs or its disk are shaped. Proven structurally (the
raw responses on disk are byte-identical afterwards and no new cache entry
appeared) rather than off the ledger, because in fixture mode the ledger never
moves and a ledger assertion would pass against a route that re-pulled
everything — the same reasoning F4-S9's verify file gives.

ZERO LIVE CALLS (D17, WORKFLOW.md §6): `RENTCOMP_LIVE` explicitly unset,
fixtures in a temp directory, `RENTCOMP_HOME` a temp directory.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger

PLAN_PATH = "/api/search/plan"
SEARCH_PATH = "/api/search"
TODAY = date.today()

#: A full-calendar-year window, fetchable whatever today is — the date-
#: independence device the F2-S1 and F4-S9 suites both use.
SEARCH_BODY: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start": "01-01",
    "window_end": "12-31",
}

PLAN_ARGS: dict[str, Any] = {
    "address": SEARCH_BODY["address"],
    "radius": SEARCH_BODY["radius"],
    "bedrooms": SEARCH_BODY["bedrooms"],
    "bathrooms": SEARCH_BODY["bathrooms"],
    "property_types": SEARCH_BODY["property_types"],
    "years_back": SEARCH_BODY["years_back"],
    "window_start_mmdd": SEARCH_BODY["window_start"],
    "window_end_mmdd": SEARCH_BODY["window_end"],
}


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------
#
# The first six are F4-S9's enumeration, kept byte-identical so the two routes
# are answering the same question. The last three are the ones `read_manifest`
# is specifically exposed to and F4-S9 did not reach: a manifest whose keys are
# all present but whose *types* are wrong, which is what a schema change across
# two versions of this tool actually looks like on disk.

CORRUPTIONS: dict[str, str] = {
    # --- value-shaped breakage: currently handled ---------------------------
    "unparseable": "{ not json",
    "truncated to nothing": "",
    "no as_of": json.dumps({"window": ["01-01", "12-31"]}),
    "as_of is not a date": json.dumps({"as_of": "someday", "window": ["01-01", "12-31"]}),
    # --- type-shaped breakage: escapes `_cache_status` today ----------------
    # `payload["as_of"]` on a non-mapping -> TypeError
    "json but not an object": "null",
    "json is a list": "[]",
    # `_query_status("nope")` -> AttributeError ('str' has no attribute 'get')
    "queries are not records": json.dumps(
        {"as_of": "2026-07-27", "window": ["01-01", "12-31"], "queries": ["nope"]}
    ),
    # `tuple(5)` -> TypeError
    "window is not a sequence": json.dumps({"as_of": "2026-07-27", "window": 5}),
    # `int([1])` -> TypeError. Deliberately a NON-EMPTY list: `planned: []` is
    # falsy, so `int(payload.get("planned") or 0)` quietly yields 0 and the
    # entry reads as a complete `hit` — handled, but worth knowing.
    # `planned: "abc"` would be a *caught* ValueError.
    "planned is not a number": json.dumps(
        {"as_of": "2026-07-27", "window": ["01-01", "12-31"], "planned": [1]}
    ),
}

#: Shapes whose 500 is the defect this file reports rather than pins. Non-strict
#: `xfail`: when the follow-up widens `_cache_status`'s `except` clause these
#: turn XPASS (green run, visible in the summary) instead of demanding an edit
#: in the same commit as the fix.
UNHANDLED_TODAY = {
    "json but not an object",
    "json is a list",
    "queries are not records",
    "window is not a sequence",
    "planned is not a number",
}


def _record(n: int) -> dict[str, Any]:
    return {
        "id": f"f2s1-corrupt-{n}",
        "formattedAddress": f"{900 + n} S Corrupt Ave, Chicago, IL 60609",
        "addressLine1": f"{900 + n} S Corrupt Ave",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{(TODAY - timedelta(days=150 + n)).isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


@pytest.fixture
def seeded(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode with a saved response per fetchable window."""
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed() -> int:
        queries = fetchable_queries(plan_pull_queries(TODAY, **PLAN_ARGS))
        for index, query in enumerate(queries):
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(
                json.dumps([_record(index)]).encode("utf-8")
            )
        clear_caches()
        return len(queries)

    seed.dir = fixtures
    return seed


@pytest.fixture
def http(app, rentcomp_home):
    """`raise_server_exceptions=False` — an unhandled exception must surface as
    the 500 a browser would see, not as a pytest traceback. Without this the
    tests below would report an error rather than a status code, and the
    distinction between "handled badly" and "not handled" would be invisible."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def corrupted(http, seeded, rentcomp_home, clear_caches):
    """Run a real pull, then break its manifest in the requested way.

    Returns the cache entry directory and a snapshot of the raw responses taken
    *after* corruption, so the money assertion compares like with like.
    """

    def _corrupt(shape: str):
        seeded()
        first = http.post(SEARCH_PATH, json=SEARCH_BODY)
        assert first.status_code == 200, first.text[:400]
        ref = first.json()["pull_ref"]

        entry = rentcomp_home / "cache" / ref
        raw_before = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
        assert raw_before, (
            "the seeding search filed no raw responses, so the money assertion in this "
            "file would prove nothing"
        )

        (entry / "manifest.json").write_text(CORRUPTIONS[shape], encoding="utf-8")
        # Remove the saved fixtures too: a route that went back to the source
        # would now find nothing and could not quietly rewrite identical bytes.
        for saved in seeded.dir.glob("*.json"):
            saved.unlink()
        clear_caches()
        return ref, entry, raw_before

    return _corrupt


# ===========================================================================
# 1. MONEY — unconditional, every shape, including the xfailed ones
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_previewing_over_a_corrupt_manifest_spends_nothing(corrupted, http, shape) -> None:
    """However the manifest breaks, the preview buys nothing and changes nothing.

    This is the assertion that must hold whatever is decided about the status
    code, so it deliberately does **not** inspect the response: a shape that
    500s still has its money guarantee asserted here. `api/plan.py`'s central
    claim is structural — the module does not import `run_pull`, a client, the
    ledger or `httpx` — and this is that claim exercised from outside, through
    the one input (the disk) that a caller can actually corrupt.
    """
    ref, entry, raw_before = corrupted(shape)
    ledger_before = load_ledger().calls_this_month
    cache_root = entry.parent
    entries_before = sorted(p.name for p in cache_root.iterdir())

    http.post(PLAN_PATH, json=SEARCH_BODY)

    raw_after = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
    assert raw_after == raw_before, (
        f"[{shape}] previewing over a corrupt manifest touched the raw responses under "
        f"{ref}. The preview fires on every keystroke; it may never write."
    )
    assert load_ledger().calls_this_month == ledger_before, (
        f"[{shape}] the preview moved the call ledger"
    )
    assert sorted(p.name for p in cache_root.iterdir()) == entries_before, (
        f"[{shape}] the preview created or removed a cache entry"
    )


# ===========================================================================
# 2. the docstring's promise: a broken cache file does not take the form down
# ===========================================================================


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(
            shape,
            marks=pytest.mark.xfail(
                reason=(
                    "api/plan.py::_cache_status catches (OSError, ValueError, KeyError) "
                    "but read_manifest raises AttributeError/TypeError for type-shaped "
                    "corruption. Non-strict: turns XPASS when the follow-up widens the "
                    "clause. Same gap as api/search.py:92."
                ),
                strict=False,
            ),
        )
        if shape in UNHANDLED_TODAY
        else shape
        for shape in sorted(CORRUPTIONS)
    ],
)
def test_a_corrupt_manifest_previews_as_a_miss_instead_of_500ing_the_form(
    corrupted, http, shape
) -> None:
    """`_cache_status`'s own docstring, as an executable assertion.

    "A *corrupt* entry deliberately reads as `miss` here rather than raising...
    taking the whole form down over an unreadable cache file would be a worse
    answer than 'this looks like it needs a pull'."

    A 500 here is one 500 per keystroke behind a debounced preview, and it also
    costs the user F2 step 4: the form cannot route to F3's cache modal when it
    could not learn whether a cache exists.
    """
    corrupted(shape)
    response = http.post(PLAN_PATH, json=SEARCH_BODY)

    assert response.status_code == 200, (
        f"[{shape}] POST {PLAN_PATH} returned {response.status_code} over a corrupt "
        f"manifest. api/plan.py:122-131 promises this reads as a cache MISS rather than "
        f"raising."
    )
    assert response.json()["cache_status"] == "miss", (
        f"[{shape}] expected cache_status 'miss' for an unreadable entry, got "
        f"{response.json().get('cache_status')!r}"
    )


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_the_estimate_survives_a_corrupt_manifest_when_the_status_does(
    corrupted, http, shape
) -> None:
    """Whatever `cache_status` resolves to, the *count* must still be the
    planner's — F2-S3's [INVARIANT] does not have an exception for a bad disk.

    Skipped-by-assertion rather than xfailed: for the shapes that 500 there is
    no body to check, and asserting on one would be asserting on the defect.
    """
    corrupted(shape)
    response = http.post(PLAN_PATH, json=SEARCH_BODY)
    if response.status_code != 200:
        return

    expected = {
        len(fetchable_queries(plan_pull_queries(when, **PLAN_ARGS)))
        for when in (TODAY, TODAY + timedelta(days=1))
    }
    assert response.json()["estimated_calls"] in expected, (
        f"[{shape}] estimated_calls={response.json()['estimated_calls']} but the planner "
        f"says {sorted(expected)}. The call count is a fact about the plan and the "
        "calendar; a corrupt cache file must not change it."
    )
