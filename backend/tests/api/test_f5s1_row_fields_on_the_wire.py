"""F5-S1 — the row's server-computed fields, over HTTP.

QA-authored, written RED before the developer's branch exists (AGENT_QA.md
protocol step 3). Layer 2 by the decision procedure's second question: every
assertion below is phrasable as "given curation state X, `POST /api/derive`
returns Y", and every value asserted is a `DerivedState` field. Zero network
(D17) — `ws1-real` shapes the two committed T-S3 gate responses off disk and
the ledger cannot move.

WHAT THIS FILE OWNS THAT LAYER 1 CANNOT
---------------------------------------
`tests/unit/test_f5s1_row_fields.py` proves the RULES on plain values, and it
has to propose a function signature to do it. This file is signature-free: it
only ever asks the endpoint. Two things live here and nowhere else:

* **AC7 — the named real-data case.** `2350 S Leavitt St 1R` is a 3-bed,
  3-bath listed as **700 sqft** at $2,995: **$4.28/sqft, +156.7% against its
  own 2025 cohort**, the most extreme $/sqft in the whole pull. Row 9b admits
  it into the evidence deliberately (admitting it is 9b's job; JUDGING it is
  this story's), and `sqft_suspect` — the guard designed for exactly this — is
  hardcoded `False` until F5-S1 turns it on. **An implementation that does not
  flag this comp has not implemented the flag.** A synthetic fixture cannot
  stand in for it: the point is that the real pull the owner looks at contains
  it.
* **The flag against the SELECTED cohort median.** F4-S5 [INVARIANT] takes
  the cohort median over the selected comps, so it moves whenever the user
  toggles one — and the deviation this flag reports must move with it. Only an
  assembled derive can show that.

HOW THE ~30% THRESHOLD IS TESTED WITHOUT PINNING IT
---------------------------------------------------
The threshold is `[DEFAULT]` ("~30%"), so no assertion here equals 0.30.
Instead: over 485 real comps carrying a premium, the flagged set and the
unflagged set must be **separated** by |premium| — every flagged comp further
from its cohort median than every unflagged one. That is the invariant, and it
survives a developer choosing 0.28 or 0.33. The separation point is then
sanity-bounded well outside the ambiguity (a comp 60%+ off must flag; a comp
10% off must not), and the flagged share is bounded because a badge on
everything is a badge on nothing.

⚠ PREMIUM IS ALREADY THE DEVIATION. `pipeline/premium.py` computes
`premium = psf / cohort_median_psf − 1`, so "psf deviates >30% from the cohort
median" is `abs(premium) > 0.30` exactly, on the same median. That identity is
why this file can test the flag through `premium` without re-deriving
anything — and it is worth the developer knowing before writing a second
median lookup.

EXPECTED RED STATE TODAY: every test in this file fails.
"""

from __future__ import annotations

import pytest

from rentcomp.pipeline.keys import comp_key

REAL_PULL_REF = "ws1-real"

#: The comp this acceptance criterion is named after (AC7).
LEAVITT = comp_key("2350 S Leavitt St", "Unit 1R")
#: Measured on the real pull, 2026-08-03 (and by row 9b's QA, 2026-07-30).
LEAVITT_PSF = 4.2785714285714285
LEAVITT_PREMIUM = 1.5671428571428572
LEAVITT_SQFT = 700.0

#: Comps with a cut, and the day offsets their spells actually produced.
#: `1758 w 35th st|3f` — first listed 2024-07-22, cut 1700 → 1600 on 2024-10-01.
CUT_ONCE = "1758 w 35th st|3f"
CUT_ONCE_OFFSETS = [71]
#: `2453 w 46th pl|1` — the pull's only twice-cut comp; listed 2025-10-27,
#: cut 2025-12-25 and 2026-03-15.
CUT_TWICE = "2453 w 46th pl|1"
CUT_TWICE_OFFSETS = [59, 139]
#: How many comps in the real pull carry at least one cut.
REAL_COMPS_WITH_A_CUT = 28

#: Sanity bounds on the [DEFAULT] threshold — deliberately far from ~30% on
#: both sides, so a developer who picks 0.25 or 0.35 passes and one who picks
#: 0.05 or 0.90 does not.
CLEARLY_SUSPECT = 0.60
CLEARLY_ORDINARY = 0.10
#: A flag raised on most of the pull is not a flag.
MAX_SUSPECT_SHARE = 0.40

