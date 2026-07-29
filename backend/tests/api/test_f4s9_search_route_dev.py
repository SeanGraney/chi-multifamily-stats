"""F4-S9 supporting Layer-2 tests — the route decisions QA left open.

Written during implementation (AGENT_DEVELOPER.md step 4). QA's
`test_f4s9_search_route.py` owns AC3 and one representative each of AC4/AC5/
AC6, and deliberately asserted only the uncontroversial half of
ARCHITECTURE.md §4's cache rule, flagging the rest as a PM decision. The PM
ruled; this file pins what the ruling actually produced, so the next reader
finds the behaviour written down as a test rather than as prose in a handoff:

    cache MISS        -> pull (the user saw the cost before submitting: F2
                         step 3's estimator is the same count this route
                         returns)
    cache HIT / STALE -> fetch nothing, report what is on disk so F3-S2's
                         modal can offer the choice
    force_refresh     -> go back to the source regardless, including for
                         windows already satisfied

Plus the two edges nothing else covers: a window the planner refuses is a 422
rather than a 500, and `estimated_calls` counts fetchable queries rather than
planned ones (F2-S3 [INVARIANT] — the number shown and the number spent are
one number).

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
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger

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


def _record(n: int) -> dict:
    return {
        "id": f"f4s9-route-dev-{n}",
        "formattedAddress": f"{500 + n} E Ruling Ave, Chicago, IL 60609",
        "addressLine1": f"{500 + n} E Ruling Ave",
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
    """Fixture mode, pointed at a temp dataset seeded per planned window.

    `seed(n)` saves a response for the first `n` fetchable queries (all of them
    by default) and returns the plan. Fewer than all is how a partial pull is
    produced with no network: a window with no saved response is the same
    "never arrived" condition a 503 is on the live path.
    """
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed(count: int | None = None, *, plan_args: dict | None = None):
        queries = fetchable_queries(plan_pull_queries(TODAY, **(plan_args or PLAN_ARGS)))
        for index, query in enumerate(queries if count is None else queries[:count]):
            body = json.dumps([_record(index)]).encode("utf-8")
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)
        clear_caches()
        return queries

    seed.dir = fixtures
    return seed


@pytest.fixture
def search(app, rentcomp_home):
    from fastapi.testclient import TestClient

    client = TestClient(app)

    def _search(**overrides: Any):
        return client.post(SEARCH_PATH, json={**SEARCH_BODY, **overrides})

    return _search


# ---------------------------------------------------------------------------
# the PM's force_refresh ruling
# ---------------------------------------------------------------------------


def test_a_first_search_reports_a_miss_and_pulls(search, seeded) -> None:
    """The MISS half of the ruling. ARCHITECTURE §4 read literally ("never
    hits the network unless force_refresh") makes a first search impossible,
    since force_refresh is only ever set by a modal that needs a cache entry
    to exist. F2's own flow is the authority: no cache ⇒ run the pull."""
    seeded()
    payload = search().json()

    assert payload["cache_status"] == "miss"
    assert payload["complete"] is True, (
        "a first search did not pull — with no cache entry there is nothing to show, and "
        "the user already consented to the cost at F2 step 3"
    )


def test_a_repeat_search_reports_a_hit_and_spends_nothing(search, seeded) -> None:
    """The HIT half: the modal decides, not the route."""
    seeded()
    search()
    payload = search().json()

    assert payload["cache_status"] == "hit"
    assert payload["calls_spent"] == 0


def test_reopening_a_partial_search_does_not_quietly_finish_it(search, seeded) -> None:
    """The STALE half, and the one worth spelling out.

    Completing a partial pull costs real calls, so it is the user's decision,
    not a side effect of reopening a search. The response says exactly what
    finishing would cost so F3-S2/F4-S6 can offer it.
    """
    seeded(4)
    first = search().json()
    assert first["complete"] is False

    seeded()  # every window now has a saved response — but nothing may go get it
    payload = search().json()

    assert payload["cache_status"] == "stale"
    assert payload["complete"] is False, (
        "reopening a partial search went and completed it without being asked. Every window "
        "it fetches is a call the user never consented to (F3: no API call without a choice)."
    )
    assert payload["calls_to_complete"] == 2


def test_force_refresh_goes_back_to_the_source_for_windows_already_on_disk(
    search, seeded
) -> None:
    """`force_refresh` is the REFRESH click, and it has to mean something.

    Proven by removing one window's saved response first: if the refresh
    re-sent every window, that one now fails and the pull reports a gap. A
    `force_refresh` that skipped satisfied windows would come back complete,
    having quietly done nothing while the user was told it refreshed.
    """
    queries = seeded()
    assert search().json()["complete"] is True

    (seeded.dir / f"{fixture_signature(queries[0].params)}.json").unlink()
    payload = search(force_refresh=True).json()

    assert payload["complete"] is False, (
        "force_refresh did not re-fetch a window that was already satisfied — the REFRESH "
        "button would report success without going back to the source"
    )
    assert payload["missing"] == [f"{queries[0].year} {queries[0].status}"]


# ---------------------------------------------------------------------------
# the cost the user is quoted
# ---------------------------------------------------------------------------


def test_the_estimate_counts_fetchable_queries_not_planned_ones(search, seeded) -> None:
    """F2-S3 [INVARIANT]: one function, two consumers, no drift.

    A structurally-empty window is planned (so the user can see the cohort was
    considered) and never sent, so it must never appear in a price quoted to
    the user either. Asserted against the planner's own count rather than a
    literal, so it holds whatever today's date makes of the wrapping window.
    """
    wrapping = {**PLAN_ARGS, "window_start_mmdd": "12-15", "window_end_mmdd": "01-15"}
    seeded(plan_args=wrapping)

    plan = plan_pull_queries(TODAY, **wrapping)
    payload = search(window_start="12-15", window_end="01-15").json()

    assert payload["estimated_calls"] == len(fetchable_queries(plan))
    assert payload["estimated_calls"] <= len(plan)


def test_a_search_that_spends_nothing_says_so(search, seeded) -> None:
    """Fixture mode serves saved responses for free, and the ledger is the
    record the 50/month cap is enforced on — a search that moved it would be
    charging the owner for a file read."""
    seeded()
    payload = search().json()

    assert payload["calls_spent"] == 0
    assert load_ledger().calls_this_month == 0


# ---------------------------------------------------------------------------
# bad input is the user's problem, named
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [("window_start", "13-01"), ("window_end", "06-31"), ("window_start", "june")],
)
def test_a_window_the_planner_refuses_is_a_422_not_a_500(
    search, seeded, field, value
) -> None:
    """A month-day that is not a date in any year is a typo with a specific
    fix. Quietly clamping it would move a window the user never asked to move
    (F4-S1), and a 500 would put the reason in a log they are not reading."""
    seeded()
    response = search(**{field: value})

    assert response.status_code == 422, (
        f"{field}={value!r} returned {response.status_code}: {response.text[:300]}"
    )


