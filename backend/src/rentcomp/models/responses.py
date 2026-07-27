"""Layer 3, outbound half — the API contract (ARCHITECTURE.md §3, ADR-001 §1.2).

`DerivedState` is what `POST /api/derive` returns and what codegens into
TypeScript (D12). It is one payload on purpose: everything derived re-renders
together, so a partially-updated screen is not representable
(ARCHITECTURE.md §6).

Four properties of this module are structural, not stylistic:

1. **Every aggregate carries `comp_keys`.** The evidence-first invariant
   ("every aggregate is one click from its comps", T-S2) is a *schema*
   property here rather than a UI convention: an aggregate that cannot name
   its comps does not typecheck.
2. **`psf` / `premium` / `bucket` are `float | None` first class.** 14.7% of
   the real pull has no `squareFootage`. Those comps appear with `None` all
   the way down, are counted in `breakdown.missing_sqft`, are clickable from
   there — and are absent from every *evidence* list. Never a fabricated 0.
3. **`price_test` is a discriminated union.** F11-S3's "guard state and curve
   state are mutually exclusive renders, by construction" becomes a fact of
   the type: there is no value in which both a curve and a guard reason
   exist. It codegens to a TS discriminated union the view must `switch` on.
4. **Nothing here is clock- or environment-derived.** No `computed_at`, no
   `elapsed_ms`. That is what lets the statelessness AC be asserted on the
   raw response bytes rather than on a dict with timestamps excused
   (ADR-001 §4.6). `meta.as_of` is *data* — the pull's fetch date.

Everything is frozen (ADR-001 §4.3).

**Units.** `premium` and every premium bound are **ratios** (0.04 == +4%),
matching `DerivedComp.premium`, which is `psf / cohort_median − 1` by
definition. The `bucket_half_width_pct` knob is in percentage points and is
converted exactly once, in `pipeline/buckets.py`. Money is float64 and is
rounded at display only (D14).
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from rentcomp.storage.config import Config

__all__ = [
    "Anchor",
    "Band",
    "Breakdown",
    "BucketStat",
    "CohortCount",
    "CohortStat",
    "CurveResult",
    "Cut",
    "DeriveMeta",
    "DerivedComp",
    "DerivedState",
    "DerivedWarning",
    "ExpectedVacancy",
    "GuardResult",
    "HorizonReadout",
    "KMCurve",
    "KMPoint",
    "Neighbor",
    "PartialPullInfo",
    "PriceTest",
]

T = TypeVar("T")

#: Bucket identifiers, in render order.
BucketId = Literal["below", "at", "above"]

#: The removal-confidence ladder on the wire. `None` means still active.
#: ARCHITECTURE.md §3's `leased` spelling is an erratum (PM ruling, F0-S2).
RemovalClass = Literal["pending", "provisional", "confirmed"]

#: Which set a median was computed over (F4-S4's thin-cohort fallback).
Basis = Literal["selected", "pulled"]


class Band(BaseModel, Generic[T]):
    """A value at the three drift points: `low` = d−2, `mid` = d, `high` = d+2.

    F8-S2 [INVARIANT]: the sensitivity band propagates as a band, never as a
    point. Having it in the *types* is how that stays true — and it is what
    makes the owner's "the guard trips if it trips at any edge" rule
    representable at all (a centre-only candidate premium could not express
    it).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: T
    mid: T
    high: T


