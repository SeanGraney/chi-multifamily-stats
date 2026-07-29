"""F4-S8 [INVARIANT] — the removal ladder and the suspect flag, over HTTP.

QA-authored, written test-first. **Layer 2** (AGENT_QA.md decision procedure
Q2): every claim here is "given curation state X, `POST /api/derive` returns
Y". Nothing in this file needs a browser, and nothing in it is a computed
value that Layer 1 could hold on its own — the Layer-1 files own the
classification arithmetic; this file owns the half of the AC that is about the
*assembled response*:

    "bucket leased-DOM stats exclude pendings and mark provisionals"

plus the negative the PM asked to be pinned: a withdrawal-suspect comp is
**display-only and never auto-excluded**, which is only really checkable where
inclusion is decided — in the assembled `DerivedState`.

THE FIXTURE IS BUILT SO THAT ONE NUMBER SEPARATES ALL THREE MISTAKES
--------------------------------------------------------------------
Six comps, identical $/sqft (so every one lands in the "at" bucket), differing
only in outcome::

    confirmed   dom 20   (1 price cut)
    confirmed   dom 40
    provisional dom 25
    confirmed   dom 30   withdrawal_suspect
    pending     dom  2
    censored    dom 100

The leased set is therefore {20, 25, 30, 40} and its median is **27.5**, and
that single number is wrong under each of the three failure modes this story
exists to prevent:

    27.5  correct
    25.0  the pending leaked in ({2,20,25,30,40})    <- inflates lease velocity
    25.0  the suspect was auto-excluded ({20,25,40}) <- badge acting as filter
    30.0  the provisional was dropped ({20,30,40})   <- "marked" read as "excluded"

Seeded through `RENTCOMP_FIXTURE_PULLS_DIR` as a pre-shaped pull, exactly like
`fixtures/synthetic/pulls/synthetic-basic.json` — zero network (D17), and no
dependence on the real pull's own census, which moves for other reasons.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

PULL_REF = "f4s8-removal-states"
AS_OF = date(2026, 6, 1)

#: Identical geometry and ask on every comp, so premium is 0.0 for all of them
#: and the bucket split cannot interfere with what this file is measuring.
_SHARED = {
    "unit": None,
    "lat": 41.9,
    "lng": -87.68,
    "beds": 2,
    "baths": 1.0,
    "sqft": 1000.0,
    "initial_ask": 2000.0,
    "cohort_year": 2026,
    "sqft_suspect": False,
    "relist_count": 0,
    "gap_days": 0,
}

_CUT = [{"on": "2026-02-01", "from_price": 2100.0, "to_price": 2000.0}]

COMPS = [
    {
        **_SHARED,
        "address": "1000 W Ladder St",
        "effective_dom": 20,
        "censored": False,
        "removal_class": "confirmed",
        "withdrawal_suspect": False,
        "first_listed": "2026-01-05",
        "cut_history": _CUT,
    },
    {
        **_SHARED,
        "address": "1010 W Ladder St",
        "effective_dom": 40,
        "censored": False,
        "removal_class": "confirmed",
        "withdrawal_suspect": False,
        "first_listed": "2026-01-06",
        "cut_history": [],
    },
    {
        **_SHARED,
        "address": "1020 W Ladder St",
        "effective_dom": 25,
        "censored": False,
        "removal_class": "provisional",
        "withdrawal_suspect": False,
        "first_listed": "2026-04-27",
        "cut_history": [],
    },
    {
        **_SHARED,
        "address": "1030 W Ladder St",
        "effective_dom": 30,
        "censored": False,
        "removal_class": "confirmed",
        "withdrawal_suspect": True,
        "first_listed": "2026-01-08",
        "cut_history": [],
    },
    {
        **_SHARED,
        "address": "1040 W Ladder St",
        "effective_dom": 2,
        "censored": False,
        "removal_class": "pending",
        "withdrawal_suspect": False,
        "first_listed": "2026-05-26",
        "cut_history": [],
    },
    {
        **_SHARED,
        "address": "1050 W Ladder St",
        "effective_dom": 100,
        "censored": True,
        "removal_class": None,
        "withdrawal_suspect": False,
        "first_listed": "2026-02-21",
        "cut_history": [],
    },
]


@pytest.fixture
def ladder_state(derive_client, make_body, clear_caches, tmp_path, monkeypatch):
    """`POST /api/derive` over the six-comp ladder fixture, parsed."""
    pulls = tmp_path / "pulls"
    pulls.mkdir()
    (pulls / f"{PULL_REF}.json").write_text(
        json.dumps({"ref": PULL_REF, "fetched_at": AS_OF.isoformat(), "comps": COMPS}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RENTCOMP_FIXTURE_PULLS_DIR", str(pulls))
    clear_caches()

    response = derive_client.post("/api/derive", json=make_body(pull_ref=PULL_REF))
    assert response.status_code == 200, (
        f"derive over the F4-S8 ladder fixture returned {response.status_code}: "
        f"{response.text[:600]}"
    )
    state = response.json()
    clear_caches()
    return state


def _at_bucket(state):
    buckets = {b["id"]: b for b in state["buckets"]}
    at = buckets["at"]
    assert at["count"] == len(COMPS), (
        "every comp in this fixture has premium 0.0 and must land in the 'at' bucket; "
        f"got {at['count']} of {len(COMPS)}. Bucket membership must not depend on removal state"
    )
    return at


def _comp_by_address(state, address: str):
    matches = [c for c in state["comps"] if c["address"] == address]
    assert len(matches) == 1, f"expected one comp at {address}, got {len(matches)}"
    return matches[0]


# ---------------------------------------------------------------------------
# AC 3 — "bucket leased-DOM stats exclude pendings and mark provisionals"
# ---------------------------------------------------------------------------


def test_bucket_leased_dom_stats_exclude_pendings(ladder_state) -> None:
    """The pending's 2-day DOM must not appear anywhere in the leased figures.

    NORTH_STAR: "Pendings are *excluded*, not just marked — mixing them in
    silently inflates apparent lease velocity." A 2-day 'lease' in a set whose
    real minimum is 20 does exactly that, and it is the kind of error that
    looks like good news."""
    at = _at_bucket(ladder_state)
    assert at["leased_dom_min"] == 20, (
        f"leased_dom_min is {at['leased_dom_min']}; 2 means the pending removal was counted as "
        "a lease"
    )
    assert at["leased_dom_median"] == 27.5, (
        f"leased_dom_median is {at['leased_dom_median']}; over {{20, 25, 30, 40}} it is 27.5. "
        "25.0 means the pending (dom 2) leaked into the leased set"
    )


def test_bucket_leased_dom_stats_mark_provisionals_and_still_count_them(ladder_state) -> None:
    """"Counted as leased, marked" — both halves, in one response.

    `provisional_count` is the mark; the median is the counting. Dropping the
    provisional to be safe would read as caution and would discard 30 of the
    528 closed chains in the real pull."""
    at = _at_bucket(ladder_state)
    assert at["provisional_count"] == 1, "the provisional comp must be marked in its bucket"
    assert at["leased_dom_median"] == 27.5, (
        f"leased_dom_median is {at['leased_dom_median']}; 30.0 means the provisional (dom 25) "
        "was excluded rather than merely marked"
    )
    assert _comp_by_address(ladder_state, "1020 W Ladder St")["removal_class"] == "provisional", (
        "the row must carry its own marker too, not only the bucket aggregate"
    )


def test_a_censored_floor_is_never_a_leased_outcome(ladder_state) -> None:
    """The invariant the whole ladder sits on: the censored comp's 100 days are
    a floor, reported separately, never mixed into a leased statistic."""
    at = _at_bucket(ladder_state)
    assert at["censored_floors"] == [100]
    assert at["leased_dom_max"] == 40, (
        f"leased_dom_max is {at['leased_dom_max']}; 100 means a censored floor was counted as "
        "an observed lease"
    )


def test_the_breakdown_counts_pendings_and_provisionals_and_clicks_through(ladder_state) -> None:
    """Evidence-first (T-S2): every count reaches the comps behind it. A
    "1 pending" the user cannot click is an assertion, not evidence."""
    breakdown = ladder_state["breakdown"]
    assert breakdown["pending"] == 1
    assert breakdown["provisional"] == 1
    assert breakdown["censored"] == 1
    assert len(breakdown["comp_keys"]["pending"]) == 1
    assert len(breakdown["comp_keys"]["provisional"]) == 1
    assert len(breakdown["comp_keys"]["withdrawal_suspect"]) == 1


# ---------------------------------------------------------------------------
# The negative — "display-only; never auto-excluded"
# ---------------------------------------------------------------------------


def test_a_withdrawal_suspect_comp_is_still_included_and_still_counted(ladder_state) -> None:
    """NORTH_STAR: a withdrawal-suspect is "**not** grounds for automatic
    exclusion. Display-only flag; the human makes the call."

    Three separate ways the flag could quietly become a filter, all checked:
    the comp's membership state, its presence in the bucket's click-through
    list, and its DOM's effect on the leased median. The last is the one a
    refactor is most likely to break without noticing — which is why the
    fixture is built so that dropping the suspect (dom 30) moves the median
    from 27.5 to 25.0."""
    suspect = _comp_by_address(ladder_state, "1030 W Ladder St")
    at = _at_bucket(ladder_state)

    assert suspect["withdrawal_suspect"] is True, "the flag must reach the row"
    assert suspect["state"] == "included", (
        f"the withdrawal-suspect comp is {suspect['state']!r}; the flag is display-only and must "
        "never auto-exclude"
    )
    assert suspect["weight"] > 0.0, "a suspect comp defaults to weight 1 like any other comp"
    assert suspect["key"] in at["comp_keys"], (
        "the suspect must remain click-through evidence in its bucket"
    )
    assert at["withdrawal_suspect_count"] == 1, "and it must be *counted* in the bucket stats"
    assert at["leased_dom_median"] == 27.5, (
        f"leased_dom_median is {at['leased_dom_median']}; 25.0 means the suspect (dom 30) was "
        "dropped from the leased set — the badge has become a filter"
    )


def test_a_pending_comp_is_excluded_from_statistics_but_not_from_the_response(
    ladder_state,
) -> None:
    """"Excluded from leased stats" is not "excluded from the evidence".

    The pending row is still there, still included, still clickable — the epic
    calls this out explicitly as expected behaviour rather than an error ("a
    removal <7 days old is not yet counted as leased or vacant"). Hiding it
    would leave the user unable to see why a count moved."""
    pending = _comp_by_address(ladder_state, "1040 W Ladder St")
    assert pending["removal_class"] == "pending"
    assert pending["censored"] is False, "a pending removal is removed — it is not censored"
    assert pending["state"] == "included", (
        "the pending comp is excluded from LEASED STATISTICS, not from the comp list"
    )
    assert pending["key"] in _at_bucket(ladder_state)["comp_keys"]


def test_the_ladder_survives_the_round_trip_unchanged(ladder_state) -> None:
    """Every rung reaches the wire with the spelling the contract declares.

    `RemovalClass = Literal["pending", "provisional", "confirmed"]` — the
    ladder ends at `confirmed`, and `None` means still active. A `leased`
    spelling anywhere is a contract break (standing PM erratum, `domain.py:47`)
    that would codegen into TypeScript and break the row component."""
    seen = {c["address"]: c["removal_class"] for c in ladder_state["comps"]}
    assert seen == {
        "1000 W Ladder St": "confirmed",
        "1010 W Ladder St": "confirmed",
        "1020 W Ladder St": "provisional",
        "1030 W Ladder St": "confirmed",
        "1040 W Ladder St": "pending",
        "1050 W Ladder St": None,
    }


# ---------------------------------------------------------------------------
# The story's row copy — REPORTED TO THE PM AS A GAP, NOT A UNILATERAL CALL
# ---------------------------------------------------------------------------


def test_the_wire_carries_what_the_pending_row_copy_needs(ladder_state) -> None:
    """F4-S8's story text: a pending row "shows 'removed 4d - classifying'".

    Rendering that "4d" needs the number of days since the removal, and the
    frontend may not compute a statistic (D5) or invent one. Today's
    `DerivedComp` carries `effective_dom`, `censored` and `removal_class` but
    **no removal date and no `first_listed`**, so `as_of - removed` is not
    recoverable on the client for a comp with no price cuts — the copy the
    story specifies cannot be rendered from this response.

    This test is deliberately generous about the fix: any one of a
    `days_since_removal`, a `removed_on`, or a `first_listed` field satisfies
    it, because which one is the developer's call. What is not the developer's
    call — or QA's — is whether the story's own row copy is in scope for this
    `[BE]` story or is F5-S1's problem to raise later. **That is flagged to the
    PM in the handoff**; if the PM rules it out of scope, this test moves to
    F5-S1 rather than being weakened."""
    pending = _comp_by_address(ladder_state, "1040 W Ladder St")
    available = {"days_since_removal", "removed_on", "removed_date", "first_listed"} & set(pending)
    assert available, (
        "no field on the pending comp lets the row render \"removed 4d - classifying\": the "
        "response carries neither a removal date nor a stitched-start date, and D5 forbids the "
        f"view from deriving one. Fields present: {sorted(pending)}"
    )


@pytest.mark.skip(
    reason="OPEN QUESTION for the owner (Semantic Change Protocol) — relayed to the PM in the "
    "F4-S8 QA handoff. Un-skip only on a ruling, never to get to green."
)
def test_open_question_a_pending_must_not_be_a_kaplan_meier_event(ladder_state) -> None:
    """ESCALATION, NOT A VERDICT. Do not implement against this test until the
    owner has ruled.

    The story says a pending removal is "excluded from **all** leased stats".
    The AC names one consumer of that rule (bucket leased-DOM stats) and this
    file covers it. But the Kaplan-Meier estimator has a second one: a pending
    comp has `censored=False`, so `pipeline/pricetest.py` hands it to
    `km_estimate` as an **observed lease event** at its DOM. In this fixture
    that is a 2-day lease — the precise thing NORTH_STAR says "silently
    inflates apparent lease velocity", now inflating the survival curve and
    every expected-vacancy-days figure derived from it.

    Two honest readings, and QA does not get to choose between them:

    (a) "all leased stats" includes the KM event set, so a pending must enter
        the estimator as *censored at its DOM* (we know it was on market that
        long; we do not know it leased) — a strictly more conservative curve;
    (b) the AC names bucket stats *because* that is the whole scope, and the
        estimator's job is to report observed removals as observed.

    Reading (a) changes what every KM curve means, so it is an owner decision
    under `docs/SEMANTIC_CHANGE_PROTOCOL.md`, not a developer's or a QA's. The
    5-question write-up is in the F4-S8 QA handoff. If the owner picks (a),
    delete the skip marker and this test asserts it; if (b), delete the test
    and record the ruling in QUEUE.md's log."""
    at = _at_bucket(ladder_state)
    assert at is not None  # placeholder — the real assertion depends on the ruling
