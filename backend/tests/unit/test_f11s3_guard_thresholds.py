"""F11-S3 [BE] Insufficient-evidence guard — QA-authored, per `AGENT_QA.md`'s
protocol. Written before any developer branch exists.

BACKGROUND (see the F11-S3 dispatch / QUEUE.md row 6 in full): the guard
itself is not new code to write — `pipeline/pricetest.py`'s
`_band_guard_reason` / `_edge_guard_reason` already implement the trip rule,
and the bulk of this story's [INVARIANT] text is already exercised by tests
written during F11-S1/F11-S2's QA and developer passes:

* mutual exclusivity by construction: `test_derivation_graph.py::
  test_a_curve_result_and_a_guard_result_cannot_be_confused` (type level) and
  `test_f11s2_curve_reaches_derive.py::test_a_response_is_a_curve_or_a_guard_
  and_never_both` (wire level, over real data).
* "trips whenever evidence is thin", both real-data regressions and the
  per-edge owner ruling: `test_ws1a_guard_reason_honesty.py`,
  `test_f11s2_curve_reaches_derive.py::
  test_thin_evidence_still_trips_the_guard_after_the_curve_exists`,
  `test_f11s2_km_dev.py::test_the_guard_trips_when_only_one_drift_edge_is_thin`.
* all-k-censored trips even with >= 3 in range: `test_f11s2_km_dev.py::
  test_an_all_censored_neighbour_set_never_reaches_the_estimator` (7
  same-premium, all-censored comps).

This file closes the gaps that survive that inventory:

1. **The story's own literal regression scenario is not pinned anywhere by
   its own shape.** Every existing thin-evidence regression uses either the
   real `ws1-real` fixture at a hardcoded dollar figure, or a tight single
   cluster with one drift edge knocked out. Nobody has built the exact shape
   the AC states in prose — "evidence clustered at −2%…+4% and one comp at
   +10%, a +16.5% candidate" — the literal prototype failure this story
   exists to kill. `test_the_prototype_failure_scenario_renders_the_guard_
   not_a_curve` below is that regression, built from the AC's own numbers.

2. **"Usable neighbor = selected, non-excluded" has never been pinned at the
   guard's own threshold boundary.** `test_f11s2_km_dev.py::
   test_a_zero_weight_comp_cannot_reach_the_curve` proves an excluded comp's
   DOM never becomes a curve step, on a pool with 4 other included comps —
   it never puts the *excluded* comp in a position to be the difference
   between 2 and 3 usable in-range neighbours. `test_an_excluded_near_
   neighbor_cannot_supply_the_third_usable_slot` below does exactly that:
   2 included in-range comps + 1 excluded in-range comp must still trip the
   guard, because the real usable count is 2, not 3.

Layer: L1 throughout — `price_test()` is directly importable and callable
with plain values (AGENT_QA.md decision procedure item 1), same seam
`test_f11s2_km_dev.py` and `test_derivation_graph_dev.py` already use.

[DEFAULT] note: `IN_RANGE_DISTANCE` (0.03) and `MIN_USABLE_IN_RANGE` (3) are
imported from `pipeline.pricetest` rather than hardcoded here, so these
tests keep asserting the *outcome* the [INVARIANT] requires ("thin evidence
trips the guard") against whatever the current [DEFAULT] threshold values
are, rather than pinning "3" and "0.03" as sacred numbers a developer could
never legitimately retune (AGENT_QA.md's rule).
"""

from __future__ import annotations

from datetime import date

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import Anchor, Band, CurveResult, GuardResult
from rentcomp.pipeline.pricetest import IN_RANGE_DISTANCE, MIN_USABLE_IN_RANGE, price_test

HORIZONS = (14, 30, 45, 60)


def make_comp(
    address: str,
    *,
    dom: int = 30,
    censored: bool = False,
) -> StitchedComp:
    return StitchedComp(
        address=address,
        unit=None,
        lat=41.9,
        lng=-87.68,
        beds=2.0,
        baths=1.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=censored,
        removal_class=None if censored else "confirmed",
        cohort_year=2026,
        first_listed=date(2026, 1, 1),
    )


