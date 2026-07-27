"""F0-S3 [INVARIANT] — weighted statistics module. QA-authored, written RED
before any implementation exists (AGENT_QA.md protocol; dispatch routed all
of this story's ACs to L1 because every assertion is a direct call on a pure
function).

Import target (ARCHITECTURE.md section 2): ``rentcomp.stats.weighted``
(backend/src/rentcomp/stats/weighted.py).

Contract these tests pin (the locked [INVARIANT] parts):

* ``weighted_median(values, weights)`` — the LOWER weighted median: the
  smallest value v (over entries with weight > 0) such that the cumulative
  weight of all entries with value <= v is >= 50% of total weight. Never the
  upper variant, never interpolated. The result is always an element of the
  positive-weight input values.
* ``weighted_quantile(values, weights, q)`` — same convention generalized:
  smallest v whose cumulative weight >= q * total weight. Consequences this
  file pins: q=0.5 agrees exactly with ``weighted_median``; q=0 returns the
  minimum positive-weight value; q=1 returns the maximum positive-weight
  value; output is always an element of the input, no interpolation ever;
  nondecreasing in q.
* Weight-0 entries are excluded BEFORE computation — a zero-weight outlier
  must not shift any quantile, including q=0 and q=1.
* Empty input, or input whose weights are all zero, returns ``None`` — never
  NaN, never a raise ("returns null" AC).

QA-PROPOSED contract (NOT in the story text — dev should implement these,
but the PM/owner must confirm before they are treated as locked; see the
``TestProposedContract`` class):

* Any negative weight -> ``ValueError`` (a negative weight has no meaning in
  this system; silently clamping or excluding would hide caller bugs).
* ``q`` outside [0, 1] -> ``ValueError``.
* ``len(values) != len(weights)`` -> ``ValueError``.

Expected red state today: ``test_module_imports_and_exports`` FAILS with
ModuleNotFoundError detail; every other test SKIPS until the module exists,
then runs for real.
"""

from __future__ import annotations

import math
import statistics

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

try:
    from rentcomp.stats.weighted import weighted_median, weighted_quantile

    _IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover - red state before F0-S3 lands
    _IMPORT_ERROR = _e

needs_module = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.stats.weighted not importable yet (F0-S3 red): {_IMPORT_ERROR}",
)


def test_module_imports_and_exports() -> None:
    """Legible red: the module and both functions must exist at the section-2 path."""
    assert _IMPORT_ERROR is None, (
        "Cannot import weighted_median/weighted_quantile from "
        f"rentcomp.stats.weighted (ARCHITECTURE.md section 2 path): {_IMPORT_ERROR}"
    )
    assert callable(weighted_median)
    assert callable(weighted_quantile)


# ---------------------------------------------------------------------------
# Strategies (integer weights keep every cumulative-sum comparison exact in
# float64, so these tests pin the definition, not float-rounding accidents)
# ---------------------------------------------------------------------------

finite_values = st.floats(
    allow_nan=False, allow_infinity=False, min_value=-1e12, max_value=1e12
)
positive_int_weights = st.integers(min_value=1, max_value=1000)
nonneg_int_weights = st.integers(min_value=0, max_value=1000)
quantiles = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


# ---------------------------------------------------------------------------
# AC1 + locked definition: uniform weights == plain median (lower convention)
# ---------------------------------------------------------------------------


