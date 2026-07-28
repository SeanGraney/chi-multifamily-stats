"""F11-S2 Layer 1 — developer-authored companion to QA's KM specs.

QA's `test_f11s2_km_estimator.py` / `test_f11s2_km_properties.py` own the
product-limit arithmetic itself (seven hand-derived fixtures plus the
metamorphic weight oracle) and `test_f11s2_curve_reaches_derive.py` owns the
wire contract. This file owns what QA's plan calls for but did not write:

1. **`expected_days`** — the restricted-mean integration the PM ruled into
   this story (ruling 4). QA's API test only pins that the number is present
   and internally honest (`0 <= days <= truncated_at`), which is deliberately
   loose because F11-S4 owns the final formula. The areas below are
   hand-computed, so the arithmetic is checkable without running anything.

2. **The per-edge guard** (owner ruling: the guard trips if it trips at ANY
   of d-2 / d / d+2). Nothing else in the suite can see this: the wire
   reports `Neighbor.distance` against the *mid* candidate premium, so a
   band-edge trip is invisible from the response. It is asserted here at the
   `price_test` seam, where each edge's own candidate premium is in scope.

3. **The curve branch at the seam** — that the estimator is actually reached,
   with the neighbour set the retrieval chose, on a synthetic pool whose
   expected answer can be read off by hand.
"""

from __future__ import annotations

import pytest

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import Anchor, Band, CurveResult, GuardResult
from rentcomp.pipeline.pricetest import IN_RANGE_DISTANCE, price_test
from rentcomp.stats.km import expected_days, km_estimate

HORIZONS = (14, 30, 45, 60)


# ---------------------------------------------------------------------------
# expected_days — area under the truncated step function
# ---------------------------------------------------------------------------


def test_area_under_a_fully_observed_curve() -> None:
    """[5, 10, 20], all leased, unit weights (QA's fixture A), truncated at 20.

        S = 1    on [0, 5)    ->  5 x 1   = 5
        S = 2/3  on [5, 10)   ->  5 x 2/3 = 10/3
        S = 1/3  on [10, 20)  -> 10 x 1/3 = 10/3
                                          = 35/3
    """
    estimate = km_estimate([5, 10, 20], [False, False, False])
    assert estimate.truncated_at == 20
    assert expected_days(estimate, 20) == pytest.approx(35 / 3, rel=1e-12)


def test_the_censored_tail_is_counted_not_extrapolated() -> None:
    """[4, 9, 12, 20] with only day 4 leased (QA's fixture E), truncated at 20.

        S = 1    on [0, 4)   ->  4 x 1    = 4
        S = 3/4  on [4, 20)  -> 16 x 0.75 = 12
                                           = 16

    The three censored comps flatten the curve rather than driving it to 0, so
    the area over the observed window is *larger* than a naive "everything
    leases eventually" reading would give. Understating this number is the
    dangerous direction for a pricing tool.
    """
    estimate = km_estimate([4, 9, 12, 20], [False, True, True, True])
    assert estimate.truncated_at == 20
    assert expected_days(estimate, 20) == pytest.approx(16.0, rel=1e-12)


def test_truncating_before_the_first_event_is_just_the_window() -> None:
    """S == 1 everywhere before the first event, so the area is the window."""
    estimate = km_estimate([5, 10, 20], [False, False, False])
    assert expected_days(estimate, 3) == pytest.approx(3.0, rel=1e-12)


def test_an_event_exactly_at_the_truncation_point_contributes_no_area() -> None:
    """The drop at day T has zero width inside [0, T] — the interval ends
    where the step lands. Getting this wrong is an off-by-one-segment error
    that no other test in the suite would localize."""
    estimate = km_estimate([5, 10, 20], [False, False, False])
    assert expected_days(estimate, 5) == pytest.approx(5.0, rel=1e-12)


def test_an_all_censored_cohort_has_the_whole_window_as_its_area() -> None:
    """No observed lease means no drop: S == 1 over the whole window. This is
    a state F11-S3 refuses to *render*, but the arithmetic must still be the
    honest one rather than a fabricated decay."""
    estimate = km_estimate([10, 12, 30], [True, True, True])
    assert estimate.truncated_at is None
    assert expected_days(estimate, 30) == pytest.approx(30.0, rel=1e-12)


