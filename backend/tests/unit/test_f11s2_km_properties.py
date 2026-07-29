"""F11-S2 [INVARIANT] — Kaplan-Meier property tests (`hypothesis`).

QA-authored, written RED before implementation. Layer 1.

ARCHITECTURE.md §8 names these explicitly: "`hypothesis` property tests where
the stories call for them — stitcher gap boundaries, DOM monotonicity, **KM
non-increasing**". This file is that, plus the surrounding invariants the PM's
dispatch and the AC name:

* the survival curve is non-increasing                       (AC, §8)
* it starts at 1.0                                           (AC)
* censored observations reduce the at-risk set without ever
  counting as an event                                       (NORTH_STAR)
* weights behave consistently with F0-S3's semantics
  (weight 3 == three weight-1 entries)                       (PM dispatch)

The strongest of these is `test_weight_replication_matches_duplicated_rows`:
it is a *metamorphic oracle*. Expanding integer weights into duplicated rows
and running the estimator unweighted is a structurally different computation
from summing weights into the risk sets, and the two must agree exactly. Where
the hand-computed fixtures in `test_f11s2_km_estimator.py` check seven specific
inputs, this checks the same claim over hundreds of generated ones.

Every strategy below uses INTEGER weights and INTEGER durations, so every
risk-set sum is exact in float64 and a failure means the definition is wrong,
not that floats rounded. (Fractional weights are covered by fixture G in the
companion file, where the arithmetic was hand-verified instead.)

EXPECTED RED STATE: all tests SKIP until `rentcomp.stats.km` exists —
`test_f11s2_km_estimator.py::test_km_module_exists` is the single loud failure
that names the missing module, and duplicating it here would just make the red
state noisier.
"""

from __future__ import annotations

import os

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Matches the F0-S3 convention: `HYPOTHESIS_PROFILE=qa-soak pytest ...` runs
# these at 2000 examples for the verify pass.
settings.register_profile("qa-soak", max_examples=2000)
if os.environ.get("HYPOTHESIS_PROFILE"):
    settings.load_profile(os.environ["HYPOTHESIS_PROFILE"])

try:
    from rentcomp.stats.km import km_estimate, survival_at

    _IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover - red state before F11-S2 lands
    _IMPORT_ERROR = _e

needs_module = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.stats.km not importable yet (F11-S2 red): {_IMPORT_ERROR}",
)

REL = 1e-12

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: `StitchedComp.effective_dom` is `ge=0` (models/domain.py), so 0 is
#: representable and the estimator must survive it. The upper bound is a
#: generous three-year DOM.
durations = st.integers(min_value=0, max_value=200)
positive_int_weights = st.integers(min_value=1, max_value=12)
nonneg_int_weights = st.integers(min_value=0, max_value=12)


def cohorts(weights=positive_int_weights, min_size=1, max_size=25):
    """`(durations, censored, weights)` triples of equal length."""
    return st.lists(
        st.tuples(durations, st.booleans(), weights),
        min_size=min_size,
        max_size=max_size,
    ).map(lambda rows: tuple(list(column) for column in zip(*rows, strict=True)))


def _steps(estimate) -> list:
    return list(estimate.steps)


def _survivals(estimate) -> list[float]:
    return [s.survival for s in _steps(estimate)]


def _probe_days(d, estimate=None) -> list[int]:
    """Interesting days to evaluate the step function at: every step day and
    its immediate neighbours (where a step function is easiest to get wrong by
    one), every observed duration, day 0, and one day past the window.

    A targeted probe rather than `range(0, max(d))` — a dense sweep over a
    3-year horizon multiplied by hypothesis' example count is minutes of test
    time for no extra coverage of a piecewise-constant function.
    """
    interesting = {0}
    for day in d:
        interesting.update({day - 1, day, day + 1})
    if estimate is not None:
        for step in _steps(estimate):
            interesting.update({step.day - 1, step.day, step.day + 1})
    interesting.add((max(d) if d else 0) + 30)
    return sorted(day for day in interesting if day >= 0)


# ---------------------------------------------------------------------------
# Curve shape
# ---------------------------------------------------------------------------


