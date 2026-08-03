"""F5-S1 AC6 — `compute_sqft_suspects` as a stage, developer-authored.

QA's `test_f5s1_row_fields.py` owns what the flag *means* (deviation in either
direction, from the comp's OWN cohort, monotone, never `None`). This file owns
the things that are the stage's own rather than the flag's meaning, and that a
threshold change must not quietly break:

* the flag and `premium` are the SAME deviation, judged once — not two
  implementations of one distance that can drift apart. This is the property
  the whole design rests on (PM ruling R2), and it is the one a developer
  "optimising" the flag into its own median lookup would break first;
* the boundary is exclusive, so the threshold constant means what it says;
* the threshold is a parameter, so the owner's tuning is a one-line change and
  not a rewrite;
* the stage cannot see a comp record, and therefore cannot reach an outcome
  (D19a, ADR-001 §2.2 — the same isolation `compute_premiums` is held to).

No network, no pull on disk, no clock.
"""

from __future__ import annotations

import inspect

import pytest

from rentcomp.pipeline.premium import (
    SQFT_SUSPECT_THRESHOLD,
    compute_premiums,
    compute_sqft_suspects,
)


def test_the_flag_is_exactly_the_premium_the_row_displays_beside_it() -> None:
    """[INVARIANT] AC6, and the reason this stage is three lines long.

    `premium = psf / cohort_median − 1`, so "deviates >30% from the cohort
    median" IS `abs(premium) > 0.30` on that same median. The badge and the
    number beside it on the row must therefore agree by construction: a comp
    flagged while showing +4% would be reporting two different distances from
    the same median, and the user has no way to tell which one is the lie.
    """
    psfs = [1.0, 1.2, 1.3, 1.31, 2.0, 4.28, None, 0.5]
    years = [2024, 2024, 2025, 2025, 2025, 2025, 2025, 2024]
    medians = {2024: 1.0, 2025: 1.6672}

    premiums = compute_premiums(psfs, years, medians)
    flags = compute_sqft_suspects(psfs, years, medians)

    assert flags == [
        premium is not None and abs(premium) > SQFT_SUSPECT_THRESHOLD for premium in premiums
    ]


def test_the_boundary_is_exclusive_so_the_threshold_means_what_it_says() -> None:
    """A comp *exactly* `threshold` off its cohort median is NOT suspect —
    ">30% off" excludes 30% itself.

    The boundary is approached through the threshold rather than through the
    $/sqft, on purpose: `median * 1.30` is not exactly 30% away in float64
    (2.0 * 1.30 / 2.0 − 1 is 0.30000000000000004), so a test that built the
    comp on the line would be measuring binary floating point, not the rule.
    Taking the premium the stage itself computed and using its magnitude AS
    the threshold puts the comparison exactly on the boundary, by
    construction, at any scale.
    """
    psfs = [2.6, 1.4]
    years = [2025, 2025]
    medians = {2025: 2.0}
    premiums = compute_premiums(psfs, years, medians)
    assert all(premium is not None for premium in premiums)

    for psf, premium in zip(psfs, premiums):
        exact = abs(premium)  # type: ignore[arg-type]
        assert compute_sqft_suspects([psf], [2025], medians, threshold=exact) == [False], (
            f"$/sqft {psf} is exactly {exact} off the median and flagged — the rule is "
            "'>threshold', not '>='"
        )
        nudged = exact * (1.0 - 1e-9)
        assert compute_sqft_suspects([psf], [2025], medians, threshold=nudged) == [True]


def test_the_threshold_is_a_parameter_the_owner_can_tune() -> None:
    """`[DEFAULT]`, and the queue says so: the badge fires on ~19% of the real
    pull at 0.30 and the owner tunes it on sight. A hardcoded literal would
    make that a code change in the middle of a per-comp loop; a parameter makes
    it one number in one place, with every test above still meaningful."""
    psfs = [2.4]  # +20% — inside the default, outside a 0.10 threshold
    assert compute_sqft_suspects(psfs, [2025], {2025: 2.0}) == [False]
    assert compute_sqft_suspects(psfs, [2025], {2025: 2.0}, threshold=0.10) == [True]


def test_the_stage_cannot_see_a_comps_outcome() -> None:
    """D19a / ADR-001 §2.2, structurally — the same guard `compute_premiums`
    carries. A function that was never handed a comp record cannot leak an
    outcome into a flag the user curates by; it takes psfs, years and medians,
    and a threshold, and nothing else."""
    params = list(inspect.signature(compute_sqft_suspects).parameters)
    assert params == ["psfs", "cohort_years", "cohort_medians", "threshold"], params


def test_mismatched_inputs_are_an_error_not_a_silent_realignment() -> None:
    """Inherited from `compute_premiums`, and pinned here because this function
    is what `derive` zips against the comp list: a length mismatch that
    truncated instead of raising would attach one comp's suspicion to another
    comp's row."""
    with pytest.raises(ValueError):
        compute_sqft_suspects([1.0, 2.0], [2025], {2025: 2.0})