def test_a_non_positive_window_has_no_area() -> None:
    estimate = km_estimate([5, 10], [False, False])
    assert expected_days(estimate, 0) == 0.0


def test_area_grows_with_the_window_and_never_exceeds_it() -> None:
    """Two properties the band relies on: `expected_days` is non-decreasing in
    the truncation point (so the common-window choice is a lower bound, not an
    arbitrary one), and it can never exceed the window, since S <= 1."""
    estimate = km_estimate(
        [3, 8, 8, 14, 30], [False, False, True, False, True], [1.0, 2.0, 1.0, 1.0, 3.0]
    )
    previous = 0.0
    for window in range(0, 40):
        area = expected_days(estimate, window)
        assert area >= previous - 1e-12, f"area shrank at T={window}"
        assert area <= window + 1e-12, f"area {area} exceeds the window {window}"
        previous = area


# ---------------------------------------------------------------------------
# the price-test seam — the band-edge guard and the curve branch
# ---------------------------------------------------------------------------


def make_comp(
    address: str,
    *,
    dom: int = 30,
    censored: bool = False,
    ask: float = 2000.0,
) -> StitchedComp:
    return StitchedComp(
        address=address,
        unit="1",
        lat=41.9,
        lng=-87.68,
        beds=2.0,
        baths=1.0,
        sqft=1000.0,
        initial_ask=ask,
        effective_dom=dom,
        censored=censored,
        removal_class=None if censored else "confirmed",
        cohort_year=2026,
        withdrawal_suspect=False,
        cut_history=(),
    )


def make_anchor(low: float, mid: float, high: float) -> Anchor:
    """An anchor whose rent band is stated directly, so a test can place the
    candidate premium at any point of the band it likes."""
    return Anchor(
        rent=Band[float](low=low, mid=mid, high=high),
        psf=Band[float](low=low / 1000.0, mid=mid / 1000.0, high=high / 1000.0),
        drift_pct=7.0,
        drift_sensitivity_pts=2.0,
        subject_sqft=1000.0,
        n_comps=1,
        comp_keys=[],
    )


def run(
    candidate_rent: float,
    anchor_value: Anchor,
    comps: list[StitchedComp],
    premiums: list[float | None],
    weights: list[float] | None = None,
    knn_k: int = 7,
):
    keys = [f"k{index}" for index in range(len(comps))]
    return price_test(
        candidate_rent,
        anchor_value,
        comps,
        keys,
        premiums,
        weights if weights is not None else [1.0] * len(comps),
        [True] * len(comps),
        knn_k,
        4.0,
        HORIZONS,
    )


def test_the_guard_trips_when_only_one_drift_edge_is_thin() -> None:
    """Owner ruling, and the reason it is not observable from the response:
    `Neighbor.distance` is reported against the MID candidate premium, so a
    band edge with no evidence looks perfectly well-evidenced on the wire.

    Setup: evidence is a tight cluster at premium 0.00-0.02. The candidate
    premium band straddles it — mid lands right in the cluster, but the LOW
    edge (a lower anchor, hence a higher premium for the same rent) lands 10
    premium points above everything. Judged on the mid alone this is a curve;
    judged per edge it is the guard, and the guard is the conservative answer
    the owner ruled for.
    """
    comps = [make_comp(f"{100 + index} W Fake St", dom=10 + index) for index in range(6)]
    premiums: list[float | None] = [0.0, 0.004, 0.008, 0.012, 0.016, 0.02]

    # rent 2000 against these anchors gives premiums of roughly
    # low=+0.111, mid=+0.010, high=-0.005.
    anchor_value = make_anchor(low=1800.0, mid=1980.0, high=2010.0)
    result = run(2000.0, anchor_value, comps, premiums)

    assert isinstance(result, GuardResult), (
        "the low drift edge sits ~11 premium points above every comp, so the guard "
        f"must trip for the whole band; got {type(result).__name__}"
    )
    assert result.reason == "too_few_in_range"
    assert result.candidate_premium.low > IN_RANGE_DISTANCE, (
        "fixture assumption broke: the low edge is supposed to be out of range"
    )
    in_range_at_mid = sum(
        1 for neighbor in result.neighbors if neighbor.distance <= IN_RANGE_DISTANCE
    )
    assert in_range_at_mid >= 3, (
        "fixture assumption broke: this test only means something while the MID "
        "edge looks well-evidenced on the wire"
    )