def test_a_trailing_space_in_a_window_is_not_an_error(search, seeded) -> None:
    """F4-S1's QA found the planner rejects `"06-01 "`. The API edge is where a
    user's stray space is trimmed — refusing a search over invisible
    whitespace is not a fix the user can see how to make."""
    seeded()
    assert search(window_start=" 01-01", window_end="12-31 ").status_code == 200


def test_a_corrupt_cache_entry_is_surfaced_rather_than_re_bought(
    search, seeded, rentcomp_home
) -> None:
    """An entry that exists but cannot be read is not an empty cache.

    Treating it as a miss would re-pull — spending real calls to work around a
    corrupt file, which is the one thing D24 exists to prevent. The user is
    told which entry to delete instead, and nothing goes to the source.
    """
    seeded()
    ref = search().json()["pull_ref"]
    (rentcomp_home / "cache" / ref / "manifest.json").write_text("{ not json", encoding="utf-8")

    response = search()

    assert response.status_code == 500
    assert ref in response.text, (
        "the error does not name the cache entry, so the user cannot act on it"
    )
    assert load_ledger().calls_this_month == 0


def test_an_unknown_field_is_rejected_rather_than_ignored(search, seeded) -> None:
    """`extra="forbid"`, for the same reason `DeriveRequest` has it: a typo'd
    field here silently widens or narrows a pull that costs real money."""
    seeded()
    assert search(radius_mi=5.0).status_code == 422
