"""The derivation graph — F0-S2 [INVARIANT], the reactivity invariant.

`derive(req, ctx)` is the whole pipeline, and the wiring *is* the diagram.
There is no stage registry, no discovery, no `Stage` base class: the stage set
is known at author time and fan-in shaped (buckets need premiums *and* the
anchor; the price test needs premiums *and* the candidate premium), so a
uniform `run(state) -> state` signature would force a god-object state bag
through every stage — and that bag would hand the outcome to kNN's distance
calculation, demoting D19a from a fact of the call graph to a promise in a
code review (ADR-001 §5.1).

Adding a stage is three steps: write the function, add one line here, add its
field to `DerivedState`.

WHY THIS IS A PURE FUNCTION, AND WHY THAT IS THE STORY
------------------------------------------------------
The AC is "identical body ⇒ identical response". Precisely: `derive` is a pure
function of (request, immutable pull, config), and the endpoint keeps no
per-client state and writes nothing. Structurally, not by discipline
(ADR-001 §4):

1. **The wall clock is unreachable from here.** `as_of` is *data* — the pull
   manifest's fetch date — carried in `DeriveContext`. Owner ruling: censored
   means still active *as of the pull*, so a workspace reopened a week later
   must not add seven unobserved days to every censored floor, nor silently
   re-classify removals up the pending→provisional→confirmed ladder with no
   new evidence. (An AST guard over `pipeline/` and `stats/` enforces it.)
2. **No I/O below the API edge.** Every input arrives as an argument; loading
   happens in `api/derive.py`.
3. **Everything is frozen**, so no stage can leave a footprint for the next
   request.
4. **No module-level mutable state.**
5. **Every ordering is an explicit total sort** — no reliance on set/dict
   iteration order for anything that reaches a number.
6. **Nothing in the response is clock- or environment-derived**, which is what
   lets the AC be asserted on raw bytes.

Group A (record shaping) runs once per (pull, config) behind the loader's
memo; everything below is Group B and runs on every request, because it all
depends on curation (ADR-001 §2.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from rentcomp.models.domain import StitchedComp
from rentcomp.models.requests import DeriveRequest
from rentcomp.models.responses import (
    Band,
    Breakdown,
    CohortCount,
    Cut,
    DerivedComp,
    DeriveMeta,
    DerivedState,
    DerivedWarning,
    PartialPullInfo,
)
from rentcomp.pipeline.anchor import anchor as anchor_stage
from rentcomp.pipeline.buckets import bucket_of, bucket_stats
from rentcomp.pipeline.cohorts import cohort_medians, median_by_year
from rentcomp.pipeline.keys import disambiguate_keys
from rentcomp.pipeline.membership import classify_membership, distances_mi
from rentcomp.pipeline.premium import compute_premiums
from rentcomp.pipeline.pricetest import price_test
from rentcomp.pipeline.weights import contribution_shares, effective_weights
from rentcomp.storage.config import Config

__all__ = [
    "DRIFT_SENSITIVITY_PTS",
    "PIPELINE_VERSION",
    "DeriveContext",
    "derive",
    "drift_band",
]

#: ±2 percentage points, per spec §5.2's +5/+7/+9 example and F11-S4's
#: "recompute at d−2 / d / d+2". A module constant rather than an F0-S5 knob
#: (PM ruling): one fewer thing to keep coherent, and the response reports it
#: as a fact rather than reading it from config.
DRIFT_SENSITIVITY_PTS = 2.0

#: Bumped when the derivation changes in a way that changes output values.
#: WS-1 replaces record shaping (F4-S3/S4/S8), the anchor (F8-S1), bucket
#: outcome stats (F10-S1) and kNN retrieval (F11-S1) with real
#: implementations, so the `-stubs` marker (and the `stub_stage` warnings
#: that went with it) retire here. What remains genuinely unbuilt — the
#: Kaplan-Meier curve (F11-S2) and F11-S3's guard-vs-curve branch selection —
#: is not a stubbed *number* the way the others were: the price test always
#: returns the guard branch (`GuardResult`), which is the honest terminal
#: state NORTH_STAR names for evidence this thin, not a placeholder standing
#: in for a curve. See `pipeline/pricetest.py`.
PIPELINE_VERSION = "0.1.0-ws1"


@dataclass(frozen=True, slots=True)
class DeriveContext:
    """Everything the pipeline is allowed to know, besides the request.

    Assembled at the API edge. `as_of` in particular arrives as *data*: there
    is no clock down here to read it from, by construction.
    """

    config: Config
    comps: tuple[StitchedComp, ...]
    as_of: date
    pull_ref: str = ""
    pull_digest: str = ""
    config_digest: str = ""
    #: The gap in this pull's evidence, if it has one (F4-S9). Read off the
    #: pull's manifest at the edge, like `as_of` — the pipeline never looks at
    #: a file, and "which windows are missing" is not something it could
    #: recompute from the comps it was handed.
    partial_pull: PartialPullInfo | None = None


def drift_band(drift_pct: float, sensitivity_pts: float) -> Band[float]:
    """`(d−s, d, d+s)` — the drift sensitivity band (F8-S2 [INVARIANT]).

    A negative low edge is legal: drift is an assumption about market
    movement, and markets go down.
    """
    return Band[float](
        low=drift_pct - sensitivity_pts,
        mid=drift_pct,
        high=drift_pct + sensitivity_pts,
    )


def derive(req: DeriveRequest, ctx: DeriveContext) -> DerivedState:
    """One derivation pass, whole. The wiring is the diagram."""
    cfg, comps = ctx.config, ctx.comps

    keys = disambiguate_keys(comps)
    psfs = [comp.psf for comp in comps]
    years = [comp.cohort_year for comp in comps]

    weights = effective_weights(keys, [psf is not None for psf in psfs], req.weights)
    distances = distances_mi(comps, req.subject)
    states = classify_membership(
        comps, keys, weights, distances, req.filters, req.include_overrides
    )
    included = [state == "included" for state in states]

    cohorts = cohort_medians(keys, psfs, years, weights, included, cfg.min_cohort_size)
    premiums = compute_premiums(psfs, years, median_by_year(cohorts))
    comp_buckets = [bucket_of(premium, cfg.bucket_half_width_pct) for premium in premiums]

    drift = drift_band(req.drift_pct, DRIFT_SENSITIVITY_PTS)
    anchor = anchor_stage(
        keys, psfs, years, weights, included, drift, req.subject.sqft, ctx.as_of.year
    )
    buckets = bucket_stats(
        comps, keys, premiums, included, comp_buckets, anchor, cfg.bucket_half_width_pct
    )
    price = price_test(
        req.candidate_rent,
        anchor,
        comps,
        keys,
        premiums,
        weights,
        included,
        cfg.knn_k,
        cfg.bucket_half_width_pct,
    )

    contributions = contribution_shares(weights, included)
    derived_comps = [
        _derived_comp(comp, key, psf, premium, bucket, distance, weight, share, state)
        for comp, key, psf, premium, bucket, distance, weight, share, state in zip(
            comps,
            keys,
            psfs,
            premiums,
            comp_buckets,
            distances,
            weights,
            contributions,
            states,
            strict=True,
        )
    ]
    breakdown = _breakdown(comps, keys, psfs, years, states)

    return DerivedState(
        comps=derived_comps,
        cohorts=cohorts,
        anchor=anchor,
        buckets=buckets,
        price_test=price,
        breakdown=breakdown,
        warnings=_warnings(keys, psfs, req.candidate_rent, anchor is None),
        meta=DeriveMeta(
            as_of=ctx.as_of,
            pull_ref=ctx.pull_ref,
            pull_digest=ctx.pull_digest,
            config_digest=ctx.config_digest,
            pipeline_version=PIPELINE_VERSION,
            config=cfg,
            # D24/§5a: a gap in the evidence is carried in the same payload as
            # the numbers computed from it. `None` means "no gap", never "not
            # known" — a pull with no manifest record (a synthetic fixture) is
            # whole by construction.
            partial_pull=ctx.partial_pull,
        ),
    )


def _derived_comp(
    comp: StitchedComp,
    key: str,
    psf: float | None,
    premium: float | None,
    bucket: str | None,
    distance: float,
    weight: float,
    share: float | None,
    state: str,
) -> DerivedComp:
    return DerivedComp(
        key=key,
        address=comp.address,
        unit=comp.unit,
        lat=comp.lat,
        lng=comp.lng,
        beds=comp.beds,
        baths=comp.baths,
        sqft=comp.sqft,
        initial_ask=comp.initial_ask,
        psf=psf,
        premium=premium,
        # F4-S5 owns the fallback to the pulled-set median; until then every
        # premium that exists came from the selected set.
        premium_basis="selected" if premium is not None else None,
        cohort_year=comp.cohort_year,
        effective_dom=comp.effective_dom,
        censored=comp.censored,
        removal_class=comp.removal_class,
        withdrawal_suspect=comp.withdrawal_suspect,
        sqft_suspect=comp.sqft_suspect,
        cut_history=[
            Cut(on=cut.on, from_price=cut.from_price, to_price=cut.to_price)
            for cut in comp.cut_history
        ],
        relist_count=comp.relist_count,
        gap_days=comp.gap_days,
        distance_mi=distance,
        weight=weight,
        contribution_share=share,
        state=state,
        bucket=bucket,
    )


def _breakdown(
    comps: Sequence[StitchedComp],
    keys: Sequence[str],
    psfs: Sequence[float | None],
    years: Sequence[int],
    states: Sequence[str],
) -> Breakdown:
    """Counts and their evidence, from one pass over one set of labels.

    Every count is `len()` of the list beside it, so `breakdown.included` and
    the comps carrying `state="included"` cannot drift apart, and
    `included + excluded + filtered == pulled` holds by construction (F7-S1).
    """
    by_count: dict[str, list[str]] = {
        "pulled": list(keys),
        "included": [],
        "excluded": [],
        "filtered": [],
        "censored": [],
        "pending": [],
        "provisional": [],
        "confirmed": [],
        "withdrawal_suspect": [],
        # The one evidence-excluded set that must still be one click away
        # (T-S2): these comps are out of every median, bucket and neighbour
        # set, and the user still has to be able to see which ones they are.
        "missing_sqft": [],
    }
    for comp, key, psf, state in zip(comps, keys, psfs, states, strict=True):
        by_count[state].append(key)
        if comp.censored:
            by_count["censored"].append(key)
        elif comp.removal_class is not None:
            by_count[comp.removal_class].append(key)
        if comp.withdrawal_suspect:
            by_count["withdrawal_suspect"].append(key)
        if psf is None:
            by_count["missing_sqft"].append(key)

    per_cohort = [
        CohortCount(
            year=year,
            count=sum(1 for comp_year in years if comp_year == year),
            comp_keys=[
                key for key, comp_year in zip(keys, years, strict=True) if comp_year == year
            ],
        )
        for year in sorted(set(years))
    ]

    return Breakdown(
        pulled=len(by_count["pulled"]),
        included=len(by_count["included"]),
        excluded=len(by_count["excluded"]),
        filtered=len(by_count["filtered"]),
        censored=len(by_count["censored"]),
        pending=len(by_count["pending"]),
        provisional=len(by_count["provisional"]),
        missing_sqft=len(by_count["missing_sqft"]),
        per_cohort=per_cohort,
        comp_keys=by_count,
    )


def _warnings(
    keys: Sequence[str],
    psfs: Sequence[float | None],
    candidate_rent: float | None,
    no_anchor: bool,
) -> list[DerivedWarning]:
    """Everything the user must know about *this* derive.

    The `stub_stage` entries are not decoration: they travel with the payload,
    so a placeholder number cannot reach a screen (or a screenshot) without
    the label that says what it is. They disappear as their stories land.

    The `provisional_field` entries (WS-1a, ADR-002 finding 5) are the same
    self-documenting convention applied to individual FIELDS rather than
    whole stages: `sqft_suspect` and `premium_basis` are honest defaults for
    what this pipeline actually computes today, not placeholders standing in
    for a real number — but nothing signals they are provisional until
    F5-S1/F4-S5 build the real computation each one is a stand-in for. Each
    warning is unconditional and each disappears independently as its own
    story lands.

    **`partial_pull`'s entry is retired here (F4-S9).** It is no longer a
    placeholder: the orchestrator records which planned windows never arrived
    in the pull's manifest, and the edge hands that to `DeriveContext`, so
    `None` now means "this pull has no gap" rather than "nobody has looked".
    That was the whole point of the convention — the story that makes a
    placeholder real clears its own warning.
    """
    warnings: list[DerivedWarning] = [
        DerivedWarning(
            code="provisional_field",
            message=(
                "sqft_suspect is always false — the >30% cohort-median $/sqft deviation "
                "flag (F5-S1) is not built yet."
            ),
        ),
        DerivedWarning(
            code="provisional_field",
            message=(
                "premium_basis is always \"selected\" — the pulled-set fallback for thin "
                "cohorts (F4-S5) is not built yet."
            ),
        ),
    ]

    missing = [key for key, psf in zip(keys, psfs, strict=True) if psf is None]
    if missing:
        warnings.append(
            DerivedWarning(
                code="missing_sqft",
                message=(
                    f"{len(missing)} comp(s) have no square footage. They carry no $/sqft, "
                    "so they are excluded from every median, bucket and neighbour set and "
                    "default to weight 0 — they are still listed, and still one click away "
                    "from the missing-sqft count."
                ),
                breakdown_ref="missing_sqft",
            )
        )

    if candidate_rent is not None and no_anchor:
        warnings.append(
            DerivedWarning(
                code="no_anchor",
                message=(
                    "No selected comp has a $/sqft, so there is no anchor to express the "
                    "candidate rent against and no price test was run."
                ),
            )
        )

    return warnings
