"""F4-S8 — the pending row's own datum, over HTTP.

Developer-authored, in answer to the gap QA reported and the PM ruled on:
*the DATUM is in scope for this `[BE]` story; the RENDERING is F5-S1's.*
QA's `test_the_wire_carries_what_the_pending_row_copy_needs` deliberately
accepts any of four field names, because which one to ship was the
developer's call. This file pins the one that shipped —
`DerivedComp.days_since_removal` — and, more importantly, pins the two things
that make it *usable* rather than merely present:

1. it is the same subtraction the ladder was classified by, so the badge and
   the figure beside it cannot contradict each other;
2. it is measured against the **pull date** and never against the wall clock
   (owner ruling 1) — the failure mode that would make a workspace reopened a
   week later report every removal a week staler with no new evidence.

Layer 2 (D21): `POST /api/derive` in, parsed JSON out. Zero network (D17) —
the pulls are written to a temp `RENTCOMP_FIXTURE_PULLS_DIR`, exactly as
`fixtures/synthetic/pulls/` are.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

AS_OF = date(2026, 6, 1)

_SHARED = {
    "unit": None,
    "lat": 41.9,
    "lng": -87.68,
    "beds": 2,
    "baths": 1.0,
    "sqft": 1000.0,
    "initial_ask": 2000.0,
    "cohort_year": 2026,
    "withdrawal_suspect": False,
    "cut_history": [],
    "relist_count": 0,
    "gap_days": 0,
}


def _removed_comp(address: str, *, days_since: int, dom: int, removal_class: str) -> dict:
    """A comp removed exactly ``days_since`` days before the pull date.

    `first_listed` is back-computed from the removal so the fixture states the
    same fact the pipeline reconstructs, rather than restating the
    reconstruction — the test would be circular the other way round.
    """
    removed = AS_OF - timedelta(days=days_since)
    return {
        **_SHARED,
        "address": address,
        "effective_dom": dom,
        "censored": False,
        "removal_class": removal_class,
        "first_listed": (removed - timedelta(days=dom)).isoformat(),
    }


#: One comp per rung, plus the censored control. The DOMs differ from each
#: other so that a `days_since_removal` accidentally wired to `effective_dom`
#: is wrong on every single row rather than coincidentally right on one.
COMPS = [
    _removed_comp("2000 W Datum St", days_since=4, dom=31, removal_class="pending"),
    _removed_comp("2010 W Datum St", days_since=9, dom=17, removal_class="provisional"),
    _removed_comp("2020 W Datum St", days_since=90, dom=52, removal_class="confirmed"),
    {
        **_SHARED,
        "address": "2030 W Datum St",
        "effective_dom": 100,
        "censored": True,
        "removal_class": None,
        "first_listed": (AS_OF - timedelta(days=100)).isoformat(),
    },
]

EXPECTED_DAYS = {
    "2000 W Datum St": 4,
    "2010 W Datum St": 9,
    "2020 W Datum St": 90,
    "2030 W Datum St": None,
}


@pytest.fixture
def derive_at(derive_client, make_body, clear_caches, tmp_path, monkeypatch):
    """``derive_at(as_of) -> DerivedState`` over the same four comps.

    The pull's `fetched_at` is the only thing that varies, which is what makes
    the wall-clock test below a controlled experiment rather than an
    observation.
    """
    pulls = tmp_path / "pulls"
    pulls.mkdir()
    monkeypatch.setenv("RENTCOMP_FIXTURE_PULLS_DIR", str(pulls))

    def _derive_at(as_of: date):
        ref = f"f4s8-datum-{as_of.isoformat()}"
        (pulls / f"{ref}.json").write_text(
            json.dumps({"ref": ref, "fetched_at": as_of.isoformat(), "comps": COMPS}),
            encoding="utf-8",
        )
        clear_caches()
        response = derive_client.post("/api/derive", json=make_body(pull_ref=ref))
        assert response.status_code == 200, f"{response.status_code}: {response.text[:600]}"
        state = response.json()
        clear_caches()
        return state

    return _derive_at


def _by_address(state) -> dict[str, dict]:
    return {comp["address"]: comp for comp in state["comps"]}


def test_every_removed_comp_reports_how_long_ago_it_was_removed(derive_at) -> None:
    """The datum itself: "removed 4d — classifying" is renderable from this.

    The pending row's 4 is the story's own example, and it is asserted as a
    literal here so that the number the story specifies is pinned by name."""
    comps = _by_address(derive_at(AS_OF))
    actual = {address: comp["days_since_removal"] for address, comp in comps.items()}
    assert actual == EXPECTED_DAYS, (
        f"days_since_removal is {actual}, expected {EXPECTED_DAYS}. The pending row cannot "
        'render "removed 4d — classifying" without the 4'
    )


def test_a_still_listed_comp_reports_no_removal_age(derive_at) -> None:
    """`None`, not `0` and not its censored floor.

    `0` would render as "removed 0d — classifying" on a unit that is still on
    the market, which is the censored/leased confusion NORTH_STAR calls the
    single most important distinction in the system, printed on a row."""
    censored = _by_address(derive_at(AS_OF))["2030 W Datum St"]
    assert censored["censored"] is True
    assert censored["days_since_removal"] is None, (
        f"a censored comp reported days_since_removal={censored['days_since_removal']!r}; it "
        "has not been removed"
    )
    assert censored["effective_dom"] == 100, (
        "and its DOM floor must be untouched — the two are different quantities"
    )


def test_the_number_and_the_rung_beside_it_agree(derive_at) -> None:
    """One fact, reported twice, must not be reported two ways.

    A row reading "provisional · removed 3d" is worse than a row with no date:
    it invites the user to distrust the badge that is actually correct. The
    ladder is `< 7` pending, `< 42` provisional, else confirmed."""
    for comp in derive_at(AS_OF)["comps"]:
        days, rung = comp["days_since_removal"], comp["removal_class"]
        if rung is None:
            assert days is None
            continue
        expected = "pending" if days < 7 else "provisional" if days < 42 else "confirmed"
        assert rung == expected, (
            f"{comp['address']} is on the {rung!r} rung but reports removed {days}d ago, which "
            f"is the {expected!r} rung"
        )


def test_the_age_is_measured_from_the_pull_date_not_from_today(derive_at) -> None:
    """Owner ruling 1, on this field.

    The same four comps, read from a pull fetched a year later, are a year
    older — and *exactly* a year older. Two failure modes die here: a
    `date.today()` reading (which would report ~the same number from both
    pulls, and a number that changes tomorrow), and an `as_of` that is
    ignored entirely.

    The pull dates are deliberately historical. A wall-clock implementation
    cannot accidentally agree with either of them at any point in the future,
    so this test does not decay."""
    early = _by_address(derive_at(AS_OF))
    late = _by_address(derive_at(AS_OF + timedelta(days=365)))

    for address, expected in EXPECTED_DAYS.items():
        if expected is None:
            assert late[address]["days_since_removal"] is None
            continue
        assert early[address]["days_since_removal"] == expected
        assert late[address]["days_since_removal"] == expected + 365, (
            f"{address} reports {late[address]['days_since_removal']}d from a pull fetched a "
            f"year later; it should be {expected + 365}d. The count must move with meta.as_of"
        )


def test_the_field_is_a_count_and_not_a_date(derive_at) -> None:
    """D5/D15: shipping `removed_on` alone would leave the view subtracting it
    from `meta.as_of` — a derivation the view may not perform, in a language
    whose date arithmetic crosses timezones. What arrives is already the
    number the row prints."""
    pending = _by_address(derive_at(AS_OF))["2000 W Datum St"]
    assert isinstance(pending["days_since_removal"], int)
    assert not isinstance(pending["days_since_removal"], bool)
