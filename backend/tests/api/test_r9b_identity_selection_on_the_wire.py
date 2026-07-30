"""Row 9b — what the identity-field fix changes on `POST /api/derive`.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Layer 2: decision procedure Q2 — every assertion below is phrasable
as "given curation state X, the API returns Y", and every value asserted is a
`DerivedState` field. Zero network (D17); `ws1-real` shapes the two committed
T-S3 gate responses off disk.

WHY THIS FILE EXISTS AT ALL — THE LAYER THE SIBLINGS CANNOT REACH
------------------------------------------------------------------
`tests/unit/test_r9b_identity_field_selection.py` proves the *rule* on
synthetic chains, and `tests/unit/test_r9b_real_pull_identity_census.py` proves
it *bites* on the real pull. Both stop at `shape_raw_pull`, which returns
`StitchedComp`s — and row 9b's whole reason for existing is what happens
*downstream* of that: F4-S5 excludes a missing-sqft comp "from every median",
so the selector decides cohort evidence, then `premium`, then **which bucket a
comp lands in**, then that bucket's DOM statistics. None of that is observable
from a `StitchedComp`. It is observable here, and only here.

THE MEASURED CONSEQUENCE (real pull, canonical body, before writing anything —
2026-07-30). One comp moves, and it moves everything it touches:

    2350 s leavitt st|1r    sqft   None -> 700.0
                            psf    None -> 4.2785714285714285
                            state  "excluded" -> "included"
                            weight 0.0 -> 1.0
                            bucket None -> "above"
    breakdown.included               484 -> 485
    breakdown.excluded / missing_sqft 83 -> 82
    cohort 2025 selected/pulled      215 -> 216   (median UNCHANGED)
    bucket "above" count             210 -> 211
    bucket "above" leased_dom_median 33.0 -> 34.0   <-- the F10-S1 number
    anchor.rent.mid @ drift 7        1791.67 -> 1792.25   (+$0.58, +0.033%)
    anchor.rent.mid @ drift 0        1681.82 -> 1687.50   (+$5.68, +0.338%)

⚠ THE BUCKET DOM MEDIAN MOVES A FULL DAY. That is the statistic F10-S1 renders
and the reason this row was sequenced ahead of it. The *anchor* barely moves
(see `test_the_anchor_moves_and_the_move_is_small`); the bucket DOM does.

Every "after" number above is measured against the same candidate rule the row
describes — the most-complete observation supplies the identity fields — and is
asserted below as an expectation, not inherited from an implementation. If the
developer's chosen rule produces different numbers, that is a conversation with
the PM about the rule, not a licence to edit these constants.
"""

from __future__ import annotations

import pytest

from rentcomp.pipeline.keys import comp_key

PULL_REF = "ws1-real"

#: The one comp this row moves.
LEAVITT = comp_key("2350 S Leavitt St", "Unit 1R")
#: The comp whose displayed `unit` flips — presentational only, no numeric effect.
PL_46TH = comp_key("2453 W 46th Pl", "Unit 1")

TOL = 1e-9

# --- measured before/after, canonical body (subject sqft 1000, drift 7.0) ----
COMPS_TOTAL = 567
INCLUDED_BEFORE, INCLUDED_AFTER = 484, 485
MISSING_SQFT_BEFORE, MISSING_SQFT_AFTER = 83, 82

LEAVITT_SQFT = 700.0
LEAVITT_PSF = 4.2785714285714285
LEAVITT_PREMIUM = 1.5671428571428572

ABOVE_COUNT_BEFORE, ABOVE_COUNT_AFTER = 210, 211
ABOVE_DOM_MEDIAN_BEFORE, ABOVE_DOM_MEDIAN_AFTER = 33.0, 34.0

ANCHOR_RENT_MID_BEFORE_D7, ANCHOR_RENT_MID_AFTER_D7 = 1791.6666666666667, 1792.2500000000002
ANCHOR_RENT_MID_BEFORE_D0, ANCHOR_RENT_MID_AFTER_D0 = 1681.818181818182, 1687.5


@pytest.fixture
def real(derive, make_body):
    """`real(**overrides) -> DerivedState` against the real committed pull."""

    def _derive(**overrides):
        response = derive(make_body(pull_ref=PULL_REF, **overrides))
        assert response.status_code == 200, (
            f"POST /api/derive against {PULL_REF!r} returned {response.status_code}: "
            f"{response.text[:400]}"
        )
        return response.json()

    return _derive


