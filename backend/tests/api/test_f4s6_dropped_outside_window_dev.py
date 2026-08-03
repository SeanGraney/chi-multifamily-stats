"""F4-S6 — `Breakdown.dropped_outside_window`, the datum the empty state's
"names the binding constraint" clause rests on. Developer-written (AGENT_DEVELOPER
step 4: QA's plan pins the *behaviour*, these pin the boundary it stands on).

WHY THIS FILE IS NOT COVERED BY QA'S
-------------------------------------
`test_f4s6_partial_pull_and_empty_state.py::
test_an_empty_result_says_which_constraint_emptied_it` asserts the *distinction*
exists — that a pull the source answered with nothing and a pull the date window
emptied do not produce the same payload. It deliberately accepts any field pair
carrying that distinction, because the spelling was the developer's to choose.

What it therefore does not pin, and what the empty state's copy actually
branches on, is the three properties below:

1. **`None` is not `0`.** The view renders a different sentence for "the window
   dropped nothing, so the radius/beds/types are what bound" than for "we have
   no window-stage measurement for this pull at all". A pre-shaped synthetic
   pull has no shaping stage behind it, so reporting `0` there would assert a
   measurement nobody took — and would send a user off widening the wrong knob.
2. **It counts chains, not raw records** (F4-S4's PM ruling A4). Three records
   that stitch into one padding chain are one drop. A record count would
   over-report by the re-list count and would never reconcile against `pulled`,
   which is a chain count by construction.
3. **It is outside F7-S1's `included + excluded + filtered == pulled`
   identity.** Those are measured over post-window comps; this counts chains
   that never reached that population. Folding it in would silently change what
   that [INVARIANT] is measured over.

MONEY: fixture mode throughout. `RENTCOMP_LIVE` is never set and no key is
configured; the cache-backed cases are seeded with saved responses, the
technique `test_f4s9_search_route.py` established. Zero live RentCast calls
(D17, WORKFLOW.md §6).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import Ledger, current_month, save_ledger

SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"

LIVE_ENV = "RENTCOMP_LIVE"
KEY_ENV = "RENTCAST_API_KEY"
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

TODAY = date.today()

#: A narrow month-day window, so "the window dropped it" is reachable: the pull
#: buys +/-90 days of padding either side (spec §3.2), and everything outside
#: 06-15..06-30 is padding this stage discards.
NARROW_SEARCH: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 0.5,
    "bedrooms": "3",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start": "06-15",
    "window_end": "06-30",
}

SUBJECT: dict[str, Any] = {
    "address": "3651 S Wood St Unit 2",
    "lat": 41.82,
    "lng": -87.67,
    "sqft": 1000.0,
    "beds": 3.0,
    "baths": 1.0,
}


def plan_args(search: dict[str, Any]) -> dict[str, Any]:
    return {
        "address": search["address"],
        "radius": search["radius"],
        "bedrooms": search["bedrooms"],
        "bathrooms": search["bathrooms"],
        "property_types": search["property_types"],
        "years_back": search["years_back"],
        "window_start_mmdd": search["window_start"],
        "window_end_mmdd": search["window_end"],
    }


def derive_body(pull_ref: str) -> dict[str, Any]:
    return {
        "pull_ref": pull_ref,
        "subject": dict(SUBJECT),
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 7.0,
        "candidate_rent": None,
    }


def listing(listing_id: str, listed: date, *, removed_after: int = 40) -> dict:
    """One synthetic RentCast listing record."""
    return {
        "id": listing_id,
        "formattedAddress": f"{listing_id} W Wood St, Chicago, IL 60609",
        "addressLine1": f"{listing_id} W Wood St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 1950.0,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 950,
        "listedDate": listed.isoformat() + "T00:00:00.000Z",
        "removedDate": (listed + timedelta(days=removed_after)).isoformat() + "T00:00:00.000Z",
        "status": "Inactive",
    }


@pytest.fixture
def client(app, rentcomp_home):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def seed_pull(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """`seed_pull(records) -> pull_ref` — a complete, cache-backed pull holding
    exactly `records` in every planned window, fetched from fixtures."""
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in (LIVE_ENV, KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def _seed(records: list[dict], search: dict[str, Any] | None = None) -> str:
        body = search or NARROW_SEARCH
        for stale in fixtures.glob("*.json"):
            stale.unlink()
        queries = fetchable_queries(plan_pull_queries(TODAY, **plan_args(body)))
        assert queries, "the fixture search plans no fetchable window — fix the fixture"
        for query in queries:
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(
                json.dumps(records).encode("utf-8")
            )
        clear_caches()
        return body

    return _seed


def run_search_and_derive(client, search: dict[str, Any]) -> dict:
    response = client.post(SEARCH_PATH, json=search)
    assert response.status_code == 200, response.text[:400]
    result = response.json()
    assert result["complete"] is True, result
    derived = client.post(DERIVE_PATH, json=derive_body(result["pull_ref"]))
    assert derived.status_code == 200, derived.text[:400]
    return derived.json()


def a_day_in(mmdd: str, *, years_ago: int = 1) -> date:
    month, day = (int(part) for part in mmdd.split("-"))
    return date(TODAY.year - years_ago, month, day)


# ===========================================================================
# harness precondition — fails loudly, never skips (WORKFLOW.md §2)
# ===========================================================================


def test_the_source_under_test_is_this_checkouts() -> None:
    """In a worktree the editable install's `.pth` resolves to the MAIN
    checkout, so a run can look entirely correct while answering about the
    wrong code. A no-op on the main checkout."""
    import rentcomp

    loaded = Path(rentcomp.__file__).resolve()
    expected = (Path(__file__).resolve().parents[3] / "backend" / "src" / "rentcomp").resolve()
    assert loaded.parent == expected, (
        f"tests are importing rentcomp from {loaded.parent}, not from this checkout's "
        f"{expected}. Set PYTHONPATH to this checkout's backend/src (on Windows the "
        "separator is ';', not ':') and run pytest BARE from the repo root."
    )


# ===========================================================================
# 1. `None` is not `0` — the distinction the empty state branches on
# ===========================================================================


def test_a_pre_shaped_synthetic_pull_reports_no_window_measurement(derive, pull_ref) -> None:
    """A pull with no shaping stage behind it reports `None`, never `0`.

    `fixtures/synthetic/pulls/*.json` are already-shaped comps: no raw records,
    no window filter, nothing dropped and nothing *kept* by a window either.
    Reporting `0` would be a measurement claim nobody is entitled to make — and
    the view branches on exactly this, so a `0` here would put "the date window
    dropped nothing, widen the radius instead" in front of a user on the
    strength of a stage that never ran.
    """
    response = derive()
    assert response.status_code == 200, response.text[:400]
    breakdown = response.json()["breakdown"]

    assert "dropped_outside_window" in breakdown, (
        "Breakdown no longer carries dropped_outside_window; F4-S6's empty state cannot "
        "name the binding constraint without it (D5 — the view may not infer it)"
    )
    assert breakdown["dropped_outside_window"] is None, (
        "a pre-shaped synthetic pull reported a window-drop count of "
        f"{breakdown['dropped_outside_window']!r}. 'We did not measure' and 'we measured "
        "zero' are different claims that lead the empty state to opposite conclusions."
    )


def test_a_real_pull_whose_records_are_all_in_window_reports_zero_not_none(
    client, seed_pull
) -> None:
    """The complement: a shaped pull always reports a number, even when it is 0.

    This is what makes `None` meaningful. If a real pull could also answer
    `None`, the empty state's "the window dropped nothing" branch would be
    unreachable and every empty result would fall through to the same sentence.
    """
    in_window = a_day_in(NARROW_SEARCH["window_start"])
    search = seed_pull([listing("in-1", in_window), listing("in-2", in_window)])
    state = run_search_and_derive(client, search)

    assert state["breakdown"]["pulled"] > 0, (
        "this test needs records the window KEPT; it is not exercising its own premise"
    )
    assert state["breakdown"]["dropped_outside_window"] == 0


def test_a_window_emptied_pull_says_so_while_a_source_emptied_one_does_not(
    client, seed_pull
) -> None:
    """The two zero-comp states are distinguishable on the wire.

    Both end at `pulled == 0` and they ask the user for opposite actions —
    widen the date window vs. widen the radius/beds/types. This is the whole
    reason the field exists, asserted on the two payloads rather than on the
    pipeline, because the view only ever sees the payloads.
    """
    # The two scenarios MUST differ in their search params. `pull_ref` is a hash
    # of those params (F3-S1), and a second search with identical ones is a
    # cache HIT that fetches nothing and returns the first pull's evidence — so
    # a shared address here would compare a payload against itself.
    february = a_day_in("02-01")

    # (a) the source had records; every one was listed in February, which the
    #     06-15..06-30 window drops (they are inside the pull's +/-90d padding).
    search = seed_pull(
        [listing(f"feb-{n}", february + timedelta(days=n)) for n in range(3)],
        dict(NARROW_SEARCH, address="1 W Window St, Chicago, IL 60609"),
    )
    window_emptied = run_search_and_derive(client, search)

    # (b) the source had nothing at all for these constraints.
    search = seed_pull([], dict(NARROW_SEARCH, address="2 W Nowhere St, Chicago, IL 60609"))
    source_emptied = run_search_and_derive(client, search)

    assert window_emptied["breakdown"]["pulled"] == 0, window_emptied["breakdown"]
    assert source_emptied["breakdown"]["pulled"] == 0, source_emptied["breakdown"]

    assert window_emptied["breakdown"]["dropped_outside_window"] > 0, (
        "a pull whose every record fell outside the date window reported nothing dropped, "
        "so the empty state would tell the user to widen the radius — which would spend "
        "calls and return the same nothing"
    )
    assert source_emptied["breakdown"]["dropped_outside_window"] == 0, (
        "a pull the source answered with nothing reported records dropped by the window"
    )


# ===========================================================================
# 2. chains, not raw records (F4-S4 PM ruling A4)
# ===========================================================================


def test_the_drop_count_is_chains_not_records(client, seed_pull) -> None:
    """Three re-lists of ONE unit outside the window are one drop, not three.

    The window is applied to a stitched chain, so the chain is the thing
    dropped. Counting records would over-report by the re-list count and would
    never reconcile against `pulled`, which is a chain count by construction —
    and the empty state prints this number to the user as "N listings were
    pulled and every one fell outside your window".
    """
    february = a_day_in("02-01")
    # One address, three spells 10 days apart: gaps of 10 days are well inside
    # the 42-day stitch threshold, so they merge into a single chain.
    one_unit = [
        {
            **listing("relist", february + timedelta(days=offset), removed_after=5),
            "id": f"relist-{offset}",
            "addressLine1": "500 W Wood St",
            "formattedAddress": "500 W Wood St, Chicago, IL 60609",
        }
        for offset in (0, 10, 20)
    ]
    search = seed_pull(one_unit)
    state = run_search_and_derive(client, search)

    assert state["breakdown"]["pulled"] == 0, state["breakdown"]
    assert state["breakdown"]["dropped_outside_window"] == 1, (
        "three re-listings of one unit were counted as three drops. The window filters "
        "stitched chains, so a re-listed unit is one dropped comp — a record count would "
        "tell the user three listings were found where one was."
    )


# ===========================================================================
# 3. outside F7-S1's reconciliation identity
# ===========================================================================


def test_the_drop_count_is_not_folded_into_the_breakdown_identity(client, seed_pull) -> None:
    """`included + excluded + filtered == pulled` still holds, unchanged.

    F7-S1's [INVARIANT] is measured over post-window comps. The dropped chains
    never reached that population, so adding them to any of its four terms
    would silently redefine what the invariant is measured over — the kind of
    change that looks like a field addition and is actually a semantic one.
    """
    february = a_day_in("02-01")
    june = a_day_in(NARROW_SEARCH["window_start"])
    search = seed_pull(
        [listing("kept-1", june), listing("kept-2", june + timedelta(days=2))]
        + [listing(f"dropped-{n}", february + timedelta(days=n)) for n in range(4)]
    )
    state = run_search_and_derive(client, search)
    breakdown = state["breakdown"]

    assert breakdown["pulled"] == 2, breakdown
    assert breakdown["dropped_outside_window"] == 4, breakdown
    assert (
        breakdown["included"] + breakdown["excluded"] + breakdown["filtered"]
        == breakdown["pulled"]
    ), breakdown
    assert len(state["comps"]) == breakdown["pulled"], (
        "the dropped chains leaked into the comp list; a comp the window removed is not "
        "evidence this pull holds"
    )
