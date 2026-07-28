"""F11-S2 [INVARIANT] — weighted Kaplan-Meier product-limit estimator.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Layer 1 (ARCHITECTURE.md §8): a pure function over plain arrays,
callable directly — the KM estimator is named in §8's own L1 list.

======================================================================
THE MODULE CONTRACT THIS FILE IS WRITTEN AGAINST
======================================================================
`backend/src/rentcomp/stats/km.py`:

    @dataclass(frozen=True)
    class KMStep:
        day: int              # an EVENT day (a leased neighbour's effective_dom)
        survival: float       # S(day), i.e. AFTER this day's drop
        at_risk: float        # n_w(day) — summed weights at risk just before
        events: float         # d_w(day) — summed weights leasing at exactly day
        censored_count: int   # bookkeeping, see CENSORED_COUNT below

    @dataclass(frozen=True)
    class KMEstimate:
        steps: Sequence[KMStep]   # EVENT days only, strictly ascending
        truncated_at: int | None  # last observable time; None when no events

    def km_estimate(durations, censored, weights=None) -> KMEstimate
    def survival_at(estimate: KMEstimate, day: float) -> float

Tests here use attribute access only, so `KMStep`/`KMEstimate` may be
dataclasses, NamedTuples or Pydantic models — that is the developer's call.
`steps` may be a list or a tuple.

======================================================================
THE PRODUCT-LIMIT DEFINITION, AS THIS FILE READS IT
======================================================================
At each distinct event day t (ascending):

    S(t) = S(t-) x (1 - d_w(t) / n_w(t))

* `d_w(t)` = summed weights of comps whose `effective_dom == t` and which are
  NOT censored (they actually leased).
* `n_w(t)` = summed weights of every comp with `effective_dom >= t`.
* `S(t-)` = 1.0 before the first event day.

TIE CONVENTION — PINNED HERE, FLAGGED TO THE PM (see the handoff).
The story says n_w is "summed weights still at risk (not yet leased, not yet
censored) at t", which does not settle what happens to a comp censored at
EXACTLY t. This file pins the standard KM convention: **a comp censored at day
t is still at risk for an event at day t** (events precede censorings at ties).

Why that direction and not the other: a comp censored at day t was observed
still vacant *through* day t — it genuinely was at risk of leasing that day.
Dropping it from n_w(t) shrinks the denominator, inflates d_w/n_w, and biases
S downward, i.e. makes the tool overstate lease velocity. That is the exact
direction NORTH_STAR's honesty invariant cares about, so the conservative
reading is also the standard one. Fixture D below is the discriminating case:
under the other convention its day-1 survival would be 3/4, not 7/10.

CENSORED_COUNT (bookkeeping, not the formula). This file does NOT pin the
exact per-step bucketing — the product-limit estimator does not depend on it
and pinning it would over-specify a display field. Only a totals invariant is
asserted (`test_censored_counts_never_exceed_the_censored_input`). The
suggested convention, for the developer: `censored_count` at step t is the
number of comps censored in `(previous_event_day, t]`.

======================================================================
HOW THE EXPECTED CONSTANTS BELOW WERE ESTABLISHED (the AC's "verified
against a reference implementation")
======================================================================
The story's AC names `lifelines`. `lifelines` is NOT used here, and no
dependency was added. Route taken (PM dispatch options 1 + 2, and see the
handoff for the reasoning):

1. Every constant is HAND-COMPUTED from the product-limit formula. Each
   fixture carries its full derivation in its own docstring, in exact
   rational arithmetic, so the arithmetic is checkable by a reader without
   running anything.
2. Each hand-computation was then cross-checked in the QA session against two
   throwaway oracles, since deleted and never committed (the F0-S3
   precedent): (a) a direct `fractions.Fraction` transcription of the formula
   with no numpy, and (b) for the five integer-weight fixtures, a textbook
   UNWEIGHTED KM loop run over the weight-EXPANDED row set (weight 3 -> three
   duplicated rows). Oracle (b) is a structurally different algorithm, so its
   agreement is a real cross-check and not a restatement. All seven fixtures
   agreed exactly, in exact rationals, on the first run.

Why not `lifelines`: it is sanctioned dev-only by ARCHITECTURE.md D3/D8/§1a,
so this was a judgement call, not a budget breach either way. It was declined
because (i) it drags pandas + scipy + matplotlib + autograd into the single
shared `.venv`, (ii) a `pytest.importorskip("lifelines")` verification test
silently SKIPS on any machine that lacks it — the exact false-green pattern
this repo just spent a commit fixing — whereas committed constants can never
skip, and (iii) at n<=7 with integer-ish weights the product-limit arithmetic
is exact and fully checkable by hand, so the library adds no assurance the
derivations above do not already carry. The AC's [INVARIANT] is that the
numbers are right against an independent reference; naming `lifelines` is the
technique, and AGENT_QA.md forbids pinning a technique over its outcome.

======================================================================
EXPECTED RED STATE TODAY
======================================================================
`stats/km.py` does not exist. `test_km_module_exists` is the ONE ungated,
loudly-failing test that names what is missing; every other test in this file
SKIPS behind the `km` fixture until the module lands. No collection errors.
"""

