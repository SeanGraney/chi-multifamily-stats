"""The price test — kNN retrieval → guard → Kaplan-Meier (F11-S1/S3/S2).

THE BAND IS THE POINT (owner ruling, ADR-001 §2.2)
--------------------------------------------------
Premium is time-local and drift-free, but the *anchor* is drift-dependent, and
`candidate_premium = candidate_rent / anchor − 1`. So the neighbour set, the
guard decision and the curve all differ across the three drift points — the
guard can trip at d−2 and pass at d. The owner's ruling is conservative: **the
guard trips if it trips at ANY edge**, on the honesty invariant that a band
whose edge has no evidence is not a band.

F0-S2 pinned that at the interface (the candidate premium arrives as a `Band`
and retrieval runs once per drift point); F11-S2 makes the *decision* per-edge
too, because that is what makes the per-edge curve well-formed: an edge that
passed the guard has at least one uncensored, positive-weight neighbour, so
its Kaplan-Meier estimate is guaranteed to have at least one step and a
truncation point. A union-of-edges decision could pass while one individual
edge had nothing to estimate from, and a `Band[KMCurve]` with an empty edge is
not a band. F11-S3 still owns the trip *thresholds* (`MIN_USABLE_IN_RANGE`,
`IN_RANGE_DISTANCE`).

Retrieval is handed **premium floats only** — never comp records — so the
outcome cannot reach the distance calculation (D19a). Mapping indices back to
comps happens here, after retrieval has finished, and the outcome pair goes
straight to the estimator without ever re-entering a distance.

WHERE THE WIRE TYPES ARE PUT ON (CLAUDE.md, three model layers)
----------------------------------------------------------------
`stats/km.py` is the math layer: plain values in, a plain `KMEstimate` out,
importing nothing from `rentcomp` outside `rentcomp.stats`. This module is
where a `KMEstimate` becomes `models/responses.py`'s `KMCurve`/`KMPoint`, next
to the rest of the mapping.
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import (
    Anchor,
    Band,
    CurveResult,
    ExpectedVacancy,
    GuardResult,
    HorizonReadout,
    KMCurve,
    KMPoint,
    Neighbor,
    PriceTest,
)
from rentcomp.pipeline.buckets import bucket_of
from rentcomp.stats.km import KMEstimate, expected_days, km_estimate, survival_at
from rentcomp.stats.knn import select_neighbors

__all__ = ["candidate_premium_band", "price_test"]

#: The three drift points, in `Band` field order. Named once so the per-edge
#: retrieval, the per-edge guard and the per-edge curve cannot fall out of
#: step with each other.
EDGES = ("low", "mid", "high")

#: F11-S4: cost = expected vacant days × candidate rent ÷ 30. A month of rent
#: spread over its days — F11-S4 owns the cost *modelling* (it may grow a
#: concession/turnover term); this is the arithmetic its own AC states, and
#: `ExpectedVacancy.cost` is a required field, so the alternative to computing
#: it is emitting a fabricated 0.
DAYS_PER_MONTH = 30.0

#: F11-S3 [DEFAULT] threshold: "within ±3 premium points of candidate",
#: expressed in `Neighbor.distance`'s own units (premium is a ratio, e.g.
#: 0.04 == 4%, so 3 points == 0.03).
IN_RANGE_DISTANCE = 0.03

#: F11-S3 [DEFAULT] threshold: fewer than this many usable, in-range
#: neighbors is "too few".
MIN_USABLE_IN_RANGE = 3

#: WS-1a's provisional `reason="curve_not_available"` retires here. It named
#: one specific gap — F11-S3's real trip rule did not fire, so the guard had
#: no honest reason to state, and there was no `CurveResult` to return
#: instead. F11-S2 supplies the missing arm: when no edge trips, this module
#: now returns a real curve. The literal is still in `GuardResult.reason`'s
#: `Literal` (retiring it is a wire-contract change, and QA's
#: `test_derivation_graph.py` pins the current set) but it is unreachable
#: from this module — flagged for F11-S3 to remove along with the codegen.


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
    km_horizons: Sequence[int],
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

    # Once per drift point: the guard's decision must have all three in hand,
    # and each edge's curve is built from that edge's own neighbours (owner
    # ruling; see the module docstring).
    candidates = (premium_band.low, premium_band.mid, premium_band.high)
    per_edge = {
        edge: [pool_indices[offset] for offset in select_neighbors(pool_premiums, candidate, knn_k)]
        for edge, candidate in zip(EDGES, candidates, strict=True)
    }

    # Union of the three neighbour sets, so the render can show every comp any
    # edge relied on.
    chosen: list[int] = []
    for edge in EDGES:
        for index in per_edge[edge]:
            if index not in chosen:
                chosen.append(index)

    neighbors = [
        _neighbor(comps[index], keys[index], premiums[index], weights[index], premium_band.mid)
        for index in sorted(chosen)
    ]

    # `bucket_of` is `None` only for a comp with no premium; the candidate
    # always has one here, so the fallback below is unreachable.
    bucket = bucket_of(premium_band.mid, half_width_pct) or "at"

    reason = _band_guard_reason(per_edge, candidates, comps, premiums)
    if reason is not None:
        return GuardResult(
            candidate_rent=candidate_rent,
            candidate_premium=premium_band,
            bucket=bucket,
            reason=reason,
            neighbors=neighbors,
        )

    # No edge tripped, so every edge has at least one uncensored,
    # positive-weight neighbour (`included` is `weight > 0`, see
    # `pipeline/membership.py`) — each estimate below is guaranteed a step and
    # a truncation point.
    estimates = {
        edge: km_estimate(
            [comps[index].effective_dom for index in indices],
            [comps[index].censored for index in indices],
            [weights[index] for index in indices],
        )
        for edge, indices in per_edge.items()
    }

    return CurveResult(
        candidate_rent=candidate_rent,
        candidate_premium=premium_band,
        bucket=bucket,
        neighbors=neighbors,
        curve=Band[KMCurve](**{edge: _km_curve(estimates[edge]) for edge in EDGES}),
        horizons=[
            HorizonReadout(
                day=day,
                survival=Band[float](
                    **{edge: survival_at(estimates[edge], day) for edge in EDGES}
                ),
            )
            for day in km_horizons
        ],
        expected_vacancy=_expected_vacancy(estimates, candidate_rent),
    )


def _band_guard_reason(
    per_edge: dict[str, list[int]],
    candidates: tuple[float, float, float],
    comps: Sequence[StitchedComp],
    premiums: Sequence[float | None],
) -> str | None:
    """The guard reason, or `None` when no drift edge trips.

    Owner ruling: **the guard trips if it trips at ANY of d−2 / d / d+2**, so
    every edge is judged against its own candidate premium and its own
    neighbour set. A rent can therefore honestly land on the guard even when
    its mid-point looks well-evidenced — that is the ruling working, not a
    bug.

    Priority mirrors F0-S2's ordering: "all k censored" is checked first, since
    an all-censored neighbour set has no leased outcome to build a curve from
    no matter how close those neighbours are.
    """
    reasons = [
        _edge_guard_reason(indices, candidate, comps, premiums)
        for indices, candidate in zip(per_edge.values(), candidates, strict=True)
    ]
    if "all_censored" in reasons:
        return "all_censored"
    if "too_few_in_range" in reasons:
        return "too_few_in_range"
    return None


def _edge_guard_reason(
    indices: Sequence[int],
    candidate: float,
    comps: Sequence[StitchedComp],
    premiums: Sequence[float | None],
) -> str | None:
    """F11-S3's trip rule at one drift point."""
    if indices and all(comps[index].censored for index in indices):
        return "all_censored"
    in_range = sum(
        1
        for index in indices
        if abs(candidate - (premiums[index] or 0.0)) <= IN_RANGE_DISTANCE
    )
    if in_range < MIN_USABLE_IN_RANGE:
        return "too_few_in_range"
    return None