KNOWN_CUT_FIELDS = {"on", "from_price", "to_price"}

DAY_OFFSET_HINT = (
    "F5-S1 AC9: the wire `Cut` carries only on/from_price/to_price, so the row "
    "copy `✂ 2,150 → 2,050 (day 21)` has no day to render — and the view may not "
    "subtract two dates to get one (D5 forbids the derivation, D15 forbids the JS "
    "date arithmetic). Ship `(cut.on - first_listed).days` as `int | None`, the "
    "same call `days_since_removal` made."
)


def _by_key(state: dict) -> dict[str, dict]:
    return {comp["key"]: comp for comp in state["comps"]}


def _day_offset(cut: dict):
    """The day offset on a wire cut, whatever the developer named it."""
    extras = {key: value for key, value in cut.items() if key not in KNOWN_CUT_FIELDS}
    assert extras, f"{DAY_OFFSET_HINT} (fields present: {sorted(cut)})"
    assert len(extras) == 1, (
        f"expected exactly one new field on `Cut` carrying the day offset, found "
        f"{sorted(extras)} — the row cannot tell which one to print"
    )
    return next(iter(extras.values()))


@pytest.fixture
def real(derive):
    """`real(**overrides) -> DerivedState` against the committed real pull."""

    def _real(**overrides):
        response = derive(pull_ref=REAL_PULL_REF, **overrides)
        assert response.status_code == 200, response.text[:600]
        return response.json()

    return _real


# ---------------------------------------------------------------------------
# AC7 — the named real-data case
# ---------------------------------------------------------------------------


def test_the_leavitt_comp_is_still_the_datum_this_flag_exists_for(real) -> None:
    """A guard on the test below, not on the product.

    If this fails, the comp moved and AC7's expectation has to be re-measured
    rather than the flag being wrong. ⚠ The fix is never to exclude the comp:
    row 9b admitted it on purpose, and suppressing a real observation to avoid
    displaying it is the wrong direction.
    """
    comp = _by_key(real()).get(LEAVITT)
    assert comp is not None, f"{LEAVITT} is not in the real pull any more"
    assert comp["sqft"] == LEAVITT_SQFT
    assert comp["psf"] == pytest.approx(LEAVITT_PSF)
    assert comp["premium"] == pytest.approx(LEAVITT_PREMIUM)
    assert comp["beds"] == 3.0 and comp["baths"] == 3.0, (
        "a 3-bed/3-bath in 700 sqft is what makes this datum look wrong on its face"
    )


def test_the_leavitt_comp_raises_the_verify_sqft_flag(real) -> None:
    """AC7 [INVARIANT]. The whole story, in one assertion.

    $4.28/sqft against a 2025 cohort median of ~$1.67 — **+157%**. If the row
    the owner keeps looking at under a frozen cache does not carry a "verify
    sqft" flag, this feature does not exist, however elegant the rest of the
    row is.
    """
    comp = _by_key(real())[LEAVITT]
    assert comp["sqft_suspect"] is True, (
        f"{LEAVITT} is {comp['premium'] * 100:.1f}% off its own cohort's median $/sqft "
        f"(${comp['psf']:.2f}/sqft, a 3bd/3ba in {comp['sqft']:.0f} sqft) and is not "
        "flagged. This is the comp F5-S1 was scheduled for"
    )


def test_the_flag_is_not_raised_on_everything(real) -> None:
    """The other half of AC7: a badge on every row carries no information, and
    would train the owner to ignore the one row that matters."""
    comps = real()["comps"]
    with_premium = [comp for comp in comps if comp["premium"] is not None]
    suspect = [comp for comp in with_premium if comp["sqft_suspect"]]
    assert with_premium, "precondition: the real pull has comps carrying a premium"
    assert suspect, "no comp at all is flagged — see the Leavitt test above"
    share = len(suspect) / len(with_premium)
    assert share <= MAX_SUSPECT_SHARE, (
        f"{len(suspect)} of {len(with_premium)} comps ({share:.0%}) flagged as "
        "suspect — a flag that fires on most of the evidence is noise, not a warning"
    )


# ---------------------------------------------------------------------------
# AC6 — what the flag means, over the wire
# ---------------------------------------------------------------------------


