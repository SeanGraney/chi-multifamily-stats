# ADR-001 — The derivation graph (`POST /api/derive`)

**Story:** F0-S2 [INVARIANT] · **Status:** ACCEPTED — owner signed off 2026-07-26 · **Date:** 2026-07-26
**Author:** developer agent · **Decides for:** ~30 downstream stories (F4, F5, F7, F8, F9, F10, F11, F13, F14)

> **Errata (PM-ruled, applied during F0-S2 implementation).** Two corrections
> are folded into the text below and marked **[ERRATUM E1]** / **[ERRATUM E2]**
> where they land:
>
> * **E1 — §4.2's no-I/O guard does not cover `client/`.** D17's fixture-mode
>   RentCast client reads fixture files from disk *by design*, so scoping the
>   guard to `pipeline/` + `stats/` is the correct rule; extending it to
>   `client/` would forbid F0-S4's whole default mode. (The separate
>   *no-`load_config`* guard from F0-S5 still covers `client/` and `models/` —
>   that one is about knobs reaching the math as values, not about I/O.)
> * **E2 — §1.2's "absent from every `comp_keys` list" means every AGGREGATE /
>   EVIDENCE list; `$.breakdown.*` is exempt.** A comp with no sqft is excluded
>   from every median, bucket and neighbour set **and** stays clickable from
>   `breakdown.comp_keys["missing_sqft"]`. Excluding it from the breakdown too
>   would make a counted set unreachable, which is the evidence-first invariant
>   (T-S2) inverted.
>
> Also resolved, and recorded here so §6's open questions are not read as still
> open: **Q1** pull date (owner), **Q2** option (b), guard trips at any band
> edge (owner), **Q3** module constant `DRIFT_SENSITIVITY_PTS = 2.0` (PM),
> **Q4** `pending | provisional | confirmed`, ARCHITECTURE §3's `leased`
> superseded (PM), **Q5** no objection — the tuple landed in F0-S2.

> *Method note:* the `engineering:architecture` skill is not installable in this
> environment (no Skill tool). ADR discipline applied by hand: context → decision →
> consequences → alternatives → open questions. Nothing else changes.

**Scope:** the request/response contract, the stage interface, memoization, and the
structural guarantees behind "identical body ⇒ identical response". No code was
written for this ADR. It does not redefine any statistic — where a number's meaning
is at stake (§6, Q1) it is escalated, not decided.

---

## 1. State shape

### 1.1 The request

Pinned by other docs (**not** my proposal — restated so the shape is readable):

- **Selection is the weight.** F5-S2 [INVARIANT]: "toggle-off ≡ weight 0 (one source of
  truth: the weight)". So the body carries `weights`, **not** a separate `selections`
  set. The story's `(selections, weights, ...)` phrasing would create two sources of
  truth; one of them must not exist.
- **Comps are keyed by normalized address+unit, never listing id.** F13-S1 [INVARIANT]:
  "re-key selections/weights by normalized address+unit — ids can churn."
- Curation state is client-owned (D13); the comps themselves are server-owned and
  immutable (§5, `cache/` is immutable raw responses).
- Filters are sent as *parameters*, not as a pre-filtered comp list — F7-S1's
  `included + excluded + filtered = pulled` must be computable in one place.

```python
# models/requests.py  (new module; Layer 3's inbound half)
class Filters(BaseModel):                       # F7-S1
    max_distance_mi: float | None = None
    hide_censored: bool = False
    leased_only: bool = False

class Subject(BaseModel):
    address: str; lat: float; lng: float
    sqft: float; beds: float; baths: float

class DeriveRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    pull_ref: str                    # cache key of the immutable raw pull (F3-S1)
    subject: Subject
    weights: dict[str, float]        # comp_key -> weight; 0 == excluded-but-visible
    include_overrides: list[str]     # manual re-includes that survive filter resets (F7-S1)
    filters: Filters
    drift_pct: float                 # annual, percentage points (7.0 == +7%/yr)
    candidate_rent: float | None     # None on Results (no price test yet)
```

