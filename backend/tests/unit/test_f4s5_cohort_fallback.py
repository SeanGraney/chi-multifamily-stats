"""F4-S5 [INVARIANT] — the thin-cohort fallback, at Layer 1.

Developer-authored (AGENT_DEVELOPER.md step 4: "all of Layer 1"). QA owns this
story's Layer 2 — `tests/api/test_f4s5_premium_basis.py` walks the same
boundary over `POST /api/derive`, and `test_f4s5_real_pull_boundary.py` walks
it on the committed real pull. Nothing here duplicates those; what it adds is
the two things the endpoint cannot show:

1. **Composition.** `cohort_medians -> median_by_year -> compute_premiums` is
   the story's formula spelled out as three calls. Over HTTP that chain is one
   number and a wrong link is only visible by inference.
2. **Properties over generated inputs** (`hypothesis`, per CLAUDE.md and the
   `add-pipeline-stage` skill: "if the stage has a threshold, add a property
   test — this pipeline has a history of edge-case bugs living exactly at
   stage boundaries"). The boundary cases below prove the rule at chosen
   counts; the properties state it as a biconditional, so an implementation
   cannot pass by being right at the three counts someone thought to check.

THE FALLBACK'S WEIGHTING IS A `[DEFAULT]`, AND IT IS PINNED HERE
---------------------------------------------------------------
The story switches the *set*, not the weighting, and says nothing about
weights. PM ruling (2026-07-29): in the fallback, **every pulled comp counts
once**. `test_the_fallback_median_ignores_the_users_weights_entirely` and its
property twin are what stop that ruling from expiring silently — reusing the
effective weights would make the fallback a literal no-op precisely when a
cohort is thin *because* comps were excluded.

WHAT IS DELIBERATELY NOT ASSERTED HERE
--------------------------------------
Nothing bounds a one-comp cohort's premium (QUEUE row 11a). A 1-comp cohort's
weighted median *is* that comp, so its premium is 0 by construction, and the
fallback cannot fix that — selected -> pulled is a no-op at n=1. What to do
about it is a different story's decision; this file only pins that the counts
explaining it travel in the same payload.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from rentcomp.pipeline.cohorts import basis_by_year, cohort_medians, median_by_year
from rentcomp.pipeline.premium import compute_premiums

#: One cohort of five comps at $/sqft 1.00 .. 5.00, chosen so the three arms of
#: the boundary return three *distinguishable* lower weighted medians:
#:
#:     5 selected -> 3.00 (selected)   4 selected -> 2.00 (selected)
#:     3 selected -> 3.00 (PULLED, since the selected-only median is 2.00)
KEYS = ["a", "b", "c", "d", "e"]
PSFS: list[float | None] = [1.0, 2.0, 3.0, 4.0, 5.0]
YEARS = [2026] * 5
MIN = 4


def selecting(*deselected: str) -> tuple[list[float], list[bool]]:
    """Weights and inclusion flags that select every key but `deselected`."""
    weights = [0.0 if key in deselected else 1.0 for key in KEYS]
    included = [key not in deselected for key in KEYS]
    return weights, included


def only(stats):
    assert len(stats) == 1, f"expected exactly one cohort, got {[s.year for s in stats]}"
    return stats[0]


# ---------------------------------------------------------------------------
# the boundary, walked at Layer 1
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("deselected", "selected_count", "basis", "median"),
    [
        pytest.param((), 5, "selected", 3.0, id="above-the-minimum"),
        pytest.param(("e",), 4, "selected", 2.0, id="exactly-at-the-minimum"),
        pytest.param(("e", "d"), 3, "pulled", 3.0, id="below-the-minimum"),
        pytest.param(("e", "d", "c", "b", "a"), 0, "pulled", 3.0, id="nothing-selected"),
    ],
)
def test_the_min_cohort_boundary_decides_which_set_the_median_comes_from(
    deselected, selected_count, basis, median
) -> None:
    """`< min`, never `<= min`: a cohort holding exactly `min_cohort_size`
    selected comps is *at* the minimum and does not fall back.

    The three medians are deliberately distinguishable, so no arm can pass by
    computing the other set — a `<=` off-by-one returns 3.00 where 2.00 is
    correct, and a missing fallback returns 2.00 where 3.00 is correct.
    """
    weights, included = selecting(*deselected)
    stat = only(cohort_medians(KEYS, PSFS, YEARS, weights, included, MIN))
    assert stat.selected_count == selected_count
    assert stat.pulled_count == 5, "a deselected comp is still pulled evidence"
    assert stat.basis == basis
    assert stat.thin is (basis == "pulled")
    assert stat.median_psf == pytest.approx(median)


def test_the_fallback_set_is_all_pulled_comps_including_the_excluded_ones() -> None:
    """"All *pulled* comps" is what separates a real fallback from a no-op.

    Three comps at 1/2/3, one excluded: the selected-only median is 1.00 and
    the fallback's is 2.00.
    """
    stat = only(
        cohort_medians(
            ["a", "b", "c"], [1.0, 2.0, 3.0], [2026] * 3, [1.0, 1.0, 0.0],
            [True, True, False], MIN,
        )
    )
    assert stat.basis == "pulled"
    assert stat.median_psf == pytest.approx(2.0)


def test_the_fallback_median_ignores_the_users_weights_entirely() -> None:
    """[DEFAULT, PM ruling] every pulled comp counts once.

    A weight of 100 on the cheapest selected comp would drag a weighted median
    down to 1.00. It does not, because the fallback exists precisely *because*
    this user's selection is insufficient evidence — importing that same
    user's weights into the replacement set would be self-contradictory.
    """
    heavy = only(
        cohort_medians(KEYS, PSFS, YEARS, [100.0, 1.0, 1.0, 0.0, 0.0],
                       [True, True, True, False, False], MIN)
    )
    flat = only(cohort_medians(KEYS, PSFS, YEARS, *selecting("d", "e"), MIN))
    assert heavy.basis == flat.basis == "pulled"
    assert heavy.median_psf == flat.median_psf == pytest.approx(3.0)


def test_each_cohort_decides_its_own_basis() -> None:
    """The flag is per cohort, not per response — one year can fall back while
    the year beside it does not, and a single global "something fell back"
    boolean cannot express that."""
    keys = [*KEYS, "x", "y"]
    stats = cohort_medians(
        keys,
        [*PSFS, 9.0, 11.0],
        [*YEARS, 2025, 2025],
        [1.0] * 7,
        [True] * 7,
        MIN,
    )
    by_year = {stat.year: stat for stat in stats}
    assert by_year[2026].basis == "selected" and by_year[2026].thin is False
    assert by_year[2025].basis == "pulled" and by_year[2025].thin is True


# ---------------------------------------------------------------------------
# missing sqft is out of EVERY median, the fallback's included
# ---------------------------------------------------------------------------


def test_a_comp_with_no_psf_is_absent_from_the_fallback_set_too() -> None:
    """Obligation 5, on the arm that only exists once the fallback does.

    The no-psf comp asks a wildly different rent, so a leak would be loud: it
    would move the median off 1.00 and inflate `pulled_count` past the two
    comps that actually carry a $/sqft.
    """
    stat = only(
        cohort_medians(
            ["a", "b", "n"], [1.0, 3.0, None], [2026] * 3, [1.0, 1.0, 5.0],
            [True, True, True], MIN,
        )
    )
    assert stat.pulled_count == 2, "a comp with no $/sqft is not $/sqft evidence"
    assert stat.basis == "pulled"
    assert stat.median_psf == pytest.approx(1.0)
    assert "n" not in stat.comp_keys


def test_a_cohort_with_no_psf_at_all_claims_no_basis() -> None:
    """A basis is a claim about where a number came from, so a cohort with no
    number makes no claim — and the year never reaches the premium stage."""
    stats = cohort_medians(["n"], [None], [2026], [1.0], [True], MIN)
    assert stats[0].median_psf is None and stats[0].basis is None
    assert stats[0].thin is True, "no selected evidence is as thin as a cohort gets"
    assert median_by_year(stats) == {} and basis_by_year(stats) == {}


# ---------------------------------------------------------------------------
# composition — the story's formula as three calls
# ---------------------------------------------------------------------------


def test_a_premium_over_a_fallback_median_uses_the_same_formula() -> None:
    """`premium = psf / cohort median psf - 1`, unchanged by which set the
    median came from. The premium stage is handed a number, not a provenance —
    which is why its signature never had to grow (D19a, ADR-001 §2.2)."""
    stats = cohort_medians(KEYS, PSFS, YEARS, *selecting("d", "e"), MIN)
    premiums = compute_premiums(PSFS, YEARS, median_by_year(stats))
    assert premiums == pytest.approx([psf / 3.0 - 1.0 for psf in PSFS])
    assert premiums[2] == pytest.approx(0.0), "the comp that IS the median sits at 0"


def test_an_excluded_comp_keeps_a_premium_against_its_cohorts_median() -> None:
    """An excluded comp keeps its row, so it keeps its number — measured
    against the same median as every other comp in its cohort, never a
    private one."""
    stats = cohort_medians(KEYS, PSFS, YEARS, *selecting("d", "e"), MIN)
    premiums = compute_premiums(PSFS, YEARS, median_by_year(stats))
    assert premiums[4] == pytest.approx(5.0 / 3.0 - 1.0)


def test_the_two_year_keyed_mappings_agree_on_which_years_exist() -> None:
    """`premium_basis` is read from `basis_by_year` and `premium` from
    `median_by_year`. If those could be keyed differently, a comp could get a
    premium with no basis (or the reverse) — the half-state
    `test_premium_basis_is_never_null_where_a_premium_exists` forbids."""
    stats = cohort_medians(
        [*KEYS, "n", "z"],
        [*PSFS, None, 8.0],
        [*YEARS, 2024, 2025],
        [1.0] * 7,
        [True] * 7,
        MIN,
    )
    assert median_by_year(stats).keys() == basis_by_year(stats).keys()
    assert 2024 not in basis_by_year(stats), "a cohort with no median names no set"


# ---------------------------------------------------------------------------
# properties (hypothesis) — the threshold as a biconditional
# ---------------------------------------------------------------------------

_PSF = st.floats(min_value=0.5, max_value=20.0, allow_nan=False, allow_infinity=False)
_WEIGHT = st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)


@st.composite
def _cohort(draw):
    """A single-year cohort: psfs (some possibly absent), weights, inclusion."""
    size = draw(st.integers(min_value=1, max_value=8))
    psfs = draw(st.lists(st.one_of(_PSF, st.none()), min_size=size, max_size=size))
    weights = draw(st.lists(st.one_of(st.just(0.0), _WEIGHT), min_size=size, max_size=size))
    included = draw(st.lists(st.booleans(), min_size=size, max_size=size))
    keys = [f"k{i}" for i in range(size)]
    return keys, psfs, weights, included


@settings(max_examples=300)
@given(cohort=_cohort(), minimum=st.integers(min_value=2, max_value=10))
def test_property_thin_and_the_basis_are_one_fact(cohort, minimum) -> None:
    """`basis == "pulled"` **iff** the cohort is thin, wherever a median
    exists — stated as a biconditional so neither a flag that latches on nor a
    fallback that fires without saying so can survive.

    `minimum` ranges over the whole `min_cohort_size` knob (spec §2.3, 2-10),
    so nothing here can pass by hardcoding 4.
    """
    keys, psfs, weights, included = cohort
    stat = only(cohort_medians(keys, psfs, [2026] * len(keys), weights, included, minimum))
    assert stat.thin is (stat.selected_count < minimum)
    if stat.median_psf is None:
        assert stat.basis is None
        return
    assert (stat.basis == "pulled") is stat.thin


@settings(max_examples=300)
@given(cohort=_cohort(), minimum=st.integers(min_value=2, max_value=10))
def test_property_the_median_is_always_some_comps_own_observed_psf(cohort, minimum) -> None:
    """NORTH_STAR, as a property: the divisor is real evidence.

    True by construction for the locked (non-interpolating, lower) weighted
    median over either set, and false the moment the divisor becomes a mean, an
    interpolated quantile, or a vendor's estimate.
    """
    keys, psfs, weights, included = cohort
    stat = only(cohort_medians(keys, psfs, [2026] * len(keys), weights, included, minimum))
    if stat.median_psf is None:
        return
    assert stat.median_psf in [psf for psf in psfs if psf is not None]


@settings(max_examples=300)
@given(cohort=_cohort(), minimum=st.integers(min_value=2, max_value=10))
def test_property_counts_bound_each_other_and_match_the_evidence_list(
    cohort, minimum
) -> None:
    """`selected_count == len(comp_keys) <= pulled_count`, always.

    The first half is what stops a count and its click-through from drifting
    apart; the second is what stops the fallback set from ever being *smaller*
    than the set it replaced, which would make it a strictly worse median.
    """
    keys, psfs, weights, included = cohort
    stat = only(cohort_medians(keys, psfs, [2026] * len(keys), weights, included, minimum))
    assert stat.selected_count == len(stat.comp_keys) <= stat.pulled_count
    assert set(stat.comp_keys) <= set(keys)


@settings(max_examples=300)
@given(
    psfs=st.lists(_PSF, min_size=1, max_size=8),
    weights=st.lists(_WEIGHT, min_size=1, max_size=8),
    other=st.lists(_WEIGHT, min_size=1, max_size=8),
)
def test_property_a_thin_cohorts_median_does_not_depend_on_the_users_weights(
    psfs, weights, other
) -> None:
    """[DEFAULT, PM ruling] as a property: below the minimum, the median is a
    function of the pulled $/sqft alone.

    Every comp is selected at a positive weight in both runs, so the *set* is
    identical and only the weights differ; `minimum` is above the cohort size,
    so both runs are thin. A fallback that re-weighted the pulled set by the
    user's weights would move the median between the two runs.
    """
    size = min(len(psfs), len(weights), len(other))
    psfs, weights, other = psfs[:size], weights[:size], other[:size]
    assume(size >= 1)
    keys = [f"k{i}" for i in range(size)]
    years = [2026] * size
    minimum = size + 1  # every comp selected, still below the minimum

    first = only(cohort_medians(keys, psfs, years, weights, [True] * size, minimum))
    second = only(cohort_medians(keys, psfs, years, other, [True] * size, minimum))
    assert first.basis == second.basis == "pulled"
    assert first.median_psf == second.median_psf
