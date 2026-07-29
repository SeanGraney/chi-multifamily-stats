"""F3-S4 (Layer 2) — the one claim of this story that a user can reach.

WHY THIS FILE IS SMALL, AND WHY THERE IS NO PLAYWRIGHT SPEC
------------------------------------------------------------
Almost all of F3-S4 is about what is on disk and what went on the wire —
neither is observable over HTTP, let alone from a browser, so the story's
arithmetic lives at Layer 1 (`backend/tests/unit/f3s4/`). F3-S4 also completes
no user flow: the flow F3 describes (cache date, USE CACHED vs REFRESH, cancel)
is F3-S2's modal and F3-S3's atomicity, and neither exists yet. Adding a
browser spec here would be a logic assertion in a browser costume — slow and
flaky where it is currently fast and certain (AGENT_QA.md, "smells").

What IS route-visible is a lie the story forbids: **`POST /api/search` reports
`complete: true` off the manifest's flags, and the manifest's flags outlive the
files they describe.** A crash, a half-finished sync, a user tidying
`~/.rentcomp/` — any of them leaves an entry that says it is whole over
evidence that is gone. The user is then told "cached, 0 calls, nothing
missing", loads the workspace, and derives an anchor from three cohorts where
four were paid for, with nothing on screen to say so. That is the honesty
invariant this project is built around (NORTH_STAR: every number traces to real
observed comps), and it is a Layer-2 claim: "given this disk state, the API
returns this".

WHAT IS DELIBERATELY NOT ASSERTED HERE
---------------------------------------
The *status code* the route answers a broken entry with. F4-S9's
`test_f4s9_search_route_verify.py::test_no_shape_of_corrupt_manifest_causes_a_re_pull`
already pins `>= 400` for a corrupt manifest, and this file must not
contradict it. The tests below deliberately damage the **raw responses**, not
the manifest, and assert only self-consistency: whatever the route says it
holds, it must actually hold.

MONEY: fixture mode throughout, `RENTCOMP_LIVE` explicitly unset, saved
responses in a temp directory that is emptied before the second request — so a
route that went back to the source could not quietly succeed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.cache import raw_response_paths
from rentcomp.storage.ledger import Ledger, current_month, save_ledger

SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"
TODAY = date.today()

#: A full-calendar-year window, fetchable whatever today is — the
#: date-independence device the F2-S1, F4-S9 and F1-S2 suites all use.
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


def _record(n: int) -> dict[str, Any]:
    return {
        "id": f"f3s4-route-{n}",
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
def seeded(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode with one saved response per fetchable window."""
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
    """`raise_server_exceptions=False` so an unhandled exception surfaces as
    the 500 a browser would see, rather than as a pytest traceback."""
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


def test_a_search_that_reports_a_pull_complete_can_actually_produce_it(
    http, seeded, rentcomp_home, clear_caches
) -> None:
    """The honesty invariant, as a cross-endpoint consistency check.

    `POST /api/search` answers `complete` off the manifest; `POST /api/derive`
    answers off the bytes. When a crash separates the two, the user is told the
    evidence is whole and shown less of it than they paid for — silently, with
    every downstream number (anchor, buckets, KM curve) computed from a set
    that is short a cohort.

    One raw response is removed and every saved response is deleted, so the
    route cannot quietly re-fetch its way out. The assertion is not "which
    answer is right" — an honestly-incomplete pull is a perfectly good answer,
    and so is re-fetching the lost window — but that the two endpoints cannot
    disagree about what this pull holds.

    RED as at dispatch: `pull_status` reads `satisfied` flags that outlive the
    files they describe, so `complete` stays true over three windows of four.
    """
    windows = seeded()
    first = http.post(SEARCH_PATH, json=SEARCH_BODY)
    assert first.status_code == 200, first.text[:400]
    ref = first.json()["pull_ref"]
    assert first.json()["complete"] is True, "the seeded search did not complete"

    raw_response_paths(ref)[0].unlink()
    for saved in seeded.dir.glob("*.json"):
        saved.unlink()
    clear_caches()

    again = http.post(SEARCH_PATH, json=SEARCH_BODY)
    assert again.status_code == 200, again.text[:400]
    if again.json()["complete"] is not True:
        # Honestly incomplete: the gap has to be nameable and priced, or the
        # user cannot act on it (F4-S6 renders both verbatim).
        assert again.json()["missing"], "a pull reported incomplete must name what is absent"
        assert again.json()["calls_to_complete"] >= 1
        return

    derived = http.post(DERIVE_PATH, json=_curation(ref))
    assert derived.status_code == 200, (
        f"the search says this pull is complete but a derive over it returns "
        f"{derived.status_code}: {derived.text[:300]}"
    )
    assert len(derived.json()["comps"]) == windows, (
        f"the search reports the pull complete with nothing missing, and the derive over "
        f"the same ref sees {len(derived.json()['comps'])} of {windows} windows' evidence. "
        "The user is being told a set is whole while being shown less of it than they paid "
        "for — every number on the screen is short a cohort and nothing says so "
        "(NORTH_STAR; ARCHITECTURE.md §5a)."
    )


def test_a_search_over_a_damaged_entry_still_spends_nothing(
    http, seeded, rentcomp_home, clear_caches
) -> None:
    """Whatever the route decides to *say* about a damaged entry, it may not
    buy its way out of the question.

    Structural rather than a ledger read: in fixture mode the ledger never
    moves, so `calls_this_month == 0` would pass against a route that
    re-pulled everything (one of this project's four known vacuous
    assertions). The saved responses are deleted first, so any re-fetch shows
    up as raw files changing, appearing, or a second cache entry.
    """
    seeded()
    ref = http.post(SEARCH_PATH, json=SEARCH_BODY).json()["pull_ref"]
    entry = rentcomp_home / "cache" / ref

    raw_response_paths(ref)[0].unlink()
    for saved in seeded.dir.glob("*.json"):
        saved.unlink()
    surviving = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
    assert surviving, "the seeding search filed no raw responses, so this test proves nothing"
    clear_caches()

    http.post(SEARCH_PATH, json=SEARCH_BODY)

    after = {p.name: p.read_bytes() for p in (entry / "raw").glob("*.json")}
    for name, blob in surviving.items():
        assert after.get(name) == blob, (
            f"the response {name} was rewritten or removed by a search over a damaged "
            "entry. Bytes that survived are paid for and are never touched to tidy up an "
            "inconsistency (D24)."
        )
    assert sorted(p.name for p in (rentcomp_home / "cache").iterdir()) == [ref], (
        "a second cache entry appeared, so the damaged entry was worked around by starting "
        "a fresh pull somewhere else"
    )
