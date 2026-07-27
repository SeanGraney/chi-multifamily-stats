"""WS-1 — F10-S1 [INVARIANT] bucket outcome statistics, replacing
`_STUB_OUTCOME_STATS` in `pipeline/buckets.py::bucket_stats`. QA-authored,
written RED before any implementation exists (AGENT_QA.md protocol).

Layer 1: `bucket_stats()` is directly importable and callable with plain
`StitchedComp` instances -- no HTTP round trip needed (AGENT_QA.md decision
procedure, item 1). Membership/count/dollar-translation are already real
(F0-S2); only the outcome statistics are stubbed to `None` today, so every
assertion here is about `leased_dom_median/min/max` and
`cut_before_lease_rate`.

NORTH_STAR exclusion rules under test (F4-S8, restated as this story's
contract): pendings are EXCLUDED from leased stats entirely (not merely
marked); provisionals are COUNTED and marked; censored comps are NEVER
counted as leased (their DOM-so-far is a floor, reported separately in
`censored_floors`).
"""

from __future__ import annotations

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.pipeline.buckets import bucket_of, bucket_stats


def _comp(
    *,
    address: str,
    dom: int,
    censored: bool = False,
    removal_class: str | None = None,
    cuts: int = 0,
) -> StitchedComp:
    cut_history = tuple(
        PriceCut(on="2026-01-01", from_price=2100.0, to_price=2000.0) for _ in range(cuts)
    )
    return StitchedComp(
        address=address,
        unit=None,
        lat=41.83,
        lng=-87.66,
        beds=3,
        baths=2.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=censored,
        removal_class=removal_class,
        cohort_year=2026,
        cut_history=cut_history,
    )


def _stats_for(comps: list[StitchedComp], half_width_pct: float = 4.0):
    keys = [f"key-{i}" for i in range(len(comps))]
    premiums: list[float | None] = [0.0] * len(comps)  # every comp lands "at market"
    buckets = [bucket_of(p, half_width_pct) for p in premiums]
    included = [True] * len(comps)
    stats = bucket_stats(comps, keys, premiums, included, buckets, None, half_width_pct)
    return {s.id: s for s in stats}


def test_leased_dom_median_min_max_exclude_pending_and_censored_but_include_provisional() -> None:
    comps = [
        _comp(address="1 W A St", dom=20, removal_class="confirmed", cuts=0),
        _comp(address="2 W B St", dom=40, removal_class="confirmed", cuts=1),
        _comp(address="3 W C St", dom=2, removal_class="pending"),  # excluded entirely
        _comp(address="4 W D St", dom=100, censored=True),  # censored: never leased
        _comp(address="5 W E St", dom=25, removal_class="provisional", cuts=0),  # counted + marked
    ]
    at = _stats_for(comps)["at"]

    assert at.count == 5, "bucket MEMBERSHIP counts every comp in the bucket, regardless of removal state"
    assert at.leased_dom_median == 25.0, (
        f"leased set is {{20, 25, 40}} (pending and censored excluded) -> median 25, "
        f"got {at.leased_dom_median}"
    )
    assert at.leased_dom_min == 20
    assert at.leased_dom_max == 40
    assert at.censored_floors == [100], "the censored comp's DOM-so-far must appear as a floor, never as a leased outcome"
    assert at.leased_dom_max != 100, "a censored floor must never be counted into leased_dom_max"


def test_cut_before_lease_rate_is_over_leased_comps_only() -> None:
    comps = [
        _comp(address="1 W A St", dom=20, removal_class="confirmed", cuts=0),
        _comp(address="2 W B St", dom=40, removal_class="confirmed", cuts=1),
        _comp(address="3 W C St", dom=2, removal_class="pending", cuts=5),  # excluded even though cut
        _comp(address="4 W D St", dom=100, censored=True, cuts=3),  # excluded even though "cut"
        _comp(address="5 W E St", dom=25, removal_class="provisional", cuts=0),
    ]
    at = _stats_for(comps)["at"]
    # leased set = {comp1 (0 cuts), comp2 (1 cut), comp5 (0 cuts)} = 3 leased comps, 1 with a cut.
    assert at.cut_before_lease_rate == 1.0 / 3.0, (
        f"rate = (leased comps with >=1 cut) / (leased comps) = 1/3, "
        f"got {at.cut_before_lease_rate} -- a pending/censored comp's cuts must not enter either "
        f"the numerator or the denominator"
    )


def test_provisional_is_marked_and_counted_pending_is_excluded_and_unmarked() -> None:
    comps = [
        _comp(address="1 W A St", dom=20, removal_class="confirmed"),
        _comp(address="2 W B St", dom=5, removal_class="pending"),
        _comp(address="3 W C St", dom=9, removal_class="provisional"),
    ]
    at = _stats_for(comps)["at"]
    assert at.provisional_count == 1
    assert at.leased_dom_min == 9, "the provisional comp (dom=9) must be counted into the leased set"


def test_empty_bucket_reports_none_not_zero() -> None:
    """NORTH_STAR: "buckets never interpolate; an empty bucket renders as a
    dash, never an estimate." A bucket with zero members must report `None`
    on every outcome statistic, never `0` or `0.0` (which would read as a
    real, computed rate/median of nothing)."""
    comps = [_comp(address="1 W A St", dom=20, removal_class="confirmed")]  # lands in "at" only
    stats = _stats_for(comps)
    for empty_id in ("below", "above"):
        empty = stats[empty_id]
        assert empty.count == 0
        assert empty.leased_dom_median is None
        assert empty.leased_dom_min is None
        assert empty.leased_dom_max is None
        assert empty.cut_before_lease_rate is None
        assert empty.comp_keys == []


def test_bucket_with_only_pending_and_censored_comps_has_no_leased_stats() -> None:
    """No leased comp at all (only pending/censored) -> still `None`, not a
    fabricated 0 or a divide-by-zero rate."""
    comps = [
        _comp(address="1 W A St", dom=3, removal_class="pending"),
        _comp(address="2 W B St", dom=50, censored=True),
    ]
    at = _stats_for(comps)["at"]
    assert at.count == 2
    assert at.leased_dom_median is None
    assert at.cut_before_lease_rate is None
    assert at.censored_floors == [50]