class Cut(BaseModel):
    """One price reduction in a comp's history, on the wire."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on: date
    from_price: float
    to_price: float


class DerivedComp(BaseModel):
    """One comp as the view sees it — evidence plus its derived position.

    Note what is *not* here: any listing id. F13-S1 [INVARIANT] keys curation
    by normalized address+unit because ids churn between pulls, and nothing
    downstream may be tempted to key on something that churns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    address: str
    unit: str | None
    lat: float
    lng: float

    beds: float
    baths: float | None
    sqft: float | None

    initial_ask: float
    #: `None` when sqft is unknown.
    psf: float | None
    #: `psf / cohort_median_psf − 1`, a ratio. `None` when psf is `None` or the
    #: comp's cohort has no median.
    premium: float | None
    #: Which set the cohort median behind `premium` came from (F4-S5).
    premium_basis: Basis | None

    cohort_year: int
    #: For a censored comp this is a *floor* measured at the pull date.
    effective_dom: int
    censored: bool
    #: `None` iff censored — a still-active listing has not been removed.
    removal_class: RemovalClass | None
    withdrawal_suspect: bool
    sqft_suspect: bool

    cut_history: list[Cut]
    relist_count: int
    gap_days: int
    distance_mi: float

    #: Effective weight after defaulting — the response echoes it so nothing
    #: about how a comp was treated is hidden from the client.
    weight: float
    #: weight ÷ Σ weights of included comps. Computed server-side (D5): the
    #: view must never divide one payload field by another.
    #:
    #: A **ratio in [0, 1]**, not percentage points — `0.25` means 25%. Named
    #: `_share` rather than `_pct` (PM ruling 2026-07-27) precisely because
    #: `_pct` means percentage POINTS everywhere else in this codebase
    #: (`bucket_half_width_pct`, `drift_pct`), and this name codegens straight
    #: into the TypeScript F5-S2's "amber above 40%" row is written against —
    #: where `contribution_pct > 40` would have looked correct and been wrong
    #: by 100x.
    contribution_share: float | None
    state: Literal["included", "excluded", "filtered"]
    bucket: BucketId | None


class CohortStat(BaseModel):
    """One listing-year's median $/sqft, and the evidence behind it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    selected_count: int
    pulled_count: int
    median_psf: float | None
    basis: Basis | None
    #: `selected_count < min_cohort_size` (F4-S4 owns what happens next).
    thin: bool
    comp_keys: list[str]


class Anchor(BaseModel):
    """The market reference point, with its sensitivity band.

    NORTH_STAR: never rendered without the band — it depends on a manual
    assumption (drift), so a point estimate would be a claim the evidence does
    not support.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rent: Band[float]
    psf: Band[float]
    drift_pct: float
    #: ±2 percentage points, a module constant rather than a knob (PM ruling).
    drift_sensitivity_pts: float
    subject_sqft: float
    n_comps: int
    comp_keys: list[str]


class BucketStat(BaseModel):
    """One premium bucket: a stable % definition, a live dollar translation.

    NORTH_STAR: buckets never interpolate. An empty bucket carries `None`
    statistics — the view renders a dash, never an estimate.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: BucketId
    #: Ratios (0.04 == +4%). `None` means unbounded on that side.
    premium_min: float | None
    premium_max: float | None
    #: The same cutoffs translated through the current anchor — a band,
    #: because the anchor is a band.
    dollar_min: Band[float] | None
    dollar_max: Band[float] | None

    count: int
    leased_dom_median: float | None
    leased_dom_min: int | None
    leased_dom_max: int | None
    cut_before_lease_rate: float | None
    provisional_count: int
    withdrawal_suspect_count: int
    #: DOM-so-far of the censored comps in this bucket: floors, not outcomes,
    #: and never mixed into `leased_dom_*`.
    censored_floors: list[int]
    comp_keys: list[str]


class Neighbor(BaseModel):
    """A comp retrieved by the kNN stage, with its distance in premium space."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    premium: float
    #: |candidate_premium − premium|. Premium is the ONLY feature distance may
    #: use (D19a); the outcome pair below is carried for the KM estimator and
    #: for display, and never re-enters retrieval.
    distance: float
    effective_dom: int
    censored: bool
    removal_class: RemovalClass | None
    weight: float
    cohort_year: int
    cut_count: int


class KMPoint(BaseModel):
    """One step of a Kaplan-Meier product-limit estimate (F11-S2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day: int
    survival: float
    at_risk: float
    events: float
    censored_count: int


class KMCurve(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    points: list[KMPoint]
    #: The last observable time — the curve says nothing beyond it.
    truncated_at: int | None


class HorizonReadout(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    day: int
    survival: Band[float]


class ExpectedVacancy(BaseModel):
    """Area under the truncated KM step function, in days and dollars.

    Always stated with its truncation point (NORTH_STAR): it is an expected
    value under a *truncated* empirical curve, not a guarantee and not valid
    beyond the observation window.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    days: Band[float]
    cost: Band[float]
    truncated_at: int