from __future__ import annotations

import importlib

import pytest

#: Relative tolerance for every survival comparison. Deliberately loose enough
#: that a different-but-equivalent accumulation order (numpy cumprod vs a
#: Python loop vs log-space) passes — the [DEFAULT] is free to restructure
#: (SEMANTIC_CHANGE_PROTOCOL example 4), so exact float equality would be a
#: test pinning an implementation.
REL = 1e-12


# ---------------------------------------------------------------------------
# Red-state gate
# ---------------------------------------------------------------------------


def test_km_module_exists() -> None:
    """The one loud failure while F11-S2 is unimplemented.

    Everything else in this file skips behind the `km` fixture, so the red
    state reads as "1 failure naming the missing module + N skips" rather
    than a wall of identical import errors.
    """
    module = importlib.import_module("rentcomp.stats.km")
    for name in ("km_estimate", "survival_at"):
        assert hasattr(module, name), (
            f"rentcomp.stats.km exists but has no {name!r} — see this module's "
            "CONTRACT block for the agreed surface"
        )


@pytest.fixture
def km():
    """The estimator module, or a legible skip while F11-S2 is unbuilt."""
    try:
        return importlib.import_module("rentcomp.stats.km")
    except ImportError:
        pytest.skip("rentcomp.stats.km not implemented yet (F11-S2 red)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _steps(estimate) -> list:
    return list(estimate.steps)


def _shape(estimate) -> list[tuple]:
    """(day, survival, at_risk, events) per step — the four fields the
    product-limit formula actually defines. `censored_count` is bookkeeping and
    is checked separately (see the module docstring)."""
    return [(s.day, s.survival, s.at_risk, s.events) for s in _steps(estimate)]


def _assert_curve(actual: list[tuple], expected: list[tuple], label: str) -> None:
    assert len(actual) == len(expected), (
        f"{label}: expected {len(expected)} event-day step(s) "
        f"{[e[0] for e in expected]}, got {len(actual)} {[a[0] for a in actual]}. "
        "Steps must exist at EVENT days only — a censoring is not a step."
    )
    for (a_day, a_s, a_n, a_d), (e_day, e_s, e_n, e_d) in zip(actual, expected, strict=True):
        assert a_day == e_day, f"{label}: step day {a_day} != expected {e_day}"
        assert a_s == pytest.approx(e_s, rel=REL, abs=1e-15), (
            f"{label}: S({e_day}) = {a_s!r}, expected {e_s!r} "
            "(see this fixture's docstring for the full hand derivation)"
        )
        assert a_n == pytest.approx(e_n, rel=REL), (
            f"{label}: n_w({e_day}) = {a_n!r}, expected {e_n!r} — the weighted "
            "risk set. A comp censored at exactly this day IS still at risk here "
            "(tie convention, module docstring)."
        )
        assert a_d == pytest.approx(e_d, rel=REL), (
            f"{label}: d_w({e_day}) = {a_d!r}, expected {e_d!r} — the summed "
            "weights of comps that actually LEASED on this day. A censored comp "
            "never contributes an event (NORTH_STAR: censored is never leased)."
        )


# ---------------------------------------------------------------------------
# The reference fixtures — AC: ">= 5 fixtures, including all-censored-after-
# first-event and heavy-tie cases". Seven here.
# ---------------------------------------------------------------------------


def test_fixture_a_no_censoring_unweighted(km) -> None:
    """A: three leases, no censoring, unit weights. The textbook base case.

    durations [5, 10, 20], all leased, weights [1, 1, 1]

        t=5 : n_w=3, d_w=1  ->  S = 1 x (1 - 1/3) = 2/3
        t=10: n_w=2, d_w=1  ->  S = 2/3 x (1 - 1/2) = 1/3
        t=20: n_w=1, d_w=1  ->  S = 1/3 x (1 - 1/1) = 0
    """
    estimate = km.km_estimate([5, 10, 20], [False, False, False], [1.0, 1.0, 1.0])
    _assert_curve(
        _shape(estimate),
        [(5, 2 / 3, 3.0, 1.0), (10, 1 / 3, 2.0, 1.0), (20, 0.0, 1.0, 1.0)],
        "fixture A",
    )


def test_fixture_b_censoring_between_events(km) -> None:
    """B: censorings interleaved between events — the classic KM illustration.

    durations [6, 7, 10, 15, 19], censored [F, T, F, T, F], unit weights

        t=6 : n_w = |{6,7,10,15,19}| = 5, d_w=1  ->  S = 1 - 1/5 = 4/5
        (7 is censored: leaves the risk set, contributes no event)
        t=10: n_w = |{10,15,19}| = 3,     d_w=1  ->  S = 4/5 x 2/3 = 8/15
        (15 censored)
        t=19: n_w = |{19}| = 1,           d_w=1  ->  S = 8/15 x 0 = 0

    This is the honesty invariant in its smallest form: the two censored comps
    shrink the risk set but never move the numerator.
    """
    estimate = km.km_estimate(
        [6, 7, 10, 15, 19], [False, True, False, True, False], [1.0] * 5
    )
    _assert_curve(
        _shape(estimate),
        [(6, 4 / 5, 5.0, 1.0), (10, 8 / 15, 3.0, 1.0), (19, 0.0, 1.0, 1.0)],
        "fixture B",
    )


def test_fixture_c_integer_weights(km) -> None:
    """C: the same event days as A, but weighted — weights move d_w and n_w.

    durations [5, 10, 20], all leased, weights [3, 1, 2]  (total 6)

        t=5 : n_w = 3+1+2 = 6, d_w = 3  ->  S = 1 - 3/6 = 1/2
        t=10: n_w = 1+2 = 3,   d_w = 1  ->  S = 1/2 x 2/3 = 1/3
        t=20: n_w = 2,         d_w = 2  ->  S = 1/3 x 0 = 0

    Cross-checked against the weight-expanded unweighted run
    [5, 5, 5, 10, 20, 20] — identical, which is F0-S3's "weight 3 is three
    weight-1 entries" carried into the KM stage.
    """
    estimate = km.km_estimate([5, 10, 20], [False, False, False], [3.0, 1.0, 2.0])
    _assert_curve(
        _shape(estimate),
        [(5, 0.5, 6.0, 3.0), (10, 1 / 3, 3.0, 1.0), (20, 0.0, 2.0, 2.0)],
        "fixture C",
    )


def test_fixture_d_heavy_ties_day_one_events_and_censoring_on_an_event_day(km) -> None:
    """D: THE discriminating fixture — heavy ties, a day-1 event, weights, and
    a censoring landing on exactly an event day at BOTH event days.

    durations [1, 1, 1, 5, 5, 5, 5]
    censored  [F, F, T, F, T, F, T]
    weights   [2, 1, 1, 1, 3, 1, 1]   (total 10)

        t=1 : at risk = everything = 10 (the day-1 censoring at w=1 IS still
              at risk for the day-1 event — tie convention, module docstring)
              d_w = 2 + 1 = 3
              S = 1 - 3/10 = 7/10
              leaving: 10 - 3 (leased) - 1 (censored) = 6
        t=5 : n_w = 1 + 3 + 1 + 1 = 6  (matches)
              d_w = 1 + 1 = 2
              S = 7/10 x (1 - 2/6) = 7/10 x 2/3 = 7/15

    Under the REJECTED tie convention (censored-at-t already gone) day 1 would
    read n_w=9, S=1-3/9=2/3 — so this fixture fails loudly if the convention
    is implemented the other way, rather than drifting silently.

    Also covers the AC's "day-1 events and ties require no special handling":
    there is no `if day == 1` anywhere in the arithmetic above.
    """
    estimate = km.km_estimate(
        [1, 1, 1, 5, 5, 5, 5],
        [False, False, True, False, True, False, True],
        [2.0, 1.0, 1.0, 1.0, 3.0, 1.0, 1.0],
    )
    _assert_curve(
        _shape(estimate),
        [(1, 0.7, 10.0, 3.0), (5, 7 / 15, 6.0, 2.0)],
        "fixture D",
    )


def test_fixture_e_all_censored_after_the_first_event(km) -> None:
    """E: the AC's named case — one event, then nothing but censoring.

    durations [4, 9, 12, 20], censored [F, T, T, T], unit weights

        t=4: n_w = 4, d_w = 1  ->  S = 1 - 1/4 = 3/4
        no further event days: the curve STOPS at 3/4.

    The curve must not be extrapolated to 0. Three comps were still vacant when
    the pull was taken; the estimator knows nothing about when they leased, and
    saying S -> 0 would invent an outcome for them.
    """
    estimate = km.km_estimate([4, 9, 12, 20], [False, True, True, True], [1.0] * 4)
    _assert_curve(_shape(estimate), [(4, 0.75, 4.0, 1.0)], "fixture E")
    assert km.survival_at(estimate, 20) == pytest.approx(0.75, rel=REL), (
        "survival must stay flat at 3/4 past the last event — the three censored "
        "comps produce no further drop"
    )
    assert km.survival_at(estimate, 10_000) == pytest.approx(0.75, rel=REL), (
        "the curve never decays to 0 on its own; `truncated_at` is what states "
        "where it stops being informative"
    )


def test_fixture_f_all_censored_has_no_estimable_event_probability(km) -> None:
    """F: no events at all. The estimator must AGREE with the `all_censored`
    guard rather than fabricate a curve.

    durations [10, 12, 30], all censored.

    There is no event day, so there is no product to take: S(t) = 1 for every
    t. The estimator returns zero steps and does NOT raise — deciding that this
    cohort cannot be shown is F11-S3's job, not an exception here. What the
    estimator must never do is emit a survival below 1.0 from an all-censored
    set: that would be an observed lease outcome no comp ever produced.
    """
    estimate = km.km_estimate([10, 12, 30], [True, True, True], [1.0, 1.0, 1.0])
    assert _steps(estimate) == [], (
        f"all-censored input produced {len(_steps(estimate))} step(s) — a censored "
        "comp is never a lease event (NORTH_STAR: the single most important "
        "distinction in the system)"
    )
    for day in (0, 1, 10, 30, 999):
        assert km.survival_at(estimate, day) == pytest.approx(1.0, rel=REL), (
            f"S({day}) from an all-censored cohort must be 1.0 — nothing was "
            "observed to lease"
        )


def test_fixture_g_fractional_weights(km) -> None:
    """G: non-integer weights, where the replication oracle does not apply and
    only the formula itself carries the answer.

    durations [3, 8, 8, 14], censored [F, F, T, F], weights [.5, 1.5, 2., 1.]
    (total 5.0)

        t=3 : n_w = 5.0,  d_w = 0.5  ->  S = 1 - 0.1 = 9/10
        t=8 : n_w = 1.5 + 2.0 + 1.0 = 4.5   (the w=2.0 comp censored at day 8
              is still at risk for the day-8 event)
              d_w = 1.5                     (only the LEASED day-8 comp)
              S = 9/10 x (1 - 1.5/4.5) = 9/10 x 2/3 = 3/5
        t=14: n_w = 1.0, d_w = 1.0   ->  S = 3/5 x 0 = 0

    Note d_w(8) = 1.5, not 3.5: two comps share day 8 but only one leased.
    """
    estimate = km.km_estimate(
        [3, 8, 8, 14], [False, False, True, False], [0.5, 1.5, 2.0, 1.0]
    )
    _assert_curve(
        _shape(estimate),
        [(3, 0.9, 5.0, 0.5), (8, 0.6, 4.5, 1.5), (14, 0.0, 1.0, 1.0)],
        "fixture G",
    )


# ---------------------------------------------------------------------------
# Curve-shape invariants stated directly by the AC
# ---------------------------------------------------------------------------


def test_survival_is_one_before_the_first_event(km) -> None:
    """AC: "S(0) = 1". Stated as "S(t) = 1 for every t strictly before the
    first event day", which is the same claim wherever DOM >= 1 and stays
    well-defined if a day-0 lease ever appears (`StitchedComp.effective_dom`
    is `ge=0`, so it is representable — see the handoff's flagged ambiguity)."""
    estimate = km.km_estimate([6, 7, 10], [False, True, False], [1.0, 1.0, 1.0])
    for day in (0, 1, 5, 5.999):
        assert km.survival_at(estimate, day) == pytest.approx(1.0, rel=REL), (
            f"S({day}) must be 1.0 — the first event is on day 6"
        )


def test_survival_at_an_event_day_is_the_post_drop_value(km) -> None:
    """The step function is read as "% still vacant AT day t", i.e. S(t) =
    P(T > t): a comp with effective_dom == 14 sat vacant for 14 days and then
    leased, so it is NOT still vacant at day 14. S(14) therefore includes
    day 14's drop. This is what the F11-S5 horizon readouts (14/30/45/60)
    consume, so getting it backwards would misreport every horizon by one
    step."""
    estimate = km.km_estimate(
        [6, 7, 10, 15, 19], [False, True, False, True, False], [1.0] * 5
    )
    assert km.survival_at(estimate, 6) == pytest.approx(4 / 5, rel=REL)
    assert km.survival_at(estimate, 9) == pytest.approx(4 / 5, rel=REL)
    assert km.survival_at(estimate, 10) == pytest.approx(8 / 15, rel=REL)
    assert km.survival_at(estimate, 18) == pytest.approx(8 / 15, rel=REL)
    assert km.survival_at(estimate, 19) == pytest.approx(0.0, abs=1e-15)


def test_steps_are_strictly_ascending_event_days(km) -> None:
    """Steps land on event days only, once each, in ascending order — the
    heavy-tie case must collapse into ONE step per day, not one per comp."""
    estimate = km.km_estimate(
        [1, 1, 1, 5, 5, 5, 5],
        [False, False, True, False, True, False, True],
        [2.0, 1.0, 1.0, 1.0, 3.0, 1.0, 1.0],
    )
    days = [s.day for s in _steps(estimate)]
    assert days == sorted(set(days)), f"steps must be unique and ascending, got {days}"
    assert days == [1, 5]


def test_censored_counts_never_exceed_the_censored_input(km) -> None:
    """Totals invariant on the bookkeeping field. The exact per-step bucketing
    is deliberately NOT pinned (module docstring), but a comp can be counted as
    censored at most once across the whole curve, and only if it was censored."""
    censored = [False, True, False, True, False, True]
    estimate = km.km_estimate([3, 3, 8, 8, 14, 40], censored, [1.0] * 6)
    total = sum(s.censored_count for s in _steps(estimate))
    assert 0 <= total <= sum(censored), (
        f"summed censored_count is {total}, but only {sum(censored)} comps were "
        "censored — a censoring cannot be counted twice, nor invented"
    )


def test_truncation_never_claims_beyond_the_observation_window(km) -> None:
    """NORTH_STAR: the curve is an estimate under a TRUNCATED empirical window
    and is never valid beyond the data's actual observation window.

    The exact truncation formula ("min of last event time and largest censoring
    floor") belongs to F11-S4 and is deliberately NOT pinned here. What is
    pinned is the honesty floor that holds under any reading of it: the
    truncation point is a time the data actually observed, and there are no
    events at all when nothing was observed to lease.
    """
    durations = [6, 7, 10, 15, 19]
    estimate = km.km_estimate(durations, [False, True, False, True, False], [1.0] * 5)
    assert estimate.truncated_at is not None
    assert estimate.truncated_at <= max(durations), (
        f"truncated_at={estimate.truncated_at} is beyond the largest observed "
        f"duration ({max(durations)}) — that is a claim about time the pull "
        "never saw"
    )
    assert estimate.truncated_at in durations, (
        f"truncated_at={estimate.truncated_at} is not one of the observed "
        f"durations {durations}"
    )

    all_censored = km.km_estimate([10, 12, 30], [True] * 3, [1.0] * 3)
    assert all_censored.truncated_at is None, (
        "an all-censored cohort has no estimable event probability, so there is "
        "no truncation point to state — None, not a fabricated day"
    )


# ---------------------------------------------------------------------------
# Weight semantics — consistent with F0-S3's locked rulings (`stats/weighted.py`)
# ---------------------------------------------------------------------------


def test_weights_default_to_uniform(km) -> None:
    """Omitting weights is the unweighted estimator."""
    durations, censored = [6, 7, 10, 15, 19], [False, True, False, True, False]
    _assert_curve(
        _shape(km.km_estimate(durations, censored)),
        _shape(km.km_estimate(durations, censored, [1.0] * 5)),
        "uniform-weight default",
    )


def test_a_zero_weight_comp_is_excluded_not_merely_downweighted(km) -> None:
    """F0-S3's locked ruling, carried into KM: a weight-0 entry is excluded
    BEFORE computation, so it is exactly equivalent to omitting the comp. It
    must not sit in the risk set contributing 0 to n_w — it already does not,
    numerically, but it must also not create a step at its own day."""
    with_zero = km.km_estimate(
        [5, 9, 10, 20], [False, False, False, False], [1.0, 0.0, 1.0, 1.0]
    )
    without = km.km_estimate([5, 10, 20], [False, False, False], [1.0, 1.0, 1.0])
    assert [s.day for s in _steps(with_zero)] == [s.day for s in _steps(without)], (
        "a weight-0 comp created its own event day — weight 0 means excluded, "
        "not 'included with no influence' (F0-S3)"
    )
    _assert_curve(_shape(with_zero), _shape(without), "zero-weight equivalence")


def test_negative_zero_weight_counts_as_zero_not_as_a_negative(km) -> None:
    """F0-S3's locked ruling verbatim: `-0.0` is a zero weight (excluded), not
    a negative weight (a raise). Pinned here so the two weighted-stats modules
    cannot drift apart on it."""
    estimate = km.km_estimate([5, 9, 10], [False, False, False], [1.0, -0.0, 1.0])
    assert [s.day for s in _steps(estimate)] == [5, 10]


def test_a_negative_weight_is_a_loud_caller_bug(km) -> None:
    """F0-S3's locked ruling: caller bugs raise, they are not laundered into a
    quiet result."""
    with pytest.raises(ValueError):
        km.km_estimate([5, 10], [False, False], [1.0, -2.0])


def test_mismatched_input_lengths_raise(km) -> None:
    """Same F0-S3 ruling. Three parallel sequences here, so both pairings are
    checked."""
    with pytest.raises(ValueError):
        km.km_estimate([5, 10, 20], [False, False], [1.0, 1.0, 1.0])
    with pytest.raises(ValueError):
        km.km_estimate([5, 10, 20], [False, False, False], [1.0, 1.0])


def test_empty_input_is_an_empty_curve_not_a_raise(km) -> None:
    """"No evidence" is a legitimate state (F0-S3 returns None for it); here it
    is an empty curve with S == 1 and nothing to truncate. F11-S3 decides that
    it cannot be rendered."""
    estimate = km.km_estimate([], [], [])
    assert _steps(estimate) == []
    assert estimate.truncated_at is None
    assert km.survival_at(estimate, 30) == pytest.approx(1.0, rel=REL)


def test_a_zero_day_lease_is_handled_without_raising(km) -> None:
    """`StitchedComp.effective_dom` is `ge=0`, so a listing removed on its own
    listing date is representable and can reach the estimator.

    QA deliberately does NOT pin what S(0) should be in that case — the AC says
    "S(0)=1" while a day-0 lease is a real event at day 0, and reconciling the
    two (clamp DOM to >= 1 at the pipeline boundary, vs. let S(0) < 1) is a
    decision for the PM/owner, not QA's to make inside a test. Flagged in the
    handoff. What is pinned is only what holds under either answer: no crash,
    and survival stays inside [0, 1].
    """
    estimate = km.km_estimate([0, 5, 9], [False, False, True], [1.0, 1.0, 1.0])
    for step in _steps(estimate):
        assert 0.0 <= step.survival <= 1.0, (
            f"S({step.day}) = {step.survival} is outside [0, 1]"
        )