**Weight defaulting (proposed).** A comp absent from `weights` gets `1.0`, except a comp
with no `squareFootage`, which gets `0.0` (spec §7, F4-S5). Reason: F13-S1 requires new
comps after a refresh to arrive included at weight 1 without the client having enumerated
them. The response echoes every comp's *effective* weight, so nothing is hidden.

**`pull_ref` rather than comps-in-body (proposed).** ~100 comps re-uploaded every 150ms
during a slider drag is wasteful, and it would put the evidence under client control —
NORTH_STAR requires every number to trace to server-held observed comps. The handler
resolves `pull_ref` through one seam, `PullLoader`, with a fixture-backed implementation
so **WS-1 can run before F3-S1's cache exists** without an API change later.

### 1.2 The response

```python
# models/responses.py
class DerivedState(BaseModel):
    comps: list[DerivedComp]              # every windowed comp, incl. excluded/filtered
    cohorts: list[CohortStat]
    anchor: Anchor | None                 # None when no positive-weight comp has $/sqft
    buckets: list[BucketStat]             # always 3; empty ones carry nulls, never estimates
    price_test: PriceTest | None          # None iff candidate_rent is None
    breakdown: Breakdown
    warnings: list[Warning]
    meta: DeriveMeta

class DerivedComp(BaseModel):
    key: str                              # normalized address+unit
    address: str; unit: str | None; lat: float; lng: float
    beds: float; baths: float | None; sqft: float | None
    initial_ask: float
    psf: float | None                     # None when sqft missing (14.7% of real pull)
    premium: float | None                 # None when psf is None OR no cohort median
    premium_basis: Literal["selected", "pulled"] | None   # F4-S5 fallback, per comp
    cohort_year: int
    effective_dom: int
    censored: bool
    removal_class: Literal["pending", "provisional", "confirmed"] | None  # None iff censored
    withdrawal_suspect: bool
    sqft_suspect: bool                    # $/sqft >30% off cohort median (F5-S1)
    cut_history: list[Cut]; relist_count: int; gap_days: int
    distance_mi: float
    weight: float                         # effective, after defaulting
    contribution_pct: float | None        # weight / Σ selected weights — server-side (F5-S2)
    state: Literal["included", "excluded", "filtered"]
    bucket: Literal["below", "at", "above"] | None

class CohortStat(BaseModel):
    year: int; selected_count: int; pulled_count: int
    median_psf: float | None
    basis: Literal["selected", "pulled"] | None   # which set the median came from
    thin: bool                                    # selected_count < min_cohort_size
    comp_keys: list[str]

class Band(BaseModel, Generic[T]):        # low = drift−2, mid = drift, high = drift+2
    low: T; mid: T; high: T

class Anchor(BaseModel):
    rent: Band[float]; psf: Band[float]
    drift_pct: float; drift_sensitivity_pts: float   # 7.0, 2.0
    subject_sqft: float; n_comps: int; comp_keys: list[str]

class BucketStat(BaseModel):
    id: Literal["below", "at", "above"]
    premium_min: float | None; premium_max: float | None      # stable % definition
    dollar_min: Band[float] | None; dollar_max: Band[float] | None  # live, anchor-derived
    count: int
    leased_dom_median: float | None; leased_dom_min: int | None; leased_dom_max: int | None
    cut_before_lease_rate: float | None
    provisional_count: int; withdrawal_suspect_count: int
    censored_floors: list[int]
    comp_keys: list[str]

class Neighbor(BaseModel):
    key: str; premium: float; distance: float          # |candidate_premium − premium|
    effective_dom: int; censored: bool
    removal_class: Literal["pending", "provisional", "confirmed"] | None
    weight: float; cohort_year: int; cut_count: int

# F11-S3's mutual exclusivity as a *type*, not a runtime check:
class CurveResult(BaseModel):
    state: Literal["curve"]
    candidate_rent: float; candidate_premium: Band[float]
    bucket: Literal["below", "at", "above"]
    neighbors: list[Neighbor]
    curve: Band[KMCurve]                               # step points, per drift point
    horizons: list[HorizonReadout]                     # S(t) band at 14/30/45/60
    expected_vacancy: ExpectedVacancy                  # days band, cost band, truncated_at

class GuardResult(BaseModel):
    state: Literal["insufficient_evidence"]
    candidate_rent: float; candidate_premium: Band[float]
    bucket: Literal["below", "at", "above"]
    reason: Literal["too_few_in_range", "all_censored"]
    neighbors: list[Neighbor]                          # nearest + their distances (spec §5.4)

PriceTest = Annotated[CurveResult | GuardResult, Field(discriminator="state")]

class Breakdown(BaseModel):
    pulled: int; included: int; excluded: int; filtered: int   # invariant: i+e+f == pulled
    censored: int; pending: int; provisional: int; missing_sqft: int
    per_cohort: list[CohortCount]
    comp_keys: dict[str, list[str]]                    # every count → its comps

class DeriveMeta(BaseModel):
    as_of: date                     # from the pull manifest, NOT the wall clock (§4)
    pull_digest: str; config_digest: str; pipeline_version: str
    config: Config                  # the knobs this derive actually used
    partial_pull: PartialPullInfo | None   # D24/F4-S6 named gap ("2025 inactive missing")
```

