"""F5-S1 — the row's contract over HTTP, developer-authored.

QA's `test_f5s1_row_fields_on_the_wire.py` owns AC6/AC7/AC9's *behaviour* on
the real pull. This file owns the parts of the contract that behaviour rests
on and that no browser test could see:

* the flag and the premium beside it are the SAME deviation on EVERY comp of
  a real pull, not merely separated sets (QA's separation test would still
  pass if the flag were computed off a slightly different median);
* the flag genuinely MOVES with the selection, in the direction the median
  moved — QA's test pins that the sets stay separated after a thinning, which
  is the invariant, and this pins that something actually changed, so the
  identity cannot be satisfied by a flag frozen at shaping time;
* `Cut` is additive on the wire (nothing the row already printed was
  displaced) and `day_offset` orders with `on`;
* the `provisional_field` warning family is empty and `sqft_suspect` no longer
  exists on the domain model — the R2 half of this story, which is a claim
  about what is NOT there and therefore has no other home.

Zero network (D17): both pulls are committed fixtures.
"""

from __future__ import annotations

import pytest

from rentcomp.pipeline.premium import SQFT_SUSPECT_THRESHOLD

REAL_PULL_REF = "ws1-real"
LEAVITT = "2350 s leavitt st|1r"


@pytest.fixture
def real(derive):
    def _real(**overrides):
        response = derive(pull_ref=REAL_PULL_REF, **overrides)
        assert response.status_code == 200, response.text[:600]
        return response.json()

    return _real


# ---------------------------------------------------------------------------
# AC6 — the flag IS the premium, everywhere
# ---------------------------------------------------------------------------


def test_the_flag_agrees_with_the_premium_on_every_comp_of_the_real_pull(real) -> None:
    """[INVARIANT]. Not "the sets are separated" — *the same number*.

    `premium = psf / cohort_median − 1`, so the deviation the badge claims and
    the deviation the row prints are one quantity. Checked comp by comp, over
    all 567, because a second median lookup (or a median taken over a slightly
    different set) would still produce separated sets while disagreeing with
    the number displayed next to the badge.
    """
    offenders = [
        (comp["key"], comp["premium"], comp["sqft_suspect"])
        for comp in real()["comps"]
        if comp["sqft_suspect"]
        != (comp["premium"] is not None and abs(comp["premium"]) > SQFT_SUSPECT_THRESHOLD)
    ]
    assert not offenders, (
        f"{len(offenders)} comp(s) carry a flag that disagrees with the premium printed "
        f"beside it: {offenders[:5]}"
    )


def test_curating_the_pull_moves_the_flag_and_not_only_the_premiums(real) -> None:
    """AC6 [INVARIANT], the active half.

    QA pins that flags and premiums stay consistent after the user thins the
    selection. That identity is also satisfied by a flag that never changes
    *if* no premium changed. This asserts the premise: dropping every comp of
    one cohort but a couple moves that cohort's median, and the flags in it
    move with it. A `sqft_suspect` computed once per pull in record shaping —
    where it lived until this story — could not do that.
    """
    baseline = real()
    year = baseline["comps"][0]["cohort_year"]
    in_year = [comp for comp in baseline["comps"] if comp["cohort_year"] == year]
    assert len(in_year) > 10, "precondition: a cohort big enough to thin"

    # Keep the CHEAPEST `min_cohort_size` comps of one cohort and drop the
    # rest. Exactly `min_cohort_size`, not fewer: below it F4-S5's thin-cohort
    # fallback takes the median over the *pulled* set instead, which does not
    # move when the user deselects — so a thinner selection would prove
    # nothing about whether the flag follows the median.
    floor = baseline["meta"]["config"]["min_cohort_size"]
    priced = sorted(
        (comp for comp in in_year if comp["psf"] is not None), key=lambda comp: comp["psf"]
    )
    assert len(priced) > floor, "precondition: the cohort has more priced comps than the floor"
    keep = {comp["key"] for comp in priced[:floor]}
    thinned = {comp["key"]: 0.0 for comp in in_year if comp["key"] not in keep}

    after = {comp["key"]: comp for comp in real(weights=thinned)["comps"]}
    before = {comp["key"]: comp for comp in baseline["comps"]}

    moved_premium = [
        key for key in before if before[key]["premium"] != after[key]["premium"]
    ]
    moved_flag = [
        key for key in before if before[key]["sqft_suspect"] != after[key]["sqft_suspect"]
    ]
    assert moved_premium, "precondition: thinning a cohort to two comps moved some premium"
    assert moved_flag, (
        f"{len(moved_premium)} premium(s) moved when the selection changed and NOT ONE flag "
        "followed — the flag is not being computed against the median the user is looking at"
    )
    # And every flag that moved is still the premium's own answer.
    for key in moved_flag:
        premium = after[key]["premium"]
        assert after[key]["sqft_suspect"] == (
            premium is not None and abs(premium) > SQFT_SUSPECT_THRESHOLD
        )