@needs_module
class TestLowerMedianDefinition:
    def test_ac1_uniform_weights_equal_plain_median_odd_n(self) -> None:
        # Odd n: every median convention agrees, so "plain median" is unambiguous.
        values = [2100.0, 1800.0, 2400.0, 2000.0, 2250.0]
        assert weighted_median(values, [1, 1, 1, 1, 1]) == statistics.median(values)

    def test_ac1_uniform_weights_equal_plain_median_even_n_is_lower(self) -> None:
        # Even n: the locked LOWER convention means "plain median" must be read
        # as statistics.median_low, NOT the interpolated statistics.median —
        # the interpolated value 2.5 is not an element of the input at all.
        assert weighted_median([1.0, 2.0, 3.0, 4.0], [1, 1, 1, 1]) == 2.0

    def test_lower_not_upper_not_interpolated(self) -> None:
        # Discriminating case: lower=2, upper=3, interpolated=2.5 — three
        # different statistics. The invariant locks exactly the lower one.
        result = weighted_median([1.0, 2.0, 3.0, 4.0], [1, 1, 1, 1])
        assert result == 2.0
        assert result != 3.0  # upper weighted median — a different statistic
        assert result != 2.5  # interpolated median — a different statistic

    def test_even_split_two_values_takes_lower(self) -> None:
        # 50/50 weight split: cumulative weight at the lower value is exactly
        # 50% of total, and >= means it qualifies. Lower=10, upper=20, interp=15.
        assert weighted_median([10.0, 20.0], [1, 1]) == 10.0

    def test_cumulative_weight_exactly_half_qualifies(self) -> None:
        # ">= 50%", not "> 50%": weights [2, 2], cum at 1.0 is exactly half.
        assert weighted_median([1.0, 2.0], [2, 2]) == 1.0

    def test_input_order_is_irrelevant(self) -> None:
        assert weighted_median([3.0, 1.0, 2.0], [1, 1, 1]) == 2.0

    def test_single_element(self) -> None:
        assert weighted_median([1725.0], [7]) == 1725.0

    def test_weighted_majority_pulls_median(self) -> None:
        # Story-domain case: total weight 4, need cum >= 2; 2100 only reaches 1.
        assert weighted_median([2100.0, 2200.0], [1, 3]) == 2200.0


# ---------------------------------------------------------------------------
# AC2: a weight-3 comp equals three weight-1 duplicates
# ---------------------------------------------------------------------------


@needs_module
class TestWeightDuplicateEquivalence:
    def test_ac2_weight3_equals_three_weight1_duplicates(self) -> None:
        assert weighted_median([2100.0, 2200.0], [1, 3]) == weighted_median(
            [2100.0, 2200.0, 2200.0, 2200.0], [1, 1, 1, 1]
        )

    def test_ac2_holds_for_quantiles_too(self) -> None:
        for q in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert weighted_quantile([2100.0, 2200.0], [1, 3], q) == weighted_quantile(
                [2100.0, 2200.0, 2200.0, 2200.0], [1, 1, 1, 1], q
            ), f"q={q}"


# ---------------------------------------------------------------------------
# AC3: empty / all-zero-weight input returns None — never NaN, never raises
# ---------------------------------------------------------------------------


@needs_module
class TestNullContract:
    def test_ac3_empty_input_returns_none(self) -> None:
        assert weighted_median([], []) is None
        assert weighted_quantile([], [], 0.5) is None

    def test_ac3_all_zero_weights_return_none(self) -> None:
        assert weighted_median([2100.0, 2200.0], [0, 0]) is None
        assert weighted_quantile([2100.0, 2200.0], [0, 0], 0.5) is None

    def test_ac3_never_nan(self) -> None:
        # `is None` above already excludes NaN; this pins the distinction
        # explicitly so a NaN-returning implementation fails with a clear name.
        for result in (
            weighted_median([], []),
            weighted_median([1.0, 2.0], [0, 0]),
            weighted_quantile([], [], 0.0),
            weighted_quantile([5.0], [0], 1.0),
        ):
            assert result is None
            assert not isinstance(result, float) or not math.isnan(result)


# ---------------------------------------------------------------------------
# Weight-0 exclusion happens BEFORE computation
# ---------------------------------------------------------------------------