Three shape decisions worth naming:

- **`price_test` is a discriminated union.** F11-S3's "guard-state and curve-state are
  mutually exclusive renders, by construction" becomes a fact of the type: there is no
  value in which both a `curve` and a `reason` exist. It codegens to a TS discriminated
  union the view must `switch` on, so the mutual exclusivity survives into the frontend.
- **Every aggregate carries `comp_keys`.** The evidence-first invariant ("every aggregate
  is one click from its comps", T-S2) becomes a schema property rather than a UI
  convention — an aggregate that can't name its comps won't compile.
- **`premium: float | None` is first-class.** Real evidence: 14.7% of records have no
  `squareFootage`. Those comps appear in `comps` with `psf=None`, `premium=None`,
  `bucket=None`, weight 0 by default, are absent from every **aggregate/evidence**
  `comp_keys` list, and are
  counted in `breakdown.missing_sqft` + a `warnings` entry. Never a crash, never a 0.
  **[ERRATUM E2]** "every `comp_keys` list" read too broadly: **`$.breakdown.*` is
  exempt.** `breakdown.comp_keys` maps each *count* to the comps it counted, so a
  no-sqft comp must stay clickable from `missing_sqft` — excluding it there would
  make a counted set unreachable, which is T-S2 inverted. What must not happen is a
  comp with no premium being cited as *evidence* for a statistic, i.e. under
  `$.cohorts[*]`, `$.buckets[*]`, `$.anchor`, or a neighbour set. Consequence taken in
  implementation: a `warnings` entry carries **no `comp_keys` of its own** — it names a
  key into `breakdown.comp_keys` instead, so the warning and the count it describes are
  literally the same list and cannot disagree.

**Nothing in the response is clock-derived or environment-derived** — no `computed_at`,
no `elapsed_ms`. That is what makes the AC assertable on raw bytes (§4).

---

## 2. Derivation interface

### 2.1 Reconciling the two chains — they are one pipeline, cut in two places

The `add-pipeline-stage` skill lists `dedupe → spells → stitch → classify → window →
cohort → premium`. The story lists `cleanedComps → cohortMedians → premiums → anchor →
buckets → kNN → guard → KM`. They are the same pipeline at two altitudes, and they
overlap on exactly one stage. The load-bearing distinction is **what each stage depends
on**:

| Group | Stages | Depends on | Runs |
|---|---|---|---|
| **A — record shaping** | dedupe → spells → stitch → classify → window → cohort → **psf** | raw pull + config + `as_of` only. *Not* on curation. | once per (pull, config), memoized |
| **B — curation derivation** | **cohortMedians → premiums** → anchor → buckets → kNN → guard → KM | Group A's output + the request body | every request |

Group A's output *is* the story's `cleanedComps`: `tuple[StitchedComp, ...]`.

**The one genuine conflict, and its ruling.** The skill puts `premium` in the
once-per-pull chain. It cannot live there: F4-S5 [INVARIANT] defines premium against the
cohort median **over selected comps**, so premium changes when the user toggles a comp.
Ruling: **split the skill's `premium` stage.** `psf = initial_ask / sqft` (a property of
the record, no curation input) stays in Group A. `premium = psf / cohort_median_psf − 1`
moves to Group B, where selection exists. Everything else in the skill's list is
unchanged and unreordered.