def test_the_flag_separates_the_pull_by_distance_from_the_cohort_median(real) -> None:
    """AC6 [INVARIANT] without pinning the [DEFAULT] threshold.

    Over the whole real pull: every flagged comp must be further from its own
    cohort's median than every unflagged one. Whatever cutoff the developer
    picked, the two sets cannot interleave — if they do, the flag is being
    decided by something other than the deviation it claims to report.
    """
    comps = [comp for comp in real()["comps"] if comp["premium"] is not None]
    suspect = [abs(comp["premium"]) for comp in comps if comp["sqft_suspect"]]
    ordinary = [abs(comp["premium"]) for comp in comps if not comp["sqft_suspect"]]
    assert suspect and ordinary, (
        f"precondition: both sets non-empty (suspect={len(suspect)}, ordinary={len(ordinary)})"
    )
    assert min(suspect) > max(ordinary), (
        f"the flagged and unflagged sets interleave: the nearest flagged comp is "
        f"{min(suspect):.1%} off its cohort median while the furthest unflagged one is "
        f"{max(ordinary):.1%} off. `sqft_suspect` claims to be a deviation test"
    )


def test_the_threshold_sits_somewhere_reasonable(real) -> None:
    """The [DEFAULT] is "~30%", so this brackets it far outside the ambiguity:
    a comp 60%+ off its cohort median must flag, a comp within 10% must not.
    0.25 and 0.35 both pass; 0.05 and 0.90 do not."""
    comps = [comp for comp in real()["comps"] if comp["premium"] is not None]
    unflagged_far = [
        comp for comp in comps if abs(comp["premium"]) >= CLEARLY_SUSPECT and not comp["sqft_suspect"]
    ]
    flagged_near = [
        comp for comp in comps if abs(comp["premium"]) <= CLEARLY_ORDINARY and comp["sqft_suspect"]
    ]
    assert not unflagged_far, (
        f"{len(unflagged_far)} comp(s) are {CLEARLY_SUSPECT:.0%}+ off their cohort median "
        f"and unflagged, e.g. {unflagged_far[0]['key']} at {unflagged_far[0]['premium']:.1%}"
    )
    assert not flagged_near, (
        f"{len(flagged_near)} comp(s) within {CLEARLY_ORDINARY:.0%} of their cohort median "
        f"are flagged, e.g. {flagged_near[0]['key']} at {flagged_near[0]['premium']:.1%} — "
        "ordinary market variation is not a data-entry error"
    )


def test_a_comp_with_no_sqft_is_never_suspect(real, derived) -> None:
    """AC5/AC6 [INVARIANT], on both pulls.

    "We do not know this comp's size" and "this comp's size looks wrong" are
    two different statements with two different badges. The real pull has 82
    comps with no sqft; none of them has a $/sqft to doubt.
    """
    for label, state in (("ws1-real", real()), ("synthetic-basic", derived)):
        offenders = [
            comp["key"] for comp in state["comps"] if comp["psf"] is None and comp["sqft_suspect"]
        ]
        assert not offenders, (
            f"{label}: {len(offenders)} comp(s) with no $/sqft are flagged as having a "
            f"suspicious one: {offenders[:5]}"
        )


def test_the_flag_is_a_boolean_on_every_comp(real, derived) -> None:
    """`DerivedComp.sqft_suspect: bool`. Never `None` — the row branches on it
    directly, and a tri-state would put a third rendering case in the view."""
    for label, state in (("ws1-real", real()), ("synthetic-basic", derived)):
        offenders = [
            comp["key"] for comp in state["comps"] if not isinstance(comp["sqft_suspect"], bool)
        ]
        assert not offenders, f"{label}: non-boolean sqft_suspect on {offenders[:5]}"


def test_the_flag_follows_the_selection_the_median_was_taken_over(real) -> None:
    """AC6 [INVARIANT] — the one that says WHICH median.

    F4-S5 takes a cohort's median over the **selected** comps, so it moves as
    the user curates, and `premium` moves with it. `sqft_suspect` reports a
    deviation from that same median, so it has to move with it too. A flag
    computed once per pull in record shaping — where it is hardcoded today —
    would keep answering against a median nobody is looking at.

    Asserted as an identity rather than a direction: whatever the selection,
    the flagged set is still separated from the unflagged one by |premium|
    **computed in that same response**.
    """
    baseline = real()
    thinned = {
        comp["key"]: 0.0
        for index, comp in enumerate(baseline["comps"])
        if index % 2 == 0 and comp["state"] == "included"
    }
    assert thinned, "precondition: the baseline derive has included comps to drop"
    state = real(weights=thinned)
    comps = [comp for comp in state["comps"] if comp["premium"] is not None]
    suspect = [abs(comp["premium"]) for comp in comps if comp["sqft_suspect"]]
    ordinary = [abs(comp["premium"]) for comp in comps if not comp["sqft_suspect"]]
    assert suspect and ordinary, "precondition: both sets non-empty after thinning"
    assert min(suspect) > max(ordinary), (
        "after halving the selection the premiums moved (the cohort medians did) but "
        "the flags did not follow them — the flag is being computed against a median "
        "other than the one `premium` reports against"
    )