def make_anchor(low: float, mid: float, high: float) -> Anchor:
    """A zero-drift-sensitivity anchor band, so all three edges share one
    candidate premium and this module can reason about a single number
    instead of the owner's any-edge ruling (that ruling has its own coverage
    already; see the module docstring)."""
    return Anchor(
        rent=Band[float](low=low, mid=mid, high=high),
        psf=Band[float](low=low / 1000.0, mid=mid / 1000.0, high=high / 1000.0),
        drift_pct=0.0,
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
    included: list[bool] | None = None,
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
        included if included is not None else [True] * len(comps),
        knn_k,
        4.0,
        HORIZONS,
    )


# ---------------------------------------------------------------------------
# Gap 1 — the story's own literal prototype-failure scenario
# ---------------------------------------------------------------------------


def test_the_prototype_failure_scenario_renders_the_guard_not_a_curve() -> None:
    """The AC's own regression, built from the AC's own numbers:

    "with evidence clustered at −2%…+4% and one comp at +10%, a +16.5%
    candidate must render the guard state (with nearest comps + their
    distances), never a curve."

    A candidate anchor of $2,000 makes premium-percent and dollars-off-2000
    the same number, so the fixture can be built directly from the prose.
    """
    comps = [
        make_comp("101 W Cluster St", dom=12),
        make_comp("102 W Cluster St", dom=18),
        make_comp("103 W Cluster St", dom=9),
        make_comp("104 W Cluster St", dom=25),
        make_comp("105 W Outlier St", dom=40),
    ]
    # −2%, −1%, 0%, +4% clustered; one comp at +10%, well outside range.
    premiums: list[float | None] = [-0.02, -0.01, 0.0, 0.04, 0.10]
    anchor_value = make_anchor(2000.0, 2000.0, 2000.0)

    # +16.5% candidate premium => candidate_rent = 2000 * 1.165
    candidate_rent = 2000.0 * 1.165
    result = run(candidate_rent, anchor_value, comps, premiums)

    assert isinstance(result, GuardResult), (
        f"a +16.5% candidate against evidence clustered at -2%..+4% (plus one "
        f"comp at +10%) must render the guard state, never a curve — got "
        f"{type(result).__name__}. This is the exact observed prototype "
        "failure this story exists to kill (NORTH_STAR, F11-S3 AC)."
    )
    assert result.reason == "too_few_in_range", (
        f"expected too_few_in_range (every comp is >{IN_RANGE_DISTANCE * 100:.0f} "
        f"premium points from the candidate); got {result.reason!r}"
    )
    # "+ nearest comps with their distances" — the guard must still surface
    # evidence, not an empty list, so the user can see how thin it actually is.
    assert result.neighbors, "the guard fired with no neighbours to show as 'nearest evidence'"
    for neighbor in result.neighbors:
        assert neighbor.distance >= 0.0
    nearest = min(result.neighbors, key=lambda n: n.distance)
    # The nearest evidence is the +10% comp, ~6.5 points below the +16.5% ask —
    # the exact kind of readout the epic flow names ("nearest evidence: +4.0%,
    # 12.5pts below your ask").
    assert nearest.premium == max(p for p in premiums if p is not None), (
        "the nearest-by-distance neighbour is not the closest comp by premium — "
        "'nearest comps with their distances' would mislead the user about which "
        "evidence is actually closest"
    )
    in_range = sum(1 for n in result.neighbors if n.distance <= IN_RANGE_DISTANCE)
    assert in_range < MIN_USABLE_IN_RANGE, (
        f"fixture assumption broke: {in_range} neighbours are within the current "
        f"IN_RANGE_DISTANCE of {IN_RANGE_DISTANCE} — this scenario is supposed to "
        "be genuinely thin, not thin by a threshold technicality"
    )


