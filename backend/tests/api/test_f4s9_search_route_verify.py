"""F4-S9 QA VERIFY (Layer 2) — the two self-review fixes, at the route.

QA-authored *after* the developer's diff. `test_f4s9_search_route.py` (written
red, before any code) owns AC3 and one representative each of AC4/AC5/AC6;
nothing here is a new AC. Both facts below were caught by the developer's own
self-review and both are fixed — this file is what makes them regressions
rather than anecdotes, and it widens each one past the single case the
developer's supporting file covers.

1. **A corrupt cache entry must never be re-bought.** `run_pull` treats an
   unreadable manifest as "nothing is known to be satisfied" and re-fetches,
   which on the live path is the whole pull bought a second time to work
   around a broken file. The route refuses instead. The developer's
   `test_a_corrupt_cache_entry_is_surfaced_rather_than_re_bought` covers
   invalid JSON; a manifest can be broken in more ways than one, and the money
   invariant has to hold for every one of them.

   NOTE ON WHAT IS ASSERTED: the load-bearing claim is **nothing was
   fetched**, asserted structurally (the raw responses on disk are untouched
   and no new cache entry appeared) rather than off the ledger — in fixture
   mode the ledger never moves, so a ledger assertion here would pass against
   a route that re-pulled everything. Two of the shapes below currently escape
   as an unhandled exception rather than as the 500 that names the entry; that
   is reported to the PM as a note and is deliberately NOT asserted here,
   because it is a message-quality gap, not a money one, and pinning today's
   behaviour would cement it.

2. **A plan with nothing fetchable is a legitimate search, not a crash.**
   Every window in the future (F4-S1 AC4) used to raise `CacheMissError` out
   of the pull. `test_a_plan_with_nothing_fetchable_costs_nothing_and_still_
   exists` covers it at Layer 1; this asserts it at the surface a user can
   actually reach it from, because the developer's own description of the bug
   was "reachable straight from the F2 form via a New-Year-spanning window" —
   and that is a route fact, not a function fact.

MONEY: fixture mode throughout, `RENTCOMP_LIVE` explicitly unset, fixtures in
a temp directory. Nothing here can spend a call.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import Ledger, current_month, save_ledger

SEARCH_PATH = "/api/search"
TODAY = date.today()

SEARCH_BODY: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start": "01-01",
    "window_end": "12-31",
}

PLAN_ARGS = {
    "address": SEARCH_BODY["address"],
    "radius": SEARCH_BODY["radius"],
    "bedrooms": SEARCH_BODY["bedrooms"],
    "bathrooms": SEARCH_BODY["bathrooms"],
    "property_types": SEARCH_BODY["property_types"],
    "years_back": SEARCH_BODY["years_back"],
    "window_start_mmdd": SEARCH_BODY["window_start"],
    "window_end_mmdd": SEARCH_BODY["window_end"],
}

#: Every way a `manifest.json` can be broken that `read_manifest` reaches:
#: unparseable, parseable but missing the one field F3-S1 guarantees,
#: parseable with a field that is the right name and the wrong kind, a
#: structurally valid file whose `queries` are not query records, a body that
#: is JSON but not an object, and a file truncated to nothing.
CORRUPTIONS = {
    "unparseable": "{ not json",
    "no as_of": json.dumps({"window": ["01-01", "12-31"]}),
    "as_of is not a date": json.dumps({"as_of": "someday", "window": ["01-01", "12-31"]}),
    "queries are not records": json.dumps(
        {"as_of": "2026-07-27", "window": ["01-01", "12-31"], "queries": ["nope"]}
    ),
    "json but not an object": "null",
    "truncated to nothing": "",
}


def _record(n: int) -> dict:
    return {
        "id": f"f4s9-verify-route-{n}",
        "formattedAddress": f"{800 + n} N Verify Blvd, Chicago, IL 60609",
        "addressLine1": f"{800 + n} N Verify Blvd",
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
    """Fixture mode with a saved response per fetchable window of `plan_args`."""
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed(*, plan_args: dict | None = None):
        queries = fetchable_queries(plan_pull_queries(TODAY, **(plan_args or PLAN_ARGS)))
        for index, query in enumerate(queries):
            body = json.dumps([_record(index)]).encode("utf-8")
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)
        clear_caches()
        return queries

    seed.dir = fixtures
    return seed


def _a_wholly_unfetchable_window() -> tuple[str, str]:
    """A one-cohort-year window that has not opened yet, whatever today is.

    Asked of the real planner rather than hardcoded: a New-Year-spanning window
    is wholly unfetchable for most of the year but not while it is open, and a
    test that only exercised the degenerate plan for eleven months of twelve
    would be a false green in January. Every wrapping window in the calendar is
    tried before giving up.
    """
    for month in range(1, 13):
        start = f"{month:02d}-15"
        end = f"{(month % 12) + 1:02d}-14"
        plan = plan_pull_queries(
            TODAY, **{**PLAN_ARGS, "years_back": 1,
                      "window_start_mmdd": start, "window_end_mmdd": end}
        )
        if plan and not fetchable_queries(plan):
            return start, end
    raise AssertionError(
        f"no window in the calendar is wholly unfetchable on {TODAY} — F4-S1 AC4's "
        "degenerate plan is unreachable, which would itself be a change in the planner"
    )


@pytest.fixture
def search(app, rentcomp_home):
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)

    def _search(**overrides: Any):
        return client.post(SEARCH_PATH, json={**SEARCH_BODY, **overrides})

    return _search


# ===========================================================================
# 1. a corrupt cache entry is never re-bought — every shape
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_no_shape_of_corrupt_manifest_causes_a_re_pull(
    search, seeded, rentcomp_home, shape
) -> None:
    """The money invariant, across every way the manifest can break.

    On the live path each of these would be the entire pull bought a second
    time — six of fifty monthly calls — to work around a file the user could
    have deleted for free. Nothing may go back to the source until a human has
    said so.

    Proven structurally rather than off the ledger: the saved responses are
    deleted before the second request, so a route that re-pulled would
    overwrite or add raw files (and would report every window missing) instead
    of leaving the entry exactly as it found it. That assertion works
    identically in fixture mode and on the live path, which a ledger check
    does not.
    """
    seeded()
    ref = search().json()["pull_ref"]
    entry = rentcomp_home / "cache" / ref
    raw_before = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
    assert raw_before, "the first search filed no raw responses, so this test proves nothing"

    (entry / "manifest.json").write_text(CORRUPTIONS[shape], encoding="utf-8")
    for saved in seeded.dir.glob("*.json"):
        saved.unlink()

    response = search()

    assert response.status_code >= 400, (
        f"a {shape} manifest returned {response.status_code}. An entry that exists but "
        "cannot be read is not an empty cache, and treating it as one spends real calls "
        "to work around a corrupt file (D24)."
    )
    raw_after = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
    assert raw_after == raw_before, (
        f"a {shape} manifest sent the route back to the source: the raw responses under "
        f"{ref} changed. Nothing may be re-fetched to recover from a broken manifest — "
        "the bytes it describes are still on disk and were already paid for."
    )
    assert sorted(p.name for p in (rentcomp_home / "cache").iterdir()) == [ref], (
        "a second cache entry appeared, so the corrupt entry was worked around by "
        "starting a fresh pull somewhere else"
    )


def test_a_readable_entry_is_still_served_after_a_corrupt_one_is_repaired(
    search, seeded, rentcomp_home
) -> None:
    """The complement: refusing must be a response to the corruption, not a
    permanent state.

    Without this, "always 500 on a second search" satisfies every case above
    while making the cache useless — and a user who deleted the broken entry,
    as the error tells them to, would still be stuck.
    """
    seeded()
    first = search().json()
    entry = rentcomp_home / "cache" / first["pull_ref"]
    (entry / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert search().status_code >= 400

    import shutil

    shutil.rmtree(entry)
    again = search()

    assert again.status_code == 200, (
        f"after the corrupt entry was deleted the search still fails ({again.status_code}): "
        f"{again.text[:300]}"
    )
    assert again.json()["pull_ref"] == first["pull_ref"]
    assert again.json()["complete"] is True


# ===========================================================================
# 2. a plan with nothing fetchable, from the surface it is reachable from
# ===========================================================================


def test_a_search_whose_every_window_is_in_the_future_is_a_200_not_a_crash(
    search, seeded
) -> None:
    """The degenerate plan, at the route.

    A New-Year-spanning window entered on a date before it opens is an
    ordinary thing to type into the F2 form, and every cohort year's window is
    then structurally empty (F4-S1 AC4 — `daysOld` with a negative maximum
    cannot match a record whatever the market holds). It used to raise
    `CacheMissError` out of a legitimate search.

    "Planned, cost nothing, found nothing" and "no such search" are different
    facts, and the response has to carry the first: `estimated_calls` of 0 is
    what F2-S3's estimator shows the user, and it is the honest number.
    """
    seeded()
    window_start, window_end = _a_wholly_unfetchable_window()

    response = search(
        years_back=1, window_start=window_start, window_end=window_end
    )

    assert response.status_code == 200, (
        f"a search whose every window lies in the future returned {response.status_code}: "
        f"{response.text[:400]}. Nothing can have been listed in the future, so this costs "
        "zero calls — but it is still a search the user made and the system must be able "
        "to talk about."
    )
    payload = response.json()
    assert payload["estimated_calls"] == 0, (
        f"estimated_calls={payload['estimated_calls']} for a plan with no fetchable "
        "window. F2-S3's estimator shows this number before the user submits, and "
        "quoting a price for calls that will never be sent is quoting a wrong price."
    )
    assert payload["calls_spent"] == 0
    assert payload["complete"] is True, (
        "a window that is planned-and-never-sent is not a gap in the evidence — reporting "
        "it as missing would show a permanent 'incomplete' warning for records that "
        "cannot exist"
    )
    assert payload["missing"] == []
    assert payload["calls_to_complete"] == 0
    assert payload["pull_ref"], "a search that found nothing still addresses a real pull"