@needs_module
class TestZeroWeightExclusion:
    def test_zero_weight_outlier_shifts_no_quantile(self) -> None:
        # Outliers below the min and above the max, both weight 0. If they are
        # merely weighted zero but left in the array (instead of excluded
        # before computation), q=0 returns -999 (cumulative weight 0 >= 0)
        # — this is the case that tells exclusion apart from zero-weighting.
        clean_v, clean_w = [1500.0, 1800.0, 2100.0], [1, 2, 1]
        dirty_v = [-999.0, 1500.0, 1800.0, 2100.0, 99999.0]
        dirty_w = [0, 1, 2, 1, 0]
        for q in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
            assert weighted_quantile(dirty_v, dirty_w, q) == weighted_quantile(
                clean_v, clean_w, q
            ), f"zero-weight outlier shifted q={q}"

    def test_zero_weight_outlier_does_not_shift_median(self) -> None:
        assert weighted_median([-999.0, 1500.0, 1800.0, 99999.0], [0, 1, 1, 0]) == (
            weighted_median([1500.0, 1800.0], [1, 1])
        )

    def test_q0_and_q1_ignore_zero_weight_extremes(self) -> None:
        values = [-999.0, 1500.0, 2100.0, 99999.0]
        weights = [0, 1, 1, 0]
        assert weighted_quantile(values, weights, 0.0) == 1500.0
        assert weighted_quantile(values, weights, 1.0) == 2100.0


# ---------------------------------------------------------------------------
# weighted_quantile conventions (pinned by QA from the lower-median rule;
# q=0 -> min, q=1 -> max — flagged in the report for PM awareness)
# ---------------------------------------------------------------------------


@needs_module
class TestQuantileConventions:
    def test_q_half_equals_weighted_median(self) -> None:
        values, weights = [1500.0, 1800.0, 2100.0, 2400.0], [1, 3, 2, 1]
        assert weighted_quantile(values, weights, 0.5) == weighted_median(
            values, weights
        )

    def test_q_zero_is_minimum(self) -> None:
        assert weighted_quantile([2100.0, 1500.0, 1800.0], [1, 1, 1], 0.0) == 1500.0

    def test_q_one_is_maximum(self) -> None:
        assert weighted_quantile([2100.0, 1500.0, 1800.0], [1, 1, 1], 1.0) == 2100.0

    def test_no_interpolation_ever(self) -> None:
        # Any interpolating implementation produces a value strictly between
        # input elements somewhere; the lower convention never does.
        values, weights = [10.0, 20.0], [1, 1]
        for q in (0.25, 0.5, 0.75):
            assert weighted_quantile(values, weights, q) in values


# ---------------------------------------------------------------------------
# Property tests (hypothesis) — the invariants generalized
# ---------------------------------------------------------------------------