def _comp(derived: dict, key: str) -> dict:
    for row in derived["comps"]:
        if row["key"] == key:
            return row
    raise AssertionError(
        f"{key!r} is not in the payload. This row's whole subject is that comp; if it is gone, "
        f"the grouping or the stitcher moved, not the selector."
    )


def _bucket(derived: dict, bucket_id: str) -> dict:
    for stat in derived["buckets"]:
        if stat["id"] == bucket_id:
            return stat
    raise AssertionError(f"no {bucket_id!r} bucket in {[b['id'] for b in derived['buckets']]}")


# ---------------------------------------------------------------------------
# preconditions — failing, never skipping (WORKFLOW.md §2)
# ---------------------------------------------------------------------------


def test_the_real_pull_is_reachable_and_holds_the_comp_this_row_moves(real) -> None:
    """A failing precondition rather than a skip guard. Green before and after
    the fix: it asserts the evidence exists, not what the selector does with
    it."""
    derived = real()
    assert derived["breakdown"]["pulled"] == COMPS_TOTAL, (
        f"the real pull shaped to {derived['breakdown']['pulled']} comps, expected "
        f"{COMPS_TOTAL}. Row 9b changes what a comp SAYS, never how many there are."
    )
    assert _comp(derived, LEAVITT) is not None
    assert _comp(derived, PL_46TH) is not None


# ---------------------------------------------------------------------------
# AC5 / AC6 — the comp joins the population behind `premium`
# ---------------------------------------------------------------------------


def test_the_leavitt_comp_reaches_the_wire_with_the_sqft_its_pull_reported(real) -> None:
    """AC5 on the wire. The pull holds `squareFootage: 700` for this unit, so
    the payload must not say "unknown" — that is the difference between no
    evidence and discarded evidence."""
    row = _comp(real(), LEAVITT)
    assert row["sqft"] == LEAVITT_SQFT, (
        f"(pre-fix value was None) the payload reports sqft={row['sqft']} for a unit whose own "
        f"pull reported 700."
    )
    assert row["psf"] == pytest.approx(LEAVITT_PSF, abs=TOL)


def test_the_comp_moves_from_excluded_to_included(real) -> None:
    """AC6 — the reason this row is not cosmetic.

    F4-S5 [INVARIANT]: missing-sqft comps are "flagged, default weight 0,
    excluded from every median". Giving the comp its reported square footage
    therefore does not just change a printed field — it moves the comp into the
    evidence set behind every cohort median, hence behind `premium`.
    """
    derived = real()
    row = _comp(derived, LEAVITT)
    assert row["state"] == "included", (
        f"(pre-fix value was 'excluded') state={row['state']!r}"
    )
    assert row["weight"] == 1.0, (
        f"(pre-fix value was 0.0) weight={row['weight']} — `pipeline.weights` defaults a comp "
        f"with no sqft to 0, so acquiring one is what restores the default weight"
    )
    assert row["premium"] == pytest.approx(LEAVITT_PREMIUM, abs=TOL), (
        f"(pre-fix value was None) premium={row['premium']}"
    )
    assert row["contribution_share"] is not None


def test_the_breakdown_counts_move_by_exactly_one(real) -> None:
    """The blast radius, counted. Exactly one comp changes side.

    `included + excluded + filtered == pulled` (F7-S1) must still hold, and the
    missing-sqft evidence list must lose this comp and nothing else — the list
    is what T-S2 keeps one click away, so it has to stay honest.
    """
    b = real()["breakdown"]
    assert b["included"] == INCLUDED_AFTER, f"(pre-fix {INCLUDED_BEFORE}) included={b['included']}"
    assert b["missing_sqft"] == MISSING_SQFT_AFTER, (
        f"(pre-fix {MISSING_SQFT_BEFORE}) missing_sqft={b['missing_sqft']}"
    )
    assert b["included"] + b["excluded"] + b["filtered"] == b["pulled"], (
        "F7-S1's identity must survive this row"
    )
    assert LEAVITT not in b["comp_keys"]["missing_sqft"], (
        "the comp still appears in the missing-sqft evidence list after acquiring a sqft"
    )


def test_the_comp_becomes_evidence_for_its_own_cohort(real) -> None:
    """It joins cohort 2025's evidence list. The 2025 median does NOT move —
    215 -> 216 comps does not shift the lower weighted median here — which is
    worth pinning precisely because it is counter-intuitive: the comp enters the
    median's evidence without changing the median's value."""
    derived = real()
    stat = next(c for c in derived["cohorts"] if c["year"] == 2025)
    assert stat["selected_count"] == 216, f"(pre-fix 215) selected_count={stat['selected_count']}"
    assert LEAVITT in stat["comp_keys"]
    assert stat["median_psf"] == pytest.approx(1.6666666666666667, abs=TOL), (
        "the 2025 cohort median is unchanged by this row — the comp joins the evidence set "
        "without moving the median. If this moved, re-measure every number in this file."
    )