def test_the_provisional_field_warning_for_sqft_suspect_is_retired(real, derived) -> None:
    """The convention `test_ws1a_provisional_field_warnings.py` documents: the
    story that makes a placeholder real deletes its own warning. A
    `provisional_field` warning for a value that IS computed is a false
    warning, and false warnings are how the real ones stop being read.

    (That file's `EXPECTED_PROVISIONAL_FIELDS` is edited on this QA branch to
    match — the same move F4-S5's and F4-S9's QA made, flagged to the PM at
    handoff rather than done quietly.)
    """
    for label, state in (("ws1-real", real()), ("synthetic-basic", derived)):
        stale = [
            warning
            for warning in state["warnings"]
            if warning["code"] == "provisional_field" and "sqft_suspect" in warning["message"]
        ]
        assert not stale, (
            f"{label}: the payload still says sqft_suspect is provisional: {stale}"
        )


# ---------------------------------------------------------------------------
# AC9 — the cut-history line's day offset, over the wire
# ---------------------------------------------------------------------------


def test_a_real_cut_carries_the_day_of_the_spell_it_landed_on(real) -> None:
    """AC9. `1758 w 35th st|3f`: first listed 2024-07-22, cut $1,700 → $1,600
    on 2024-10-01. The row must be able to print `✂ 1,700 → 1,600 (day 71)`
    without the browser subtracting two dates."""
    comp = _by_key(real())[CUT_ONCE]
    assert [_day_offset(cut) for cut in comp["cut_history"]] == CUT_ONCE_OFFSETS
    (cut,) = comp["cut_history"]
    assert cut["from_price"] == 1700.0 and cut["to_price"] == 1600.0


def test_a_twice_cut_comp_carries_both_days_in_order(real) -> None:
    """`2453 w 46th pl|1`, the pull's only comp with two cuts."""
    comp = _by_key(real())[CUT_TWICE]
    assert [_day_offset(cut) for cut in comp["cut_history"]] == CUT_TWICE_OFFSETS


def test_every_cut_on_the_real_pull_knows_its_day(real) -> None:
    """`shape_raw_pull` always sets `first_listed`, so on real evidence there
    is no cut whose day is unknowable — the `None` case exists for hand-built
    and pre-shaped comps (row 10b's shape), not for anything the owner sees.
    Pinning it here is what makes a regression to `None` visible."""
    comps = [comp for comp in real()["comps"] if comp["cut_history"]]
    assert len(comps) == REAL_COMPS_WITH_A_CUT, (
        f"expected {REAL_COMPS_WITH_A_CUT} comps with a cut, got {len(comps)} — "
        "re-measure before changing this number"
    )
    missing = [
        comp["key"] for comp in comps if any(_day_offset(cut) is None for cut in comp["cut_history"])
    ]
    assert not missing, f"cuts with no day on real evidence: {missing}"


def test_a_cut_day_lies_inside_the_spell_it_belongs_to(real) -> None:
    """A cut cannot land before the unit was listed, and cannot land after the
    unit left the market. `0 <= day <= effective_dom` is the honesty check on
    the subtraction: an offset measured from the wrong anchor breaks it."""
    for comp in real()["comps"]:
        for cut in comp["cut_history"]:
            day = _day_offset(cut)
            assert day is not None
            assert 0 <= day <= comp["effective_dom"], (
                f"{comp['key']}: cut on {cut['on']} reports day {day}, outside the "
                f"comp's own {comp['effective_dom']}-day spell"
            )


def test_the_cut_keeps_the_three_fields_the_row_already_prints(real) -> None:
    """The day is an addition, not a replacement: `✂ 1,700 → 1,600 (day 71)`
    needs all four."""
    comp = _by_key(real())[CUT_ONCE]
    (cut,) = comp["cut_history"]
    assert set(cut) >= KNOWN_CUT_FIELDS, cut
    assert cut["to_price"] < cut["from_price"], "a cut is a reduction, by definition"