*Follow-up for the PM (not done here — `.claude/skills/` is out of my dispatch):*
`add-pipeline-stage/reference.md` should be amended to show the A/B split and the
psf/premium cut, or the next agent to use it will wire premium into the wrong group.

**Honesty about the `cleanedComps` boundary** (from `f4-s7-first-pull-analysis.md`, real
data): the stitch stage must merge contiguous history events (gap ≤ 1d — 23 real records
are price-change chains, not re-lists) *before* the 42d re-list threshold applies; it must
synthesize a single spell from top-level fields for the 8.8% of records with no `history`
object; and `psf` is `None` for the 14.7% with no `squareFootage`. Group A promises
"every surviving record is a `StitchedComp` with a start date, a DOM, and a censoring
flag" — it does **not** promise a `psf`.

### 2.2 A stage is a plain function with a narrow signature. There is no registry.

```python
# pipeline/premium.py
def compute_premiums(
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    cohort_medians: Mapping[int, float],
) -> list[float | None]: ...

# stats/knn.py — F11-S1 AC: "distance() accepts only premium values"
def select_neighbors(
    premiums: Sequence[float], candidate_premium: float, k: int
) -> list[int]: ...
```

**A stage declares what it needs and produces by its parameter list and return type.**
No decorator metadata, no `reads=`/`writes=` declaration, no `Stage` base class. The
declaration is enforced by Python's own argument binding: a function that was not handed
`effective_dom` **cannot reach it**. That turns D19a's target-leakage prohibition and
F11-S1's AC ("a test asserts DOM is not reachable from `distance()`'s inputs") from a
code-review promise into a property of the call graph, checkable without a type checker
(we ship none).

**Config reaches a stage as scalars, unpacked by the orchestrator** — `k: int`,
`stitch_gap_days: int` — not as a `Config` object, for the same declaration-by-signature
reason, and because it keeps unit tests free of `Config` construction. Only the two
orchestrators take `Config` wholesale. Nothing under `pipeline/`, `stats/`, `client/`, or
`models/` imports `load_config`; `test_config_store.py`'s AST guard already enforces this
and must stay green.

**The orchestrator is a literal function** — the wiring is the diagram:

```python
# pipeline/derive.py — the whole graph, readable in one screen. No I/O, no clock.
def derive(req: DeriveRequest, ctx: DeriveContext) -> DerivedState:
    cfg, comps, as_of = ctx.config, ctx.comps, ctx.as_of
    weights   = effective_weights(comps, req.weights)                     # defaulting rule
    state     = classify_membership(comps, weights, req.filters,
                                    req.include_overrides, req.subject)   # incl/excl/filtered
    cohorts   = cohort_medians(comps, weights, state, cfg.min_cohort_size)
    premiums  = compute_premiums(psfs(comps), years(comps), cohorts)
    bands     = drift_band(req.drift_pct, DRIFT_SENSITIVITY_PTS)          # (d−2, d, d+2)
    anchors   = [anchor(psfs, years, weights, d, req.subject.sqft) for d in bands]
    buckets   = bucket_stats(comps, premiums, weights, state, anchors,
                             cfg.bucket_half_width_pct)
    price     = price_test(req.candidate_rent, anchors, premiums, comps, weights,
                           state, cfg.knn_k, cfg.km_horizons_days)        # kNN→guard→KM
    return DerivedState(...)
```

**Adding a stage** = write the function, add one line here, add its field to
`DerivedState`, add a Layer-1 pytest. That is the same three steps the skill already
prescribes; only the "wire into the orchestrator" step is now literal rather than
registry-mediated.

