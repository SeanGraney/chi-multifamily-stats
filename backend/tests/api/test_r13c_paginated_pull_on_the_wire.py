"""Queue row 13c (Layer 2) — the one claim of this row a user can reach.

WHY THIS FILE IS SMALL, AND WHY THERE IS NO PLAYWRIGHT SPEC
-----------------------------------------------------------
Almost all of row 13c is about which offset went on the wire and which page's
bytes are on disk. Neither is observable over HTTP, so the row's arithmetic
lives at Layer 1 (`backend/tests/unit/f3s4/test_r13c_page_level_pull.py`), and
the row completes no user flow — F4-S6 owns rendering a partial pull and F3-S2
owns the modal. A browser spec asserting a comp count here would be a logic
assertion in a browser costume (AGENT_QA.md, "smells").

What IS route-visible is the lie itself, one level down from where F3-S4 caught
it: **`POST /api/search` answers `complete` from the manifest's per-QUERY flags,
and a query that spans pages is flagged satisfied when any one of its pages
parses.** So the route can report "cached, nothing missing, 0 calls to complete"
while `POST /api/derive` over the same ref computes every number from a comp set
that is two records short — and there is nothing in either response that says
so. That is a Layer-2 claim in its plainest form: given this disk state, these
two endpoints may not disagree.

`test_f3s4_search_evidence_honesty.py` asserts the same consistency for a whole
window's response going missing. It cannot reach this one: fixture mode does not
paginate (one saved response is one page, by design — a second page would be a
different signature the gate never bought), so no route-driven pull in that file
can produce a window that spans calls.

HOW A PAGINATED ENTRY IS BUILT WITHOUT A NETWORK, AND WITHOUT PRETENDING
-------------------------------------------------------------------------
The entry is built by `run_pull` through an `httpx.MockTransport` — the real
writer, the real manifest, the real page-splitting client — and only then are
the routes asked about it. Hand-writing page files would have encoded F3-S4's
[DEFAULT] file layout into a Layer-2 test, which is exactly the coupling that
makes a suite expensive to change.

Once the entry exists, `RENTCOMP_LIVE` is unset and `RENTCOMP_FIXTURES_DIR`
points at an EMPTY directory, so the route has no way to quietly fetch its way
out of the question: a search that decided to re-pull cannot succeed, it can
only fail loudly with `FixtureMissingError`. That is the structural form of "it
spent nothing" — never a `calls_this_month == 0` read, which in fixture mode
passes whether the code is right or not (one of this project's four known
vacuous assertions).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import httpx
import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.pull import run_pull
from rentcomp.storage.cache import raw_response_paths
from rentcomp.storage.ledger import Ledger, current_month, save_ledger

SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"

#: `date.today()` rather than a frozen date: the plan's cohort years are a
#: function of it, and the route supplies its own clock, so the two must agree
#: about which windows exist. The row's subject (pagination) is independent of
#: which day it is.
TODAY = date.today()

#: A full-calendar-year window, fetchable whatever today is — the
#: date-independence device the F2-S1/F4-S9/F1-S2/F3-S4 suites all use.
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

#: The spanning window's page size and total — three pages, so a MIDDLE page can
#: be lost. See the Layer-1 module for why three rather than two.
PAGE_SIZE = 2
SPANNING_TOTAL = 6
FAKE_KEY = "qa-r13c-fake-key-not-a-real-credential"


def _record(n: int) -> dict[str, Any]:
    """One record, at an address no other record shares — so the count of comps
    the derive returns is a faithful meter of how much evidence survived."""
    return {
        "id": f"r13c-route-{n}",
        "formattedAddress": f"{700 + n} E Evidence St, Chicago, IL 60609",
        "addressLine1": f"{700 + n} E Evidence St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{(TODAY - timedelta(days=150 + 20 * n)).isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


@pytest.fixture
def paginated_entry(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """A real cache entry whose first window spans three calls.

    Returns `(ref, comps_when_whole)`. Afterwards the process is in fixture mode
    with an empty fixtures directory — see the module docstring.
    """
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    queries = fetchable_queries(plan_pull_queries(TODAY, **PLAN_ARGS))
    windows = {
        (query.params.get("status"), query.params.get("daysOld")): index
        for index, query in enumerate(queries)
    }

    def handle(request: httpx.Request) -> httpx.Response:
        window = windows[
            (request.url.params.get("status"), request.url.params.get("daysOld"))
        ]
        if window != 0:
            return httpx.Response(200, json=[_record(100 + window)])
        offset = int(request.url.params.get("offset") or 0)
        records = [_record(n) for n in range(1, SPANNING_TOTAL + 1)]
        return httpx.Response(
            200,
            json=records[offset : offset + PAGE_SIZE],
            headers={"X-Total-Count": str(SPANNING_TOTAL)},
        )

    outcome = run_pull(
        **PLAN_ARGS, today=TODAY, transport=httpx.MockTransport(handle)
    )
    assert outcome.complete is True, f"the seeded pull did not complete: {outcome.missing}"
    expected_files = (SPANNING_TOTAL // PAGE_SIZE) + (len(queries) - 1)
    assert len(raw_response_paths(outcome.pull_ref)) == expected_files, (
        f"the seeded pull filed {len(raw_response_paths(outcome.pull_ref))} responses, not "
        f"{expected_files} — the first window did not paginate, so this file's subject is "
        "not present"
    )

    # From here on: no live mode, and an empty fixtures directory, so a route
    # that tried to re-fetch could only fail loudly.
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    clear_caches()
    return outcome.pull_ref, SPANNING_TOTAL + (len(queries) - 1)


@pytest.fixture
def http(app, rentcomp_home):
    """`raise_server_exceptions=False` so an unhandled exception surfaces as the
    500 a browser would see, rather than as a pytest traceback."""
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def _curation(pull_ref: str) -> dict[str, Any]:
    return {
        "pull_ref": pull_ref,
        "subject": {
            "address": "3651 S Wood St Unit 2",
            "lat": 41.82,
            "lng": -87.67,
            "sqft": 1000.0,
            "beds": 3.0,
            "baths": 1.0,
        },
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 0.0,
        "candidate_rent": None,
    }


def _page_holding(ref: str, record_id: str):
    """The stored response carrying `record_id`.

    Content-addressed rather than by filename: `y2026-active-off002` is F3-S4's
    logged [DEFAULT] and a Layer-2 test has no business depending on it.
    """
    for path in raw_response_paths(ref):
        try:
            payload = json.loads(path.read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            continue
        records = payload.get("listings") if isinstance(payload, dict) else payload
        if isinstance(records, list) and any(
            isinstance(item, dict) and item.get("id") == record_id for item in records
        ):
            return path
    raise AssertionError(f"no stored page under {ref} holds {record_id}")


@pytest.mark.parametrize(
    "lost_page",
    ["r13c-route-1", "r13c-route-3", "r13c-route-6"],
    ids=["first page", "middle page", "last page"],
)
def test_a_search_over_a_paginated_entry_cannot_claim_more_than_the_derive_can_show(
    http, paginated_entry, clear_caches, lost_page
) -> None:
    """The honesty invariant, as a cross-endpoint consistency check.

    `POST /api/search` answers `complete`/`missing`/`calls_to_complete` off the
    manifest; `POST /api/derive` answers off the bytes. A page of a spanning
    window is removed, and the two are asked the same question.

    The assertion is deliberately not "which answer is right" — an honestly
    incomplete pull is a perfectly good answer (§5a), and so is re-fetching the
    page. What is not available is `complete: true, missing: [], calls_to_
    complete: 0` over a derive that is two records short: that is the user being
    told the evidence is whole while every number on the screen — anchor,
    premiums, bucket DOM medians, the KM curve — is computed from less of it than
    they paid for, with nothing on screen to say so (NORTH_STAR; §5a).

    RED as at dispatch, all three parametrisations: satisfaction is per query and
    a signature counts as held when any page under it parses.
    """
    ref, whole = paginated_entry
    baseline = http.post(DERIVE_PATH, json=_curation(ref))
    assert baseline.status_code == 200, baseline.text[:400]
    assert len(baseline.json()["comps"]) == whole, (
        f"the undamaged entry derives {len(baseline.json()['comps'])} comps, not {whole} — "
        "the fixture's records do not shape one comp each, so a comp count cannot be this "
        "test's evidence meter"
    )

    _page_holding(ref, lost_page).unlink()
    clear_caches()

    searched = http.post(SEARCH_PATH, json=SEARCH_BODY)
    assert searched.status_code == 200, searched.text[:400]
    reported = searched.json()

    if reported["complete"] is not True:
        # Honestly incomplete: the gap must be nameable and priced, or the user
        # cannot act on it — F4-S6 renders both verbatim.
        assert reported["missing"], "a pull reported incomplete must name what is absent"
        assert reported["calls_to_complete"] >= 1, (
            "the page is gone, so a call really does deliver evidence this pull does not "
            "hold. Priced at 0 there is no way back to it at all."
        )
        return

    derived = http.post(DERIVE_PATH, json=_curation(ref))
    assert derived.status_code == 200, (
        f"the search says this pull is complete but a derive over it returns "
        f"{derived.status_code}: {derived.text[:300]}"
    )
    assert len(derived.json()["comps"]) == whole, (
        f"the search reports complete with nothing missing and 0 calls to finish, and the "
        f"derive over the same ref sees {len(derived.json()['comps'])} of {whole} records. "
        "One page of one window is gone and the pull cannot tell — a paginated window is "
        "reporting itself whole over evidence it lost, which is the defect this row exists "
        "for. Bucket DOM medians computed over this set are wrong in the way F10-S1's "
        "[INVARIANT] forbids, and nothing downstream could notice."
    )


def test_a_search_over_a_page_damaged_entry_does_not_re_pull_behind_the_users_back(
    http, paginated_entry, rentcomp_home, clear_caches
) -> None:
    """Whatever the route decides to SAY about a page-damaged entry, it may not
    buy its way out of the question without being asked.

    F3's success condition is "no API call ever happens without the user having
    chosen it", and `api/search.py` puts a stale entry deliberately on the
    no-fetch side: completing a partial pull costs real calls, so it is the
    user's decision (F3-S2's modal, `force_refresh`), never a side effect of
    reopening a search.

    Structural, not a ledger read: the fixtures directory is empty, so a re-pull
    could only show up as pages changing or a second cache entry appearing.
    """
    ref, _whole = paginated_entry
    _page_holding(ref, "r13c-route-3").unlink()
    surviving = {
        path.name: path.read_bytes() for path in raw_response_paths(ref)
    }
    assert surviving, "the damage left no pages, so this test proves nothing"
    clear_caches()

    http.post(SEARCH_PATH, json=SEARCH_BODY)

    after = {path.name: path.read_bytes() for path in raw_response_paths(ref)}
    for name, blob in surviving.items():
        assert after.get(name) == blob, (
            f"the page {name} was rewritten or removed by a search over a page-damaged "
            "entry. Bytes that survived are paid for and are never touched to tidy up an "
            "inconsistency (D24)."
        )
    assert sorted(p.name for p in (rentcomp_home / "cache").iterdir()) == [ref], (
        "a second cache entry appeared, so the damaged entry was worked around by starting "
        "a fresh pull somewhere else"
    )
