"""The price test — kNN retrieval → guard → Kaplan-Meier (F11-S1/S3/S2).

**STUB (F0-S2)** for the decision and the curve; real for the seam. See
`_STUB_GUARD_DECISION`.

THE BAND IS THE POINT (owner ruling, ADR-001 §2.2)
--------------------------------------------------
Premium is time-local and drift-free, but the *anchor* is drift-dependent, and
`candidate_premium = candidate_rent / anchor − 1`. So the neighbour set, the
guard decision and the curve all differ across the three drift points — the
guard can trip at d−2 and pass at d. The owner's ruling is conservative: **the
guard trips if it trips at ANY edge**, on the honesty invariant that a band
whose edge has no evidence is not a band.

F0-S2 pins that at the interface: this stage receives the candidate premium as
a `Band` and runs retrieval once per drift point, so a band-aware decision is
already what the code is shaped to make. F11-S3 owns the trip rule itself
(fewer than 3 usable neighbours within ±3 premium points, or all neighbours
censored).

Retrieval is handed **premium floats only** — never comp records — so the
outcome cannot reach the distance calculation (D19a). Mapping indices back to
comps happens here, after retrieval has finished.
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import Anchor, Band, GuardResult, Neighbor, PriceTest
from rentcomp.pipeline.buckets import bucket_of
from rentcomp.stats.knn import select_neighbors

__all__ = ["candidate_premium_band", "price_test"]

#: F11-S3 [DEFAULT] threshold: "within ±3 premium points of candidate",
#: expressed in `Neighbor.distance`'s own units (premium is a ratio, e.g.
#: 0.04 == 4%, so 3 points == 0.03).
IN_RANGE_DISTANCE = 0.03

#: F11-S3 [DEFAULT] threshold: fewer than this many usable, in-range
#: neighbors is "too few".
MIN_USABLE_IN_RANGE = 3

#: WS-1a (architecture checkpoint 2, QUEUE.md row 6a): `CurveResult` is never
#: constructed here — F11-S2 (the Kaplan-Meier curve) does not exist yet, so
#: `price_test` always returns the **guard** branch. That much is unchanged
#: from F0-S2. What changed: the guard's `reason` is now computed from what
#: retrieval actually found (`_guard_reason` below), not hardcoded — WS-1
#: made `select_neighbors` real, so a hardcoded reason is no longer honest
#: (the real derive can and does retrieve real, in-range, uncensored
#: neighbours; reporting "too few" regardless would be a stated reason with
#: no evidence behind it, exactly what NORTH_STAR forbids).
#:
#: `CurveResult` is still never built. It exists in the contract (and in the
#: OpenAPI document, so D12's codegen emits both arms of the union) and
#: F11-S2 fills it in. When F11-S3's own real trip rule does not actually
#: fire (>= 3 usable in-range neighbours, not all censored) there is
#: currently nowhere honest to land that in the wire contract short of
#: inventing a curve — `reason="curve_not_available"` names that gap
#: explicitly (PM ruling, WS-1a dispatch) rather than silently claiming
#: `too_few_in_range` when there are plenty.


def candidate_premium_band(candidate_rent: float, anchor_value: Anchor) -> Band[float]:
    """`candidate_rent / anchor_rent − 1`, at each of the three drift points.

    A band rather than a point because the anchor is a band — the same reason
    the guard must see all three edges.
    """
    return Band[float](
        low=candidate_rent / anchor_value.rent.low - 1.0,
        mid=candidate_rent / anchor_value.rent.mid - 1.0,
        high=candidate_rent / anchor_value.rent.high - 1.0,
    )


def price_test(
    candidate_rent: float | None,
    anchor_value: Anchor | None,
    comps: Sequence[StitchedComp],
    keys: Sequence[str],
    premiums: Sequence[float | None],
    weights: Sequence[float],
    included: Sequence[bool],
    knn_k: int,
    half_width_pct: float,
) -> PriceTest | None:
    """The price test for `candidate_rent`, or `None` when there is none to run.

    `None` when no candidate rent was supplied (the Results view, before a
    price has been tested) — and also when there is no anchor, because a
    candidate rent has no premium to be expressed as without one. ADR-001
    §1.2's sketch says "None iff candidate_rent is None"; the anchor-less case
    is a deviation logged in the F0-S2 handoff, taken because the alternative
    is emitting a fabricated 0.0 premium band for a candidate the evidence
    cannot place at all.
    """
    if candidate_rent is None or anchor_value is None:
        return None

    premium_band = candidate_premium_band(candidate_rent, anchor_value)

    # Retrieval is fed plain premium floats — no records, no outcomes (D19a).
    pool_indices = [
        index
        for index, (premium, keep) in enumerate(zip(premiums, included, strict=True))
        if keep and premium is not None
    ]
    pool_premiums = [premiums[index] for index in pool_indices]

    # Once per drift point: the guard's decision must have all three in hand
    # (owner ruling). Union of the three neighbour sets, so the render can show
    # every comp any edge relied on.
    chosen: list[int] = []
    for candidate in (premium_band.low, premium_band.mid, premium_band.high):
        for offset in select_neighbors(pool_premiums, candidate, knn_k):
            index = pool_indices[offset]
            if index not in chosen:
                chosen.append(index)

    neighbors = [
        _neighbor(comps[index], keys[index], premiums[index], weights[index], premium_band.mid)
        for index in sorted(chosen)
    ]

    # `bucket_of` is `None` only for a comp with no premium; the candidate
    # always has one here, so the fallback below is unreachable.
    bucket = bucket_of(premium_band.mid, half_width_pct) or "at"

    return GuardResult(
        candidate_rent=candidate_rent,
        candidate_premium=premium_band,
        bucket=bucket,
        reason=_guard_reason(neighbors),
        neighbors=neighbors,
    )


def _guard_reason(neighbors: Sequence[Neighbor]) -> str:
    """F11-S3's real trip rule, named honestly (WS-1a).

    Priority mirrors F0-S2's original ordering: "all k censored" trips the
    guard regardless of how many are in range (an all-censored neighbour set
    has no leased outcome to build a curve from no matter how close those
    neighbours are), so it is checked first. Otherwise the guard trips iff
    fewer than `MIN_USABLE_IN_RANGE` retrieved neighbours are within
    `IN_RANGE_DISTANCE` of the candidate. When NEITHER real trip condition
    holds, F11-S3 says the guard should not trip at all — but there is still
    no `CurveResult` to return (F11-S2 out of scope), so that state is named
    rather than mislabelled as one of the two real trip reasons.
    """
    if neighbors and all(neighbor.censored for neighbor in neighbors):
        return "all_censored"
    in_range = sum(1 for neighbor in neighbors if neighbor.distance <= IN_RANGE_DISTANCE)
    if in_range < MIN_USABLE_IN_RANGE:
        return "too_few_in_range"
    return "curve_not_available"


def _neighbor(
    comp: StitchedComp,
    key: str,
    premium: float | None,
    weight: float,
    candidate: float,
) -> Neighbor:
    """A retrieved comp, with its distance in premium space.

    The outcome pair travels here for the KM estimator and for display — it
    arrives *after* retrieval chose this comp, never before (D19a).
    """
    value = 0.0 if premium is None else premium
    return Neighbor(
        key=key,
        premium=value,
        distance=abs(candidate - value),
        effective_dom=comp.effective_dom,
        censored=comp.censored,
        removal_class=comp.removal_class,
        weight=weight,
        cohort_year=comp.cohort_year,
        cut_count=len(comp.cut_history),
    )