**Band consequence (important, and easy to miss):** premium is time-local and drift-free,
but the *anchor* is drift-dependent, and `candidate_premium = candidate_rent / anchor − 1`
— therefore **the kNN neighbor set, the guard decision, and the KM curve all differ across
the three drift points**. Everything from `anchor` downward runs three times per request.
At n ≤ 100 this is free (§3); the interface cost is that `Band[T]` appears in the anchor,
bucket dollars, candidate premium, curve, horizons, and expected vacancy. F8-S2's
[INVARIANT] ("always propagates as a band, never a point") is what forces this, and having
it in the types is how it stays true.

---

## 3. Memoization

**Group B (the per-request chain) is not memoized, and does not need to be.** Basis:
n ≤ 100 comps; the chain is ~8 stages of `O(n log n)` sorts and vectorized arithmetic
(weighted median = one `argsort` + one `cumsum`), run 3× for the drift band; KM runs over
k=7 neighbors. That is order 10⁴–10⁵ float ops — well under 1ms. The measurable costs are
Pydantic serialization of ~100 `DerivedComp` rows (~1–3ms) and the FastAPI/HTTP round trip
(~1–2ms). Expected total: **single-digit milliseconds against a 100ms budget** — 10–50×
headroom, consistent with D5's own "at ~40 comps the math is microseconds". Adding a memo
here would buy nothing and import a cache-invalidation bug surface into the exact code
path whose entire purpose is that staleness cannot exist. If a profile ever says
otherwise, this is revisitable without touching the interface.

**Group A (record shaping) is memoized, because it is I/O + parsing, not math.** Reading
and Pydantic-validating a 500-record raw pull is tens of milliseconds and would be
repeated on every slider tick. One process-level LRU (size ~4 workspaces), living at the
storage edge — never inside `pipeline/` — keyed on the complete set of inputs:

```python
@lru_cache(maxsize=4)
def shaped_comps(pull_ref: str, pull_digest: str, cfg: Config, as_of: date
                 ) -> tuple[StitchedComp, ...]: ...
```

- **Cached:** the shaped comp tuple only. **Keyed on:** pull ref + manifest digest +
  the full `Config` + `as_of`. **Invalidated by:** key change — a refresh writes a new
  manifest (new digest), a knob change gives a new `Config`, nothing else can matter.
  There is no explicit invalidation call to forget to make.
- It is a *pure function cache*: same key ⇒ same value, so it cannot affect idempotence.
  Tests that rewrite a temp `RENTCOMP_HOME` in place should call `.cache_clear()` anyway.
- Config is in the key deliberately: F0-S5's load-bearing clause is "a knob change
  re-derives like any other input." A memo that ignored config would silently break it.

### 3.1 Ruling: `Config.km_horizons_days` becomes `tuple[int, ...]` — Config is hashable

**Decision: yes, change it.** Rationale, in priority order:

1. **`frozen=True` is currently a half-truth.** Pydantic's frozen blocks *rebinding* a
   field, not *mutation* of a mutable field value. `cfg.km_horizons_days.append(90)`
   succeeds today and changes derived output with no re-derive signal and no way for the
   memo key to notice — precisely the failure F0-S5's immutability clause exists to
   prevent, and precisely the class of bug this ADR's §4 is trying to make unwritable.
2. **`Config` is unhashable today**, so `frozen=True`'s generated `__hash__` raises at
   runtime. Any use of Config as a memo-key component, dict key, or `lru_cache` argument
   — the natural implementation of §3 — fails with a `TypeError`.
3. **Cost is one QA test row**, which QA has already agreed to relax: compare
   `list(cfg.km_horizons_days) == [14, 30, 45, 60]`, which is the form
   `test_config_store.py` already uses at lines 270 and 647.
4. **Not a semantic change.** Same values, same order, same meaning, same JSON (`[14, 30,
   45, 60]` in and out), same OpenAPI/TS type (`number[]`). Implementation-only —
   [DEFAULT]-class under `SEMANTIC_CHANGE_PROTOCOL.md`, no escalation required.