def test_the_prototype_cluster_alone_is_not_the_bug_a_well_placed_candidate_gets_a_curve() -> None:
    """The mirror check on the same fixture: the guard is not simply "trips on
    this pool no matter what" — it is the CANDIDATE landing far from the
    evidence that trips it. A candidate placed inside the cluster (e.g. at
    +1%) must reach the curve, all else unchanged. This is what rules out a
    guard that is secretly unconditional (a defect this test would catch even
    though the primary regression above would not)."""
    comps = [
        make_comp("101 W Cluster St", dom=12),
        make_comp("102 W Cluster St", dom=18),
        make_comp("103 W Cluster St", dom=9),
        make_comp("104 W Cluster St", dom=25),
        make_comp("105 W Outlier St", dom=40),
    ]
    premiums: list[float | None] = [-0.02, -0.01, 0.0, 0.04, 0.10]
    anchor_value = make_anchor(2000.0, 2000.0, 2000.0)

    candidate_rent = 2000.0 * 1.01  # +1% — inside the cluster
    result = run(candidate_rent, anchor_value, comps, premiums)

    assert isinstance(result, CurveResult), (
        f"a +1% candidate sits inside the -2%..+4% cluster (4 of 5 comps within "
        f"{IN_RANGE_DISTANCE} premium points, none censored) — got "
        f"{type(result).__name__} with reason={getattr(result, 'reason', None)!r}"
    )


# ---------------------------------------------------------------------------
# Gap 2 — "usable neighbor = selected, non-excluded" at the threshold boundary
# ---------------------------------------------------------------------------


def test_an_excluded_near_neighbor_cannot_supply_the_third_usable_slot() -> None:
    """[DEFAULT] text: 'usable neighbor = selected, non-excluded.' Two
    included, in-range, uncensored comps plus one EXCLUDED comp that is also
    in-range must still trip the guard — the excluded comp may not count
    toward `MIN_USABLE_IN_RANGE` no matter how close its premium is."""
    comps = [
        make_comp("201 W Included St", dom=15),
        make_comp("202 W Included St", dom=20),
        make_comp("203 W Excluded St", dom=25),  # would-be third neighbour
    ]
    premiums: list[float | None] = [0.0, 0.01, 0.02]
    anchor_value = make_anchor(2000.0, 2000.0, 2000.0)
    included = [True, True, False]

    result = run(2000.0, anchor_value, comps, premiums, included=included)

    assert isinstance(result, GuardResult), (
        f"2 included in-range comps + 1 excluded in-range comp must trip the guard "
        f"(real usable count is 2, below MIN_USABLE_IN_RANGE={MIN_USABLE_IN_RANGE}) "
        f"— got {type(result).__name__}"
    )
    assert result.reason == "too_few_in_range"
    assert all(n.key != "k2" for n in result.neighbors), (
        "the excluded comp (k2) was retrieved as a neighbour at all — retrieval's "
        "own pool must never admit an excluded comp, per `included`"
    )


def test_including_that_same_neighbor_flips_the_guard_to_a_curve() -> None:
    """The control for the test above: the ONLY change is `included[2]` going
    True, and the exact same pool now has 3 real usable neighbours and must
    reach the curve. Isolates that the guard's threshold, not some other
    difference between the fixtures, is what is under test."""
    comps = [
        make_comp("201 W Included St", dom=15),
        make_comp("202 W Included St", dom=20),
        make_comp("203 W Excluded St", dom=25),
    ]
    premiums: list[float | None] = [0.0, 0.01, 0.02]
    anchor_value = make_anchor(2000.0, 2000.0, 2000.0)

    result = run(2000.0, anchor_value, comps, premiums, included=[True, True, True])

    assert isinstance(result, CurveResult), (
        f"with all 3 comps included, in-range, and uncensored, the guard must not "
        f"trip — got {type(result).__name__} with reason="
        f"{getattr(result, 'reason', None)!r}"
    )
