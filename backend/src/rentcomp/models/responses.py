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

from datetime import date, datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from rentcomp.models.requests import DeriveRequest, SearchParams, Subject
from rentcomp.storage.config import Config

__all__ = [
    "Anchor",
    "Band",
    "Breakdown",
    "BucketStat",
    "CohortCount",
    "CohortPlan",
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
    "RecentWorkspace",
    "SearchPlan",
    "SearchResult",
    "Workspace",
]

T = TypeVar("T")

#: Bucket identifiers, in render order.
BucketId = Literal["below", "at", "above"]

#: The removal-confidence ladder on the wire. `None` means still active.
#: ARCHITECTURE.md §3's `leased` spelling is an erratum (PM ruling, F0-S2).
RemovalClass = Literal["pending", "provisional", "confirmed"]

#: Which set a median was computed over (F4-S5's thin-cohort fallback).
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
    #: Whole days between the chain's first listing and this cut — the *"day
    #: 21"* in §6.4's row copy `✂ 2,150 → 2,050 (day 21)`, and the reason the
    #: date beside it is never subtracted anywhere else. A cut on day 3 and a
    #: cut on day 90 mean opposite things about how the asking price was
    #: received, so the row cannot print the cut without it.
    #:
    #: A **count, not a second date**, for the same two reasons
    #: `days_since_removal` is (see `DerivedComp`): computing it in the view
    #: would be a derivation D5 puts in Python, and in JavaScript it would be
    #: date arithmetic across a timezone boundary, which D15 keeps out of this
    #: codebase entirely.
    #:
    #: `None` — never 0, which would read as "cut on the day it listed" — when
    #: the comp carries no `first_listed` to measure from. `shape_raw_pull`
    #: always sets it, so on real evidence this is never null; a hand-built or
    #: pre-shaped `StitchedComp` has none, which is exactly row 10b's shape,
    #: and the row renders the cut without its day rather than *"day nulld"*.
    day_offset: int | None = None


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
    #: Whole days between this comp's final removal and the pull date — the
    #: number `removal_class` was decided by, and the "4d" in F4-S8's row copy
    #: *"removed 4d — classifying"*. `None` iff `removal_class` is `None`.
    #:
    #: That "iff" is a **pipeline guarantee, not a type-system one**, and it
    #: holds because `shape.py::_build_comp` always sets
    #: `StitchedComp.first_listed` and derives `removal_class` from the same
    #: chain end that decides `censored` — so `removed_on` answers `None`
    #: exactly when `removal_class` is `None`. A comp assembled any other way
    #: can break it, and every pre-shaped synthetic fixture did until row 10b:
    #: they stated a rung on a comp that never said when it was listed, so the
    #: row rendered *"removed nulld — classifying"* on the very pull the
    #: Layer-2 suite derives against. What keeps that from returning is
    #: `backend/tests/unit/test_r10b_preshaped_pulls_are_shapeable.py` —
    #: read it before adding a pre-shaped pull.
    #:
    #: A **count, not a date**, deliberately. `removed_on` alone would leave
    #: the view subtracting it from `meta.as_of` — a derivation the view is not
    #: allowed to perform (D5) and, in JS, date arithmetic across a timezone
    #: boundary, which D15 keeps out of this codebase entirely. Shipping the
    #: difference means the only client-side step left is formatting.
    #:
    #: Carried for **every** removed comp, not only pendings: it is a plain
    #: fact about the comp, and a field that existed only in one state would
    #: make the row's rendering branch on a field's presence rather than on the
    #: ladder rung it is displaying.
    days_since_removal: int | None
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
    #: Taken over the set `basis` names: the selected comps, or — when the
    #: cohort is `thin` — all *pulled* comps in the year, each counting once
    #: (F4-S5 [INVARIANT]).
    median_psf: float | None
    basis: Basis | None
    #: `selected_count < min_cohort_size`, and therefore also the condition
    #: under which `basis` is `"pulled"`. F8-S3 renders the rail warning off
    #: this plus the two counts — `basis` alone does not say *how* thin, and a
    #: 3-of-5 cohort and a 1-of-1 cohort report the identical flag.
    thin: bool
    #: The user's own selected evidence for this year, always — so a comp at
    #: weight 0 is cited by no evidence list (F5-S2 [INVARIANT]) even while a
    #: thin cohort's median is taken over the wider pulled set.
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
    #: The size of the LEASED set (`removal_class in ("provisional",
    #: "confirmed")`) that `leased_dom_median`/`min`/`max`/
    #: `cut_before_lease_rate` are all computed over — distinct from `count`,
    #: which is the bucket's total membership. Row 25a: the per-cell evidence
    #: gate counts THIS, not `count` and not the search's total included
    #: comps (PM Ruling 2, QUEUE.md row 25a).
    leased_count: int
    leased_dom_median: float | None
    leased_dom_min: int | None
    leased_dom_max: int | None
    cut_before_lease_rate: float | None
    #: True when `leased_count` is below the evidence threshold
    #: (`min_cohort_size`, row 25a Ruling B) for the four leased-outcome
    #: statistics above to be shown without a caveat. Disclosure, not
    #: suppression: even when `thin` is True, the statistics above still
    #: report their real values — the view renders a warning ALONGSIDE them,
    #: never a blank/guard state instead (F10-S1's row-26 precedent on this
    #: same panel). Only meaningful on a non-empty leased set; an empty
    #: leased set already reports `None` on every statistic above and is not
    #: additionally "thin" — it has no evidence at all, not sparse evidence.
    thin: bool
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
    #: WS-1a's provisional `curve_not_available` value is retired as of
    #: F11-S3: F11-S2 landed the real curve/guard branch selection, so
    #: nothing has emitted this value since, and now the wire contract
    #: itself no longer names it as a possibility.
    reason: Literal["too_few_in_range", "all_censored"]
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

    #: Chains this pull bought and the **date window** then threw away —
    #: F4-S4's `ShapingSummary.dropped_outside_window`, on the wire since F4-S6.
    #:
    #: WHY IT IS HERE AND WHAT IT IS NOT PART OF. `included + excluded +
    #: filtered == pulled` is F7-S1's [INVARIANT] and is measured over
    #: *post-window* comps; this number counts chains that never reached that
    #: population, so it is deliberately outside that identity and must stay
    #: outside it. It is reported beside the identity, not folded into it.
    #:
    #: WHY IT IS ON THE WIRE AT ALL. F4-S6's empty state has to "name the
    #: binding constraint", and `pulled == 0` cannot: a search that the source
    #: answered with nothing and a search whose every record fell outside the
    #: date window are the same zero, and they ask the user for opposite
    #: actions (widen the radius vs. widen the window). Spec §3.2 pads every
    #: query ±90 days, so on the committed real pull a 16-day June window keeps
    #: 28 chains and drops 539 — 95% of what the pull paid for is discarded
    #: here by design, and a drop that leaves no trace makes "your market is
    #: thin" and "your window is narrow" indistinguishable. (F4-S4's PM ruling
    #: kept this off the wire; F4-S6 is the story that gave it a surface, which
    #: is the condition that ruling was waiting on.)
    #:
    #: `None` — never 0 — when the pull was not shaped from raw records: a
    #: pre-shaped synthetic fixture has no window stage behind it, so it has no
    #: honest count to report. "We did not measure" and "we measured zero" are
    #: different claims and the empty state branches on the difference.
    dropped_outside_window: int | None = None


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