Mechanics: `km_horizons_days: tuple[int, ...] = (14, 30, 45, 60)` (a tuple default is safe
to share, so `default_factory` goes away); the existing non-empty/positive/strictly-
increasing validator is unchanged apart from its annotation. **Sequencing:** this is a
one-line edit to `storage/config.py`; it is *not* in this docs-only ADR pass. It lands in
the F0-S2 implementation branch (or a 10-minute F0-S5 amendment, PM's call) together with
QA's relaxed row.

---

## 4. How idempotence and statelessness are structural, not disciplinary

The AC is "identical body ⇒ identical response." Precisely stated, the contract is:
**`derive` is a pure function of (request body, immutable pull, config)**, and the
endpoint keeps no per-client state and writes nothing. Seven mechanisms, each of which
makes a stateful mistake either impossible or loud:

1. **The wall clock is not reachable from the pipeline.** `as_of` is *data* — the pull
   manifest's `fetched_at` — carried in `DeriveContext` and echoed in `meta.as_of`.
   Enforced by an AST guard in `pipeline/` and `stats/` mirroring the existing config
   guard: no `date.today`, `datetime.now`, `time.time`. Without this, every censored
   comp's DOM floor drifts a day at midnight and the AC is untestable. (This has a
   meaning consequence — see Q1.)
2. **No I/O below the edge.** The F0-S5 AST guard already forbids `load_config` in
   `pipeline/stats/client/models`; a *second* guard forbids `open`/`Path.read_*` and
   environment reads. **[ERRATUM E1]** the I/O guard is scoped to **`pipeline/` and
   `stats/` only — not `client/`**: D17's fixture mode *is* reading fixture files from
   disk, so including `client/` would forbid F0-S4's default and only-safe mode. (The
   `load_config` guard still covers `client/` and `models/`; that one is about knobs
   reaching the math as passed-in values, which is a different rule.) Every pipeline
   input arrives as an argument; loading happens in `api/derive.py`, and the pull
   loader + its memo live at the storage edge (`storage/pulls.py`).
3. **Everything is frozen.** `DeriveRequest`, `Config`, `StitchedComp`, and the response
   models are `frozen=True`; Group A's output is a `tuple`. A stage cannot mutate an
   upstream artifact, so no stage can leave a footprint for the next request.
4. **No module-level mutable state**, and the one process-level cache is a pure function
   cache whose key contains every input (§3).