class CurveResult(BaseModel):
    """The price test when the evidence supports a curve."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["curve"] = "curve"
    candidate_rent: float
    candidate_premium: Band[float]
    bucket: BucketId
    neighbors: list[Neighbor]
    curve: Band[KMCurve]
    horizons: list[HorizonReadout]
    expected_vacancy: ExpectedVacancy


class GuardResult(BaseModel):
    """The price test when it does not.

    This is a *different render*, not a curve with wide error bars — the two
    are mutually exclusive by construction (F11-S3 [INVARIANT], NORTH_STAR).
    The nearest comps are still surfaced, with their distances, so the user
    can see exactly how thin the evidence is (spec §5.4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["insufficient_evidence"] = "insufficient_evidence"
    candidate_rent: float
    candidate_premium: Band[float]
    bucket: BucketId
    #: WS-1a: `curve_not_available` is a provisional third value, not an
    #: F11-S3 trip reason. F11-S3's real trip rule is untouched (<3 usable
    #: neighbors in range, or all censored) — but when neither holds, this
    #: endpoint still has no `CurveResult` to return (F11-S2 doesn't exist
    #: yet), and reporting one of the other two literals in that case would
    #: be a stated reason with no evidence behind it (NORTH_STAR). This value
    #: retires the moment F11-S2/F11-S3's guard-vs-curve branch selection
    #: lands for real — the same provisional-marker convention as
    #: `DerivedWarning(code="provisional_field")`.
    reason: Literal["too_few_in_range", "all_censored", "curve_not_available"]
    neighbors: list[Neighbor]


#: Discriminated on `state`, so the generated TS forces a `switch` rather than
#: probing which of two fields is populated (D12, ADR-001 §1.2).
PriceTest = Annotated[CurveResult | GuardResult, Field(discriminator="state")]


class CohortCount(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    count: int
    comp_keys: list[str]


class Breakdown(BaseModel):
    """Where every pulled comp went. `included + excluded + filtered == pulled`
    (F7-S1 [INVARIANT]), computed in one place so the count and the per-comp
    label can never disagree.

    `comp_keys` maps each count to the comps behind it — including
    `missing_sqft`, which is the one evidence-excluded set that still has to
    be reachable by a click (T-S2).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pulled: int
    included: int
    excluded: int
    filtered: int
    censored: int
    pending: int
    provisional: int
    missing_sqft: int
    per_cohort: list[CohortCount]
    comp_keys: dict[str, list[str]]


class DerivedWarning(BaseModel):
    """Something the user must know about this derive.

    Named `DerivedWarning` rather than ADR-001 §1.2's sketch spelling
    `Warning` so the module does not shadow the builtin (the field is still
    `warnings`; the wire shape is unchanged).

    A warning never carries its own `comp_keys` list. It points at one of
    `breakdown.comp_keys`' entries instead, so the comps behind a warning and
    the comps behind the count that produced it are literally the same list —
    two lists could disagree, and a `comp_keys` list outside `breakdown` reads
    as an *evidence* list (the comps a statistic was computed from), which is
    the opposite of what a warning names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    #: Key into `breakdown.comp_keys` — the click-through for this warning.
    breakdown_ref: str | None = None


class PartialPullInfo(BaseModel):
    """A named gap in the evidence (D24/F4-S6) — "2025 inactive missing"."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    missing: list[str]
    calls_to_complete: int


class DeriveMeta(BaseModel):
    """Provenance. Deliberately contains no clock reading of any kind."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The pull's fetch date, from its manifest — NOT the wall clock (owner
    #: ruling 1). "Censored" means still active *as of the pull*; using today
    #: would add unobserved days to every censored floor and silently
    #: re-classify removals with no new evidence.
    as_of: date
    pull_ref: str
    pull_digest: str
    config_digest: str
    pipeline_version: str
    #: The knobs this derive actually used — F0-S5's "a knob change re-derives
    #: like any other input" is only checkable if the knobs are reported.
    config: Config
    partial_pull: PartialPullInfo | None = None


class DerivedState(BaseModel):
    """One derivation pass, whole."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Every windowed comp, including excluded and filtered ones — a filter
    #: re-labels, it never removes evidence from the payload.
    comps: list[DerivedComp]
    cohorts: list[CohortStat]
    #: `None` when no positive-weight comp has a $/sqft.
    anchor: Anchor | None
    #: Always three, in render order; empty ones carry nulls, never estimates.
    buckets: list[BucketStat]
    #: `None` when no candidate rent was supplied (or when there is no anchor
    #: to express one against — see `pipeline/pricetest.py`).
    price_test: PriceTest | None
    breakdown: Breakdown
    warnings: list[DerivedWarning]
    meta: DeriveMeta
