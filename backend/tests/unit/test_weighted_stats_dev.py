"""F0-S3 — developer supporting tests for ``rentcomp.stats.weighted``.

QA's suite (``test_weighted_stats.py``, QA-owned, not committed on this
branch) pins the [INVARIANT] contract with integer weights. This file covers
implementation edges outside that pinned surface:

* fractional (non-integer) weights — real callers pass floats;
* totals that are not powers of two (division rounding at the boundary);
* duplicate-value runs with unequal weights;
* precedence of the loud ValueError contract over the None path.
"""

from __future__ import annotations

import pytest

from rentcomp.stats.weighted import weighted_median, weighted_quantile


class TestFractionalWeights:
    def test_fractional_weights_median(self) -> None:
        # total = 2.0, half = 1.0; cum at 1800 is 0.5 < 1.0, cum at 2100 is 2.0.
        assert weighted_median([1800.0, 2100.0], [0.5, 1.5]) == 2100.0

    def test_fractional_weights_exact_half_boundary_qualifies(self) -> None:
        # cum at 1800 is exactly half the total (1.5 of 3.0) -> ">=" picks it.
        assert weighted_median([1800.0, 2100.0], [1.5, 1.5]) == 1800.0

    def test_fractional_scale_invariance(self) -> None:
        values = [1500.0, 1800.0, 2100.0, 2400.0]
        weights = [1.0, 3.0, 2.0, 1.0]
        scaled = [w * 0.25 for w in weights]  # exact in float64
        for q in (0.0, 0.3, 0.5, 0.7, 1.0):
            assert weighted_quantile(values, weights, q) == weighted_quantile(
                values, scaled, q
            )


class TestNonPowerOfTwoTotals:
    def test_total_three_thirds_boundaries(self) -> None:
        values, weights = [10.0, 20.0, 30.0], [1, 1, 1]
        # Just below/above the exact 1/3 and 2/3 cumulative fractions.
        assert weighted_quantile(values, weights, 0.33) == 10.0
        assert weighted_quantile(values, weights, 0.34) == 20.0
        assert weighted_quantile(values, weights, 0.66) == 20.0
        assert weighted_quantile(values, weights, 0.67) == 30.0

    def test_total_seven_median(self) -> None:
        # total = 7, half = 3.5; cum: 1 -> 4 -> 6 -> 7. First >= 3.5 is 1800.
        assert weighted_median([1500.0, 1800.0, 2100.0, 2400.0], [1, 3, 2, 1]) == 1800.0


class TestDuplicateValueRuns:
    def test_duplicate_values_with_unequal_weights(self) -> None:
        # Two separate 2000.0 entries; combined weight dominates.
        assert weighted_median([1500.0, 2000.0, 2000.0, 2500.0], [1, 2, 2, 1]) == 2000.0

    def test_q1_with_duplicated_maximum(self) -> None:
        assert weighted_quantile([2400.0, 2400.0, 1500.0], [1, 3, 2], 1.0) == 2400.0

    def test_negative_values(self) -> None:
        assert weighted_median([-30.0, -20.0, -10.0], [1, 1, 1]) == -20.0


class TestErrorPrecedenceOverNone:
    """Caller bugs stay loud even when the data half of the input is empty.

    The PM-confirmed rationale (bugs must not launder into None = "no
    evidence") implies validation runs before the None path; these pin that
    precedence so a refactor cannot silently reorder it.
    """

    def test_bad_q_raises_even_on_empty_input(self) -> None:
        with pytest.raises(ValueError):
            weighted_quantile([], [], 1.5)

    def test_bad_q_raises_even_on_all_zero_weights(self) -> None:
        with pytest.raises(ValueError):
            weighted_quantile([1500.0], [0], -0.5)

    def test_negative_weight_raises_even_if_other_weights_all_zero(self) -> None:
        with pytest.raises(ValueError):
            weighted_median([1500.0, 1800.0], [0, -1])

    def test_length_mismatch_raises_even_when_one_side_empty(self) -> None:
        with pytest.raises(ValueError):
            weighted_median([1500.0], [])

    def test_negative_zero_weight_is_excluded_not_an_error(self) -> None:
        # -0.0 is not a negative weight; it is weight zero and is excluded.
        assert weighted_median([9999.0, 1500.0], [-0.0, 1]) == 1500.0
        assert weighted_median([1500.0], [-0.0]) is None