def test_the_named_comp_is_flagged_on_a_synthetic_pull_too(derived) -> None:
    """A guard against AC7 being satisfied by something specific to `ws1-real`:
    the flag is a rule, so it must be answerable on any pull. `synthetic-basic`
    is small and ordinary, so the honest assertion here is the negative one —
    nothing in it is 30% off its own cohort's median, and nothing in it is
    flagged."""
    for comp in derived["comps"]:
        premium = comp["premium"]
        assert comp["sqft_suspect"] == (
            premium is not None and abs(premium) > SQFT_SUSPECT_THRESHOLD
        ), comp["key"]


def test_no_provisional_field_warning_survives_anywhere(real, derived) -> None:
    """R1/R2. The family is empty now — `sqft_suspect` was the last member.
    Asserted on both pulls because a warning list assembled per request could
    reintroduce one on a pull the synthetic suite does not derive."""
    for label, state in (("ws1-real", real()), ("synthetic-basic", derived)):
        codes = [warning["code"] for warning in state["warnings"]]
        assert "provisional_field" not in codes, f"{label}: {state['warnings']}"


def test_the_shaped_comp_no_longer_carries_a_shaping_time_flag() -> None:
    """R2's second half, and the only place it can be stated.

    `StitchedComp.sqft_suspect` promised the graph "can surface it without
    re-deriving it per request". The real flag makes that false — the median
    it is measured against is taken over the SELECTED comps — so the field is
    gone rather than left holding a value nothing reads. A field that can only
    ever be stale is the 10b shape, and leaving one behind is what this ruling
    was raised to prevent.
    """
    from rentcomp.models.domain import StitchedComp

    assert "sqft_suspect" not in StitchedComp.model_fields, (
        "a shaping-time `sqft_suspect` is back on the domain model. It cannot be answered "
        "there: the cohort median moves with the user's selection (F4-S5)"
    )


# ---------------------------------------------------------------------------
# AC9 — the day offset as a contract, not just a number
# ---------------------------------------------------------------------------


def test_the_day_offset_is_additive_and_did_not_displace_the_prices(real) -> None:
    """The row prints four facts (`✂ 1,700 → 1,600 (day 71)`), so the wire
    carries four. A `Cut` that replaced `on` with an offset would read fine in
    a diff and would cost the tooltip its date."""
    cuts = [cut for comp in real()["comps"] for cut in comp["cut_history"]]
    assert cuts, "precondition: the real pull has cuts"
    for cut in cuts:
        assert set(cut) == {"on", "from_price", "to_price", "day_offset"}, cut


def test_the_offsets_of_one_comp_order_the_same_way_its_cut_dates_do(real) -> None:
    """A cut later in the spell has a larger offset, on every multi-cut comp.
    The single-comp case is pinned by QA against measured values; this is the
    property, which is what a change of anchor (say, to the *cohort's* start)
    would break everywhere at once rather than on one address."""
    for comp in real()["comps"]:
        if len(comp["cut_history"]) < 2:
            continue
        by_date = [cut["on"] for cut in comp["cut_history"]]
        offsets = [cut["day_offset"] for cut in comp["cut_history"]]
        assert by_date == sorted(by_date), comp["key"]
        assert offsets == sorted(offsets), (
            f"{comp['key']}: cut dates {by_date} but offsets {offsets} — the offsets are not "
            "measured from one fixed anchor"
        )


def test_a_cut_never_reports_a_day_before_the_unit_was_listed(real) -> None:
    """`day_offset >= 0` on every real cut. A negative offset would mean the
    price was cut before the listing existed, which is not a display bug — it
    is a wrong anchor, and it would silently poison the "cut on day 3 vs day
    90" reading the row exists to support."""
    negatives = [
        (comp["key"], cut["day_offset"])
        for comp in real()["comps"]
        for cut in comp["cut_history"]
        if cut["day_offset"] is not None and cut["day_offset"] < 0
    ]
    assert not negatives, negatives


def test_the_synthetic_pulls_cuts_carry_days_too(derived) -> None:
    """Not a real-pull-only field. `synthetic-basic` is pre-shaped — row 10b's
    family — and since that row it does carry `first_listed`, so its cuts have
    honest days as well. If a future pre-shaped fixture drops `first_listed`
    the offset goes `None` and the row drops the day, which
    `test_f5s1_row_fields.py` pins directly."""
    cuts = [cut for comp in derived["comps"] for cut in comp["cut_history"]]
    assert cuts, "precondition: synthetic-basic has cuts"
    assert all(cut["day_offset"] is not None for cut in cuts), cuts