class SearchResult(BaseModel):
    """What `POST /api/search` answers (F4-S9) — the outcome of a pull.

    No comps: evidence is addressed by reference (`pull_ref`) and fetched by
    `POST /api/derive`, the same reason `DeriveRequest` carries a ref rather
    than an uploaded comp list (ADR-001 §1.1).

    The cost fields exist so the user can never be surprised by a spend:
    `estimated_calls` is what a full (re)pull of this search costs,
    `calls_spent` is what this request actually cost, and `calls_to_complete`
    is what finishing an incomplete pull would cost. All three count the same
    unit — one fetchable query — so F2-S3's [INVARIANT] ("the number displayed
    and the number spent are one number") holds across the whole flow.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The ref `POST /api/derive` resolves. A function of the search params
    #: alone, so an identical search always addresses the same evidence.
    pull_ref: str
    #: What was already on disk when this search arrived: `miss` (nothing),
    #: `hit` (a whole pull), `stale` (an entry with a gap in it). F3-S2's
    #: modal branches on this to offer USE CACHED vs REFRESH.
    cache_status: Literal["hit", "miss", "stale"]
    #: `len(fetchable_queries(plan))` — what a full pull of this search costs.
    #: Structurally-empty windows are planned and never sent, so they are
    #: never billed to the user in an estimate either (F4-S1 AC4).
    estimated_calls: int
    #: Calls this request spent. Zero in fixture mode and zero when the
    #: evidence was already on disk.
    calls_spent: int
    complete: bool
    #: One label per window that never arrived ("2025 Inactive"), so a gap can
    #: be named rather than merely counted (D24 §5a).
    missing: list[str]
    calls_to_complete: int


class CohortPlan(BaseModel):
    """One cohort year in a planned pull, and whether it can be fetched.

    `fetchable` is false for a window that has not opened yet — the planner
    emits it anyway, and so does this, because "silently absent" and "planned
    but empty" are different things to a user checking whether their date
    window is the one they meant (`client/planner.py`). It costs nothing, so
    it is never billed either (F4-S1 AC4).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    fetchable: bool