# ---------------------------------------------------------------------------
# ⭐ the bucket-DOM consequence — why this row precedes F10-S1
# ---------------------------------------------------------------------------


def test_the_comp_lands_in_a_bucket_and_moves_that_buckets_dom_median(real) -> None:
    """⭐ THE REASON ROW 9b IS SEQUENCED AHEAD OF F10-S1.

    The comp arrives at premium +156.7%, which puts it in the `above` bucket,
    and the `above` bucket's **leased DOM median moves 33.0 -> 34.0 days**.
    That is the headline number F10-S1 renders, moved by a full day by a record-
    shaping rule two stages upstream.

    `leased_dom_median` is computed over leased comps only, so this also
    confirms the comp is being counted as leased (it is `confirmed`, DOM 128)
    rather than censored — the distinction NORTH_STAR calls the most important
    in the system.
    """
    derived = real()
    row = _comp(derived, LEAVITT)
    assert row["bucket"] == "above", f"(pre-fix None) bucket={row['bucket']!r}"

    above = _bucket(derived, "above")
    assert LEAVITT in above["comp_keys"], "the comp must be listed in its own bucket's evidence"
    assert above["count"] == ABOVE_COUNT_AFTER, (
        f"(pre-fix {ABOVE_COUNT_BEFORE}) above.count={above['count']}"
    )
    assert above["leased_dom_median"] == pytest.approx(ABOVE_DOM_MEDIAN_AFTER, abs=TOL), (
        f"(pre-fix {ABOVE_DOM_MEDIAN_BEFORE}) the `above` bucket's leased DOM median is "
        f"{above['leased_dom_median']}. This is F10-S1's headline statistic and this row moves "
        f"it — a bucket DOM computed before this fix is computed over the wrong comp set."
    )


def test_the_other_two_buckets_do_not_move(real) -> None:
    """The blast radius, again: only the bucket the comp joins may change.

    A fix that moved `below` or `at` would mean comps are changing bucket for
    reasons other than the one comp that gained a square footage.
    """
    derived = real()
    for bucket_id, count, dom_median in (("below", 201, 35.0), ("at", 73, 39.0)):
        stat = _bucket(derived, bucket_id)
        assert stat["count"] == count, f"{bucket_id}.count={stat['count']}, expected {count}"
        assert stat["leased_dom_median"] == pytest.approx(dom_median, abs=TOL), (
            f"{bucket_id}.leased_dom_median={stat['leased_dom_median']}, expected {dom_median} — "
            f"unchanged by this row"
        )


# ---------------------------------------------------------------------------
# the anchor — the row's explicit quantification requirement
# ---------------------------------------------------------------------------


def test_the_anchor_moves_and_the_move_is_small(real) -> None:
    """⚠ THE ROW'S EXPLICIT REQUIREMENT: quantify the anchor movement.

    Measured on the real pull, canonical body, both ends of the drift range:

        drift 7%:  $1791.67 -> $1792.25   (+$0.58,  +0.033%)
        drift 0%:  $1681.82 -> $1687.50   (+$5.68,  +0.338%)

    Both are well under half a percent, and at drift 7% the move is smaller
    than the dollar the anchor is displayed to. **The anchor movement is not
    material.** What IS worth the owner's attention is the datum behind it —
    see `test_the_admitted_comp_is_the_pulls_most_extreme_psf` in
    `tests/unit/test_r9b_real_pull_identity_census.py` — and the bucket DOM
    median above, which moves a full day.

    Pinned exactly, not as a bound, so that a fix which widens beyond this one
    comp fails here loudly rather than drifting the market reference point by an
    amount nobody re-measured.
    """
    d7 = real(drift_pct=7.0)["anchor"]
    assert d7["n_comps"] == INCLUDED_AFTER, (
        f"(pre-fix {INCLUDED_BEFORE}) the anchor's evidence set is {d7['n_comps']} comps"
    )
    assert d7["rent"]["mid"] == pytest.approx(ANCHOR_RENT_MID_AFTER_D7, abs=1e-6), (
        f"(pre-fix {ANCHOR_RENT_MID_BEFORE_D7}) anchor.rent.mid={d7['rent']['mid']}"
    )

    d0 = real(drift_pct=0.0)["anchor"]
    assert d0["rent"]["mid"] == pytest.approx(ANCHOR_RENT_MID_AFTER_D0, abs=1e-6), (
        f"(pre-fix {ANCHOR_RENT_MID_BEFORE_D0}) anchor.rent.mid={d0['rent']['mid']}"
    )

    moved_pct = abs(d0["rent"]["mid"] - ANCHOR_RENT_MID_BEFORE_D0) / ANCHOR_RENT_MID_BEFORE_D0
    assert moved_pct < 0.01, (
        f"the anchor moved {moved_pct:.2%} at drift 0. This row admits ONE comp; a move of a "
        f"percent or more means the fix is changing the evidence base more widely than the "
        f"single chain that disagrees about its square footage, and the queue's recorded "
        f"quantification is wrong."
    )