def _km_curve(estimate: KMEstimate) -> KMCurve:
    """A `KMEstimate` on the wire. The one place the math layer meets Layer 3."""
    return KMCurve(
        points=[
            KMPoint(
                day=step.day,
                survival=step.survival,
                at_risk=step.at_risk,
                events=step.events,
                censored_count=step.censored_count,
            )
            for step in estimate.steps
        ],
        truncated_at=estimate.truncated_at,
    )


def _expected_vacancy(
    estimates: dict[str, KMEstimate], candidate_rent: float
) -> ExpectedVacancy:
    """Area under each edge's curve, over the window all three edges observed.

    NORTH_STAR: expected vacancy is "an expected value under a *truncated*
    empirical curve, always stated with its truncation point" — so the number
    and the truncation travel together, and the band's three edges are
    integrated over **one common window** (the earliest of the three
    truncation points). Integrating each edge over its own would produce a
    band whose edges answer different questions, and a `days` value that
    exceeded the single `truncated_at` the response states.

    F11-S4 owns the final truncation formula; its currently-stated one ("min
    of last event time and largest censoring floor") is not implemented,
    because QA showed it discards a genuinely observed event — see
    `stats/km.py`.
    """
    # Every estimate has a truncation point here: the guard has already
    # established that each edge observed at least one lease.
    truncated_at = min(
        estimate.truncated_at for estimate in estimates.values() if estimate.truncated_at is not None
    )
    days = {edge: expected_days(estimates[edge], truncated_at) for edge in EDGES}
    return ExpectedVacancy(
        days=Band[float](**days),
        cost=Band[float](
            **{edge: days[edge] * candidate_rent / DAYS_PER_MONTH for edge in EDGES}
        ),
        truncated_at=truncated_at,
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