5. **Determinism of order.** Every ordering is an explicit sort with a total key
   (`np.argsort(kind="stable")` per D19, F11-S1's deterministic-tie AC). No RNG, no
   reliance on `set`/`dict` iteration order for anything that reaches a number, no
   concurrency inside a derive.
6. **The response carries no clock- or environment-derived field**, so the AC test asserts
   **byte equality of `response.content`** across two identical POSTs, not just dict
   equality — a much stronger and much harder-to-accidentally-break assertion.
7. **The handler has no writer in scope.** `POST /api/derive` performs zero writes;
   workspace autosave is F14-S2's separate `PUT /api/workspaces/{key}`. POST is used only
   because the payload is an object — the endpoint is semantically safe to retry.

Registration note for implementation (PM flagged it, repeating it so it isn't lost):
`create_app()` mounts the static UI at `/` **last**; the `/api/derive` router must be
included **before** that mount or it 404s whenever a UI build exists.

**Frontend side (D13, `useDerive`).** 150ms debounce; one `AbortController` per request,
aborted when a new one starts; plus a monotonic request sequence number checked before
`setState`, so even a response that resolves after its abort cannot land (defense in
depth — abort alone is racy against an already-resolved fetch). Aborts are not surfaced as
errors. The hook holds `{derived, status, error}` and **computes nothing**: the rule for
review and for the Vitest/Playwright layers is *TypeScript may format, never compute* —
`toFixed`, `toLocaleString`, string interpolation, and color/label selection from an enum
are allowed; arithmetic combining two response fields is a review defect. Contribution %,
bucket dollar boundaries, candidate premium, and the sensitivity band are all in the
payload for exactly this reason.

---

## 5. Alternatives considered and rejected

1. **Class-based stage registry** (`class Stage(ABC)` + `@register` + discovery).
   Rejected: the stage set is known at author time and fan-in shaped (buckets need
   premiums *and* anchor; kNN needs premiums *and* candidate premium), so a uniform
   `run(state) -> state` signature forces a god-object state bag through every stage.
   That bag would hand `effective_dom` to `distance()`, demoting F11-S1's AC and D19a's
   guard from a call-graph fact to a code-inspection promise. A registry also hides the
   execution order that this ADR exists to make legible. Cost of rejecting it: adding a
   stage touches one extra line. Worth it.
2. **A single frozen `Derivation` state object threaded through `f(d) -> d` stages.**
   Same leakage objection, plus every intermediate field is `Optional` until its stage
   runs, so the type stops describing anything.
3. **Lazy / partial recompute** (dirty-flag graph, recompute only downstream of what
   changed). Rejected: ARCHITECTURE §4 already rejects partial-update endpoints for this
   reason — "with a single endpoint recomputing everything from scratch, the invariant is
   structurally true rather than something we defend with careful cache invalidation."
   The saving is <5ms; the cost is the exact bug class (a stale panel) the story forbids.
4. **Memoizing `DerivedState` on a request hash.** Rejected: the body changes on every
   drag tick by construction, so the hit rate is ~0 outside undo, and it adds
   invalidation risk for no measured gain.
5. **Comps in the request body.** Rejected: ~40KB uploaded every 150ms, and it puts the
   evidence under client control, which NORTH_STAR's thesis does not allow.
6. **Streaming / multi-part responses so slow panels arrive later.** Rejected: it makes a
   partially-updated screen representable, which is the reactivity invariant inverted.

---

## 6. Open questions for the owner

**Q1 — `as_of` = the pull date, or the wall clock? (the one with meaning attached).**
I propose `as_of = pull.fetched_at`. `NORTH_STAR.md` says censored means "still active
*as of the pull*, its DOM-so-far is a floor"; spec §1 says "today − first listed date."
For a pull run today they are the same number; for a workspace reopened a week later they
differ by a week. Using the wall clock would add 7 days of *unobserved* time to every
censored floor (we do not know the unit is still listed) and would silently re-classify
removals across the pending→provisional→confirmed ladder with no new evidence — and it
makes the idempotence AC untestable. Using the pull date means "refresh to re-classify",
which is exactly what F4-S8 already specifies. **This changes what a DOM floor counts, so
it is yours, not mine.** Recommendation: pull date. If you prefer the wall clock, I'll
draft the 5-question write-up rather than implement it.

**Q2 — When the drift band straddles the guard, what renders?** The neighbor set is
drift-dependent (§2.2), so the guard can trip at d−2 and pass at d and d+2. Options:
(a) decide on mid drift; (b) conservative — guard state if it trips at *any* of the three.
Recommendation: **(b)**, on the honesty invariant: a band whose edge has no evidence is
not a band. Final wording belongs to F11-S3; I need the rule now because it determines
whether `price_test` can be a clean discriminated union (it can, under either option).

**Q3 — Is the drift sensitivity ±2 percentage points, fixed?** Spec §5.2's example is
+5/+7/+9 and F11-S4 says "recompute at d−2/d/d+2", but §2.3 has no knob for it.
Recommendation: a module constant `DRIFT_SENSITIVITY_PTS = 2.0`, not a config knob — one
fewer thing to keep coherent. Say the word and it becomes a knob in F0-S5 instead.

**Q4 — Naming: `confirmed` vs `leased`.** ARCHITECTURE §3 calls the domain field's third
state `leased`; F4-S8 and NORTH_STAR call it `confirmed`. Same state. I've used
`pending | provisional | confirmed` (the two documents that define its meaning), with
`removal_class = None` for still-active comps. Confirm and I'll note the ARCHITECTURE §3
wording as superseded; this is a naming reconciliation, not a semantic change.

**Q5 — FYI, no decision needed unless you object:** `Config.km_horizons_days` becomes a
tuple (§3.1), and the `add-pipeline-stage` skill's reference file needs the A/B split
amendment (§2.1).