def test_a_well_evidenced_candidate_reaches_the_estimator() -> None:
    """The curve branch, end to end at the seam, on a pool whose answer is
    readable by hand: seven comps at essentially the same premium, six leased
    at 10/20/30/40/50/60 days and one still active at day 45.

    With k=7 every edge retrieves the same seven comps, so all three band
    edges must produce the identical curve — and the censored comp must appear
    nowhere in it as an event.
    """
    doms = [10, 20, 30, 40, 50, 60]
    comps = [make_comp(f"{200 + index} W Fake St", dom=dom) for index, dom in enumerate(doms)]
    comps.append(make_comp("999 W Fake St", dom=45, censored=True))
    premiums: list[float | None] = [0.0] * len(comps)

    anchor_value = make_anchor(low=1990.0, mid=2000.0, high=2010.0)
    result = run(2000.0, anchor_value, comps, premiums)

    assert isinstance(result, CurveResult), (
        f"seven in-range neighbours, six of them leased; got {type(result).__name__}"
    )

    for edge in ("low", "mid", "high"):
        curve = getattr(result.curve, edge)
        assert [point.day for point in curve.points] == doms, (
            f"{edge}: steps must land on the six leased DOMs only — day 45 is a "
            "CENSORED comp's floor and can never be an event"
        )
        # n_w at day 50: the day-50 and day-60 comps. The comp censored at day
        # 45 has already left; the tie convention only keeps a censoring at
        # EXACTLY the event day.
        assert curve.points[4].at_risk == pytest.approx(2.0)
        assert curve.truncated_at == 60

    # S drops 1 -> 6/7 at day 10 (7 at risk, 1 event), then the censored comp
    # leaves at 45, so day 50 divides by 2 rather than by 3.
    mid = result.curve.mid.points
    assert mid[0].survival == pytest.approx(6 / 7, rel=1e-12)
    assert mid[-1].survival == pytest.approx(0.0, abs=1e-15)

    assert [readout.day for readout in result.horizons] == list(HORIZONS)
    assert result.horizons[0].survival.mid == pytest.approx(6 / 7, rel=1e-12)

    assert result.expected_vacancy.truncated_at == 60
    assert 0.0 < result.expected_vacancy.days.mid <= 60.0
    assert result.expected_vacancy.cost.mid == pytest.approx(
        result.expected_vacancy.days.mid * 2000.0 / 30.0, rel=1e-12
    )


def test_an_all_censored_neighbour_set_never_reaches_the_estimator() -> None:
    """`all_censored` is checked before the curve, so a cohort with no observed
    lease can never produce a `CurveResult` with an empty curve — the guard and
    the estimator agree about the same cohorts (QA's fixture F)."""
    comps = [make_comp(f"{300 + index} W Fake St", dom=20 + index, censored=True) for index in range(7)]
    result = run(2000.0, make_anchor(1990.0, 2000.0, 2010.0), comps, [0.0] * 7)
    assert isinstance(result, GuardResult)
    assert result.reason == "all_censored"


def test_a_zero_weight_comp_cannot_reach_the_curve() -> None:
    """F0-S3's weight semantics survive the whole path: a weight-0 comp is
    `excluded` by `classify_membership`, so it never enters the retrieval pool
    — and even if it did, `km_estimate` drops it before computing. Asserted as
    the outcome that matters: its DOM is not a step."""
    comps = [make_comp(f"{400 + index} W Fake St", dom=10 * (index + 1)) for index in range(4)]
    comps.append(make_comp("777 W Fake St", dom=7))
    weights = [1.0, 1.0, 1.0, 1.0, 0.0]
    result = price_test(
        2000.0,
        make_anchor(1990.0, 2000.0, 2010.0),
        comps,
        [f"k{index}" for index in range(5)],
        [0.0] * 5,
        weights,
        [True, True, True, True, False],  # membership: weight 0 => excluded
        7,
        4.0,
        HORIZONS,
    )
    assert isinstance(result, CurveResult)
    for edge in ("low", "mid", "high"):
        days = [point.day for point in getattr(result.curve, edge).points]
        assert 7 not in days, f"{edge}: a weight-0 comp created an event day"
        assert days == [10, 20, 30, 40]