@needs_module
@given(cohort=cohorts())
def test_the_survival_curve_is_non_increasing(cohort) -> None:
    """The named §8 property. S is a product of factors in [0, 1], so it can
    only fall or stay flat — a rising survival curve would mean comps
    un-leasing."""
    d, c, w = cohort
    survivals = _survivals(km_estimate(d, c, w))
    for earlier, later in zip(survivals, survivals[1:], strict=False):
        assert later <= earlier + 1e-15, (
            f"survival rose from {earlier!r} to {later!r} — the product-limit "
            f"estimator cannot increase. input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts())
def test_every_survival_value_is_a_probability(cohort) -> None:
    d, c, w = cohort
    for step in _steps(km_estimate(d, c, w)):
        assert 0.0 <= step.survival <= 1.0, (
            f"S({step.day}) = {step.survival!r} is not a probability. input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts())
def test_the_curve_starts_at_one(cohort) -> None:
    """AC: "S(0) = 1", stated as "S(t) = 1 for every t strictly before the
    first event day" so it stays well-defined if a day-0 lease appears (see
    the flagged ambiguity in the companion file)."""
    d, c, w = cohort
    estimate = km_estimate(d, c, w)
    steps = _steps(estimate)
    first_event_day = steps[0].day if steps else None
    if first_event_day is None:
        probes = [0, 1, 30]
    else:
        probes = [day for day in (0, first_event_day - 1) if 0 <= day < first_event_day]
    for day in probes:
        assert survival_at(estimate, day) == pytest.approx(1.0, rel=REL), (
            f"S({day}) != 1.0 with the first event on day {first_event_day}. "
            f"input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts())
def test_survival_at_is_a_non_increasing_step_function(cohort) -> None:
    d, c, w = cohort
    estimate = km_estimate(d, c, w)
    previous = 1.0
    previous_day = None
    for day in _probe_days(d, estimate):
        value = survival_at(estimate, day)
        assert 0.0 <= value <= 1.0, f"S({day}) = {value!r}. input={cohort!r}"
        assert value <= previous + 1e-15, (
            f"S({day}) = {value!r} rose above S({previous_day}) = {previous!r}. "
            f"input={cohort!r}"
        )
        previous, previous_day = value, day


# ---------------------------------------------------------------------------
# Censoring — the honesty invariant
# ---------------------------------------------------------------------------


@needs_module
@given(cohort=cohorts())
def test_steps_land_only_on_days_a_comp_actually_leased(cohort) -> None:
    """NORTH_STAR's single most important distinction: a censored comp is a
    floor, never an observed lease. It may shrink the risk set; it may never
    create a step."""
    d, c, w = cohort
    leased_days = {
        day for day, cens, weight in zip(d, c, w, strict=True) if not cens and weight > 0
    }
    step_days = {s.day for s in _steps(km_estimate(d, c, w))}
    assert step_days == leased_days, (
        f"step days {sorted(step_days)} != days a comp actually leased "
        f"{sorted(leased_days)}. input={cohort!r}"
    )


@needs_module
@given(cohort=cohorts())
def test_event_weight_never_exceeds_the_risk_set(cohort) -> None:
    """`d_w(t) <= n_w(t)` at every step, and both are positive. A step whose
    events exceed its risk set is the classic sign of censored comps having
    been dropped from the denominator too early."""
    d, c, w = cohort
    for step in _steps(km_estimate(d, c, w)):
        assert step.events > 0, f"step at day {step.day} has no event weight"
        assert step.at_risk > 0, f"step at day {step.day} has an empty risk set"
        assert step.events <= step.at_risk + 1e-12, (
            f"d_w({step.day}) = {step.events} > n_w({step.day}) = {step.at_risk}. "
            f"input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts(), index=st.integers(min_value=0, max_value=99))
def test_censoring_a_comp_can_only_raise_the_curve(cohort, index) -> None:
    """Re-labelling one leased comp as censored removes its event but leaves
    every later risk set untouched (it left the pool at the same day either
    way). So survival can only go UP, never down.

    This is the property that makes "censored is not leased" testable as a
    direction rather than a spot value: if an implementation ever counted a
    censored comp as a lease, censoring a comp would sometimes *lower* the
    curve, and this fails.
    """
    d, c, w = cohort
    leased = [i for i, cens in enumerate(c) if not cens]
    if not leased:
        return
    target = leased[index % len(leased)]
    censored_variant = list(c)
    censored_variant[target] = True

    base = km_estimate(d, c, w)
    variant = km_estimate(d, censored_variant, w)
    for day in _probe_days(d, base):
        assert survival_at(variant, day) >= survival_at(base, day) - 1e-12, (
            f"censoring comp {target} LOWERED S({day}) from "
            f"{survival_at(base, day)!r} to {survival_at(variant, day)!r} — a "
            f"censored comp was counted as a lease. input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts())
def test_an_all_censored_cohort_has_no_estimable_event_probability(cohort) -> None:
    """The estimator and F11-S3's `all_censored` guard must agree about the
    same cohorts. Nothing observed to lease means no product to take: an empty
    curve and S == 1 everywhere, not a raise and not a fabricated decay."""
    d, _c, w = cohort
    estimate = km_estimate(d, [True] * len(d), w)
    assert _steps(estimate) == [], f"all-censored input produced steps. input={d!r}"
    assert estimate.truncated_at is None
    for day in (0, 1, 30, max(d) + 100 if d else 100):
        assert survival_at(estimate, day) == pytest.approx(1.0, rel=REL)


# ---------------------------------------------------------------------------
# Weight semantics — F0-S3's rulings carried into the KM stage
# ---------------------------------------------------------------------------


def _expand(d, c, w):
    """Integer weights -> duplicated unit-weight rows."""
    out_d, out_c = [], []
    for duration, cens, weight in zip(d, c, w, strict=True):
        for _ in range(int(weight)):
            out_d.append(duration)
            out_c.append(cens)
    return out_d, out_c


@needs_module
@given(cohort=cohorts(max_size=8))
def test_weight_replication_matches_duplicated_rows(cohort) -> None:
    """THE metamorphic oracle: weight 3 is exactly three weight-1 entries.

    Summing weights into the risk sets and physically duplicating rows are
    structurally different computations, so their agreement is real evidence
    the weighted formula is the product-limit estimator and not something
    adjacent to it. This is also F0-S3's own locked weight semantics, held at
    the KM stage.
    """
    d, c, w = cohort
    weighted = km_estimate(d, c, w)
    expanded_d, expanded_c = _expand(d, c, w)
    expanded = km_estimate(expanded_d, expanded_c, [1.0] * len(expanded_d))

    assert [s.day for s in _steps(weighted)] == [s.day for s in _steps(expanded)], (
        f"different event days weighted vs expanded. input={cohort!r}"
    )
    for a, b in zip(_steps(weighted), _steps(expanded), strict=True):
        assert a.survival == pytest.approx(b.survival, rel=1e-9, abs=1e-12), (
            f"S({a.day}): weighted {a.survival!r} != expanded {b.survival!r}. "
            f"input={cohort!r}"
        )
        assert a.at_risk == pytest.approx(b.at_risk, rel=1e-9)
        assert a.events == pytest.approx(b.events, rel=1e-9)


@needs_module
@given(cohort=cohorts(), scale=st.integers(min_value=1, max_value=50))
def test_scaling_every_weight_leaves_the_curve_unchanged(cohort, scale) -> None:
    """Only weight RATIOS carry meaning — d_w/n_w is scale-free. Doubling every
    comp's weight is not new evidence."""
    d, c, w = cohort
    base = km_estimate(d, c, w)
    scaled = km_estimate(d, c, [weight * scale for weight in w])
    assert [s.day for s in _steps(base)] == [s.day for s in _steps(scaled)]
    for a, b in zip(_steps(base), _steps(scaled), strict=True):
        assert a.survival == pytest.approx(b.survival, rel=1e-9, abs=1e-12), (
            f"scaling all weights by {scale} moved S({a.day}) from {a.survival!r} "
            f"to {b.survival!r}. input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts(weights=nonneg_int_weights))
def test_zero_weight_comps_are_equivalent_to_omitting_them(cohort) -> None:
    """F0-S3's locked ruling, held at the KM stage: a weight-0 entry is
    excluded before computation, so dropping it changes nothing at all — not
    the steps, not the risk sets."""
    d, c, w = cohort
    kept = [i for i, weight in enumerate(w) if weight > 0]
    with_zeros = km_estimate(d, c, w)
    without = km_estimate(
        [d[i] for i in kept], [c[i] for i in kept], [w[i] for i in kept]
    )
    assert [s.day for s in _steps(with_zeros)] == [s.day for s in _steps(without)], (
        f"weight-0 comps changed the event days. input={cohort!r}"
    )
    for a, b in zip(_steps(with_zeros), _steps(without), strict=True):
        assert a.survival == pytest.approx(b.survival, rel=1e-9, abs=1e-12)
        assert a.at_risk == pytest.approx(b.at_risk, rel=1e-9), (
            f"a weight-0 comp is sitting in the risk set at day {a.day}. "
            f"input={cohort!r}"
        )


@needs_module
@given(cohort=cohorts(), data=st.data())
def test_input_order_does_not_change_the_curve(cohort, data) -> None:
    """The estimator is an aggregate over a set, not a sequence. Reproducibility
    matters for the decision log (F11-S6) and for QA's confidence in any
    result."""
    d, c, w = cohort
    order = data.draw(st.permutations(range(len(d))))
    shuffled = km_estimate([d[i] for i in order], [c[i] for i in order], [w[i] for i in order])
    base = km_estimate(d, c, w)
    assert [s.day for s in _steps(base)] == [s.day for s in _steps(shuffled)]
    for a, b in zip(_steps(base), _steps(shuffled), strict=True):
        assert a.survival == pytest.approx(b.survival, rel=1e-9, abs=1e-12), (
            f"reordering the input moved S({a.day}). input={cohort!r}"
        )
