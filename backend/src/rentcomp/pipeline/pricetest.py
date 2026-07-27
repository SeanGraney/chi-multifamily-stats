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

#: PLACEHOLDER RULE (F0-S2 → replaced by F11-S3 for the decision, F11-S2 for
#: the curve): the price test always returns the **guard** branch, with reason
#: `too_few_in_range` and an empty neighbour list.
#:
#: This is not a fabricated verdict — it is the true one given the rest of the
#: pipeline: `stats/knn.select_neighbors` is itself a stub that retrieves
#: nothing, so there are genuinely zero usable neighbours, which is genuinely
#: "too few in range". The alternative — synthesising a curve — would put a
#: survival probability and an expected-vacancy figure on screen with no
#: evidence behind them, which is the one thing NORTH_STAR says this tool must
#: never do.
#:
#: `CurveResult` is therefore never constructed in F0-S2. It exists in the
#: contract (and in the OpenAPI document, so D12's codegen emits both arms of
#: the union) and F11-S2 fills it in.
_STUB_GUARD_DECISION = True


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

    # _STUB_GUARD_DECISION (F11-S3 decides; F11-S2 builds the curve).
    return GuardResult(
        candidate_rent=candidate_rent,
        candidate_premium=premium_band,
        bucket=bucket,
        reason="all_censored"
        if neighbors and all(neighbor.censored for neighbor in neighbors)
        else "too_few_in_range",
        neighbors=neighbors,
    )


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