# ---------------------------------------------------------------------------
# what must NOT move
# ---------------------------------------------------------------------------


def test_the_46th_pl_units_display_flips_with_no_numeric_effect(real) -> None:
    """AC7 — the row's second named comp, and the whole of its effect.

    Row 9b records that this comp's displayed `unit` flips `Unit 1` -> `# 1`
    and calls it presentational. Asserted as such: the display string may move,
    and **nothing else about the comp may**. Its six spells all report 800 sqft,
    so there is no numeric consequence available here at all.
    """
    row = _comp(real(), PL_46TH)
    assert row["sqft"] == 800.0, "all six of this chain's spells report 800 sqft"
    assert row["effective_dom"] == 205, (
        "F4-S3 corrected this chain from 165 to 205 days and this row must not disturb it — "
        "effective_dom is the Kaplan-Meier event time"
    )
    assert row["premium"] is not None
    assert row["state"] == "included"
    assert row["key"] == PL_46TH, (
        f"the comp's KEY moved to {row['key']!r}. A display-string change is allowed; a key "
        f"change silently discards the owner's saved weights (F13-S1 [INVARIANT])."
    )


def test_no_comps_dom_or_removal_classification_moves(real) -> None:
    """AC8 — the blast-radius guard at the payload level.

    DOM, censoring and the removal ladder are functions of dates, and none of
    the seven identity fields is a date. The census counts are F4-S8's pinned
    values; this row must leave every one of them exactly where it found them.
    """
    b = real()["breakdown"]
    assert (b["censored"], b["pending"], b["provisional"]) == (39, 5, 30), (
        f"the removal census moved to censored={b['censored']} pending={b['pending']} "
        f"provisional={b['provisional']}; F4-S8 pinned 39/5/30 and this row changes no date"
    )
    leavitt = _comp(real(), LEAVITT)
    assert leavitt["effective_dom"] == 128, "F4-S3's corrected DOM for this chain"
    assert leavitt["censored"] is False
    assert leavitt["removal_class"] == "confirmed"
    assert leavitt["initial_ask"] == 2995.0, "initialAsk is the FIRST spell's price (F4-S3)"
    assert leavitt["cohort_year"] == 2025


def test_the_missing_sqft_warning_counts_down_with_the_breakdown(real) -> None:
    """The user-facing count and the payload count are the same number.

    `_warnings` builds the missing-sqft message from its own pass over `psfs`,
    while `breakdown.missing_sqft` is built in `_breakdown`. Two independent
    computations of one fact, so this row is a chance for them to disagree.
    """
    derived = real()
    warning = next(w for w in derived["warnings"] if w["code"] == "missing_sqft")
    assert f"{MISSING_SQFT_AFTER} comp(s)" in warning["message"], (
        f"(pre-fix said {MISSING_SQFT_BEFORE}) the warning reads: {warning['message'][:120]}"
    )
    assert warning["message"].count(str(derived["breakdown"]["missing_sqft"])) >= 1


def test_two_identical_requests_still_return_identical_bytes(derive, make_body) -> None:
    """F0-S2 [INVARIANT], restated because this row adds a selection step.

    A selector that read arrival order, dict iteration order, or anything not
    derived from the spells' own values would show up here — and `2350 S
    Leavitt St 1R` is two listing ids arriving in different halves of the pull,
    so the pull genuinely exercises it.
    """
    body = make_body(pull_ref=PULL_REF)
    first = derive(dict(body))
    second = derive(dict(body))
    assert first.status_code == second.status_code == 200
    assert first.content == second.content, "identical request bodies produced different bytes"