@needs_module
class TestProperties:
    @given(
        values=st.lists(finite_values, min_size=1, max_size=50),
        w=positive_int_weights,
    )
    def test_uniform_weights_equal_plain_lower_median(self, values, w) -> None:
        # AC1 generalized: any uniform weight vector reproduces the plain
        # LOWER median (median_low == median for odd n, lower-of-middle-two
        # for even n — exactly the locked convention).
        assert weighted_median(values, [w] * len(values)) == statistics.median_low(
            values
        )

    @given(
        pairs=st.lists(
            st.tuples(finite_values, st.integers(min_value=1, max_value=6)),
            min_size=1,
            max_size=20,
        ),
        q=quantiles,
    )
    def test_integer_weights_equal_duplication(self, pairs, q) -> None:
        # AC2 generalized: weight k behaves exactly like k weight-1 copies,
        # for the median and for every quantile.
        values = [v for v, _ in pairs]
        weights = [w for _, w in pairs]
        expanded = [v for v, w in pairs for _ in range(w)]
        ones = [1] * len(expanded)
        assert weighted_median(values, weights) == weighted_median(expanded, ones)
        assert weighted_quantile(values, weights, q) == weighted_quantile(
            expanded, ones, q
        )

    @given(
        pairs=st.lists(
            st.tuples(finite_values, positive_int_weights), min_size=1, max_size=30
        ),
        q=quantiles,
        data=st.data(),
    )
    def test_permutation_invariance(self, pairs, q, data) -> None:
        perm = data.draw(st.permutations(pairs))
        v1, w1 = [p[0] for p in pairs], [p[1] for p in pairs]
        v2, w2 = [p[0] for p in perm], [p[1] for p in perm]
        assert weighted_median(v1, w1) == weighted_median(v2, w2)
        assert weighted_quantile(v1, w1, q) == weighted_quantile(v2, w2, q)

    @given(
        pairs=st.lists(
            st.tuples(finite_values, positive_int_weights), min_size=1, max_size=30
        ),
        scale=st.integers(min_value=1, max_value=100),
        q=quantiles,
    )
    def test_weight_scale_invariance(self, pairs, scale, q) -> None:
        # Only relative weights matter: scaling all weights by a positive
        # constant changes nothing.
        values = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        scaled = [w * scale for w in weights]
        assert weighted_median(values, weights) == weighted_median(values, scaled)
        assert weighted_quantile(values, weights, q) == weighted_quantile(
            values, scaled, q
        )

    @given(
        pairs=st.lists(
            st.tuples(finite_values, nonneg_int_weights), min_size=0, max_size=30
        ),
        q=quantiles,
    )
    def test_result_is_element_of_positive_weight_values_or_none(self, pairs, q):
        # The defining property of the lower convention: the answer is always
        # one of the surviving (weight > 0) input values — never interpolated,
        # never a zero-weight entry — and None exactly when nothing survives.
        values = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        surviving = [v for v, w in pairs if w > 0]
        med = weighted_median(values, weights)
        quant = weighted_quantile(values, weights, q)
        if not surviving:
            assert med is None
            assert quant is None
        else:
            assert med in surviving
            assert quant in surviving

    @given(
        pairs=st.lists(
            st.tuples(finite_values, positive_int_weights), min_size=1, max_size=30
        ),
        q1=quantiles,
        q2=quantiles,
    )
    def test_quantile_nondecreasing_in_q(self, pairs, q1, q2) -> None:
        lo, hi = sorted((q1, q2))
        values = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        assert weighted_quantile(values, weights, lo) <= weighted_quantile(
            values, weights, hi
        )

    @settings(max_examples=200)
    @given(
        pairs=st.lists(
            st.tuples(finite_values, positive_int_weights), min_size=1, max_size=30
        )
    )
    def test_q_half_always_agrees_with_weighted_median(self, pairs) -> None:
        values = [p[0] for p in pairs]
        weights = [p[1] for p in pairs]
        assert weighted_quantile(values, weights, 0.5) == weighted_median(
            values, weights
        )


# ---------------------------------------------------------------------------
# QA-PROPOSED contract — NOT stated in the story. Dev: implement as written;
# PM/owner: confirm or overrule before treating as locked. If overruled, QA
# amends these tests (they are QA's to fix), not the invariants above.
# ---------------------------------------------------------------------------


@needs_module
class TestProposedContract:
    """PROPOSED (pending PM confirmation): reject malformed input loudly.

    Rationale: the story locks None for empty/all-zero-weight (legitimately
    "no evidence"), but a negative weight, an out-of-range q, or mismatched
    array lengths are caller bugs — returning None would silently launder
    them into "no evidence". ValueError keeps the None contract meaningful.
    """

    def test_negative_weight_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            weighted_median([1500.0, 1800.0], [1, -1])
        with pytest.raises(ValueError):
            weighted_quantile([1500.0, 1800.0], [1, -1], 0.5)

    def test_q_out_of_range_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            weighted_quantile([1500.0], [1], -0.01)
        with pytest.raises(ValueError):
            weighted_quantile([1500.0], [1], 1.01)

    def test_mismatched_lengths_raise_value_error(self) -> None:
        with pytest.raises(ValueError):
            weighted_median([1500.0, 1800.0], [1])
        with pytest.raises(ValueError):
            weighted_quantile([1500.0], [1, 2], 0.5)