class SearchPlan(BaseModel):
    """What `POST /api/search/plan` answers (F2-S3) — the cost of a search,
    before anyone has agreed to pay it.

    Spec §6.3's preview line, as data: *"Jun 15–30 · 2026, 2025 (2 cohorts) ·
    est. N API calls."* Every part of that sentence is a backend fact here,
    including the cohort years — D5/D13 make the SPA a renderer, and a frontend
    reconstructing the year set by subtracting from `new Date()` would be a
    second implementation of the planner's calendar rules.

    Deliberately the same field *names* as the overlapping half of
    `SearchResult`, because they are the same facts about the same search: a
    preview whose `estimated_calls` or `pull_ref` did not match the submit's
    would be worse than no preview at all.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: F3-S1's cache key for these params — what the form hands to F3-S2's
    #: modal and, after submit, to `POST /api/derive`. Identical to the ref
    #: `POST /api/search` resolves for the same body.
    pull_ref: str
    #: `miss` (nothing on disk), `hit` (a whole pull), `stale` (an entry with
    #: a gap). This is what lets the form route to the cache modal on submit
    #: without asking the route that pulls.
    cache_status: Literal["hit", "miss", "stale"]
    #: `len(fetchable_queries(plan))` — the F2-S3 [INVARIANT] number, counted
    #: by the same function `client/pull.py` spends against. Nothing was
    #: fetched to compute it and nothing ever will be.
    estimated_calls: int
    #: One entry per `years_back`, newest first.
    cohorts: list[CohortPlan]


class Workspace(BaseModel):
    """One stored curation state, as `GET/PUT /api/workspaces/{key}` answers it.

    `state` is nested — and is exactly a `DeriveRequest` — so that reopening a
    recent search is "read this, POST it to `/api/derive`" with nothing added,
    removed or renamed in between. A flat envelope would mix the store's own
    bookkeeping (`key`, `saved_at`) into the curation state, and a client that
    posted the whole thing back would get a 422 from `extra="forbid"`.

    Deliberately carries **no derived statistic and no cache-freshness verdict**.
    Whether the evidence behind this workspace has aged past the 7-day line is
    F3-S2's cache modal to decide and to say; answering it here would make
    every consumer of a saved workspace depend on a contract that does not
    exist yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The cache key this workspace is filed under — also its `pull_ref`.
    key: str
    #: When the store last wrote it. `None` only for a file that arrived on
    #: disk by some route other than a save and whose mtime is unreadable.
    saved_at: datetime | None
    #: The curation state, restored verbatim: the exact body `POST /api/derive`
    #: takes, including a `0.0` weight and a `null` candidate rent.
    state: DeriveRequest
    #: The search that produced the evidence, when the client stored it.
    search: SearchParams | None = None


class RecentWorkspace(BaseModel):
    """One row of the recents table (F1-S1), from `GET /api/workspaces`.

    An **index over stored workspaces**, rebuilt from the directory on every
    read — never a second store that could drift from it — and deliberately
    free of derived statistics. Serving an `anchor` column here would mean
    deriving every saved workspace on Home's mount, against an epic budget of
    "<1s, zero API calls", and would put a derived number in an index that has
    no evidence in front of it. F1-S1 decides what to do about that column.

    A row is never dropped for being unreadable. A workspace that vanished
    from the list would take the user's curation with it, silently and with no
    way back; the AC asks for the opposite — the row stays, says it is broken,
    and offers the one thing that can fix it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    saved_at: datetime | None
    #: The unit being priced. Carries the address F1-S1's table renders.
    subject: Subject | None = None
    #: Address / specs / radius for the table, when the workspace stored them.
    search: SearchParams | None = None
    #: Why this row cannot be opened — an unreadable workspace file, or
    #: evidence that is no longer on disk. `None` on a healthy row.
    error: str | None = None
    #: Whether a full re-pull is the way out of `error` (F1's edge state).
    offer_refresh: bool = False
