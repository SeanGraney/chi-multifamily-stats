# RentComp — Functional Design Spec

**Purpose:** Predict how long a rental unit will sit vacant at a given asking rent, using manually curated comps from the RentCast API, in a low-listing-volume micro-market (Chicago small multifamily).

**Core thesis:** Don't model the market — retrieve the most relevant historical evidence, clean it hard, and put it in front of the user. Every aggregate stays one click from its underlying comps. The user makes the final judgment; the tool's job is making the evidence honest.

**Platform:** Desktop web. Visual language inherited from the reference mock (dark surface `#0b0c0b`, monospace type, amber `#f5a623` = subject/anchor/emphasis, green `#52d48a` = included, grey `#555` = excluded, rust `#c45c2a` = filtered-hidden).

---

## 1. Definitions

| Term | Definition |
|---|---|
| **Subject** | The unit being priced (address + specs) |
| **Spell** | One continuous listing period (listed → removed, or listed → still active) |
| **Stitched listing** | One or more spells at the same address+unit merged across short gaps; the atomic comp record |
| **Initial ask** | Price of the *first* spell of a stitched listing. All premiums are computed from this, never from later/cut prices |
| **Effective DOM** | Final removal date − first listed date of the stitched chain (gap days count — the unit was still unleased). For active listings: today − first listed date, flagged **censored** |
| **Censored** | Still-active listing. Its DOM is a floor ("45+ days"), never a data point equal to 45 |
| **Cohort** | All comps whose stitched start date falls in the same year's date window (e.g., "June 15–30, 2024" cohort) |
| **Premium** | Comp's initial $/sqft ÷ its cohort's weighted median $/sqft − 1. Time-local: a comp is measured against *its own year's* market, so no dollar conversion is needed in the DOM model |
| **Drift** | Year-over-year rent growth factor used to pull older comps' dollar values forward to today — used **only** for the anchor, never for premiums |
| **Anchor** | Today's estimated market rent for the subject: weighted median of drift-adjusted comp $/sqft × subject sqft |

---

## 2. Inputs

### 2.1 Per-search inputs (New Search form)

| Field | Type | Notes |
|---|---|---|
| Address | text | Full `Street, City, State, Zip`; RentCast geocodes |
| Bedrooms | single value or range | e.g. `2` or `1-3`; default exact-match to subject |
| Bathrooms | single value or range | Fractional allowed (`1.5`); default exact |
| Unit sqft | number | Subject attribute; required for $/sqft math |
| Property types | multi-select | Default: **Multi-Family + Apartment + Townhouse** (Apartment included because unit-level listings in 2–4 flats are frequently typed "Apartment" in RentCast's taxonomy) |
| Radius | number (mi) | Comp search radius |
| Date window | month-day → month-day | Year-agnostic, e.g. Jun 15 – Jun 30 |
| Years back | 1–5, default 2 | Number of yearly cohorts to pull |
| Comp source mode | Exact / RentCast | Exact = `/listings` pipeline (primary). RentCast = `/avm` comps + estimate (benchmark only) |

### 2.2 Runtime input

- **Candidate asking rent** — entered on the Analysis screen; drives the price test.

### 2.3 Config knobs (Settings, with defaults)

| Knob | Default | Range |
|---|---|---|
| Stitch gap threshold (historical) | 42 days (6 weeks) | 7–60 |
| Provisional-lease threshold (recent removals) | 7 days | 3–21 |
| Withdrawal-suspect window | 6 months | 2–10 months |
| kNN neighbor count (k) | 7 | 3–15 |
| Bucket half-width | ±4% | ±2–10% |
| Min cohort size before fallback | 4 | 2–10 |
| Drift source | Manual (MVP) | zip / cohort / manual (auto sources v2) |
| Query padding | 90 days | fixed, internal |
| KM display horizons | 14 / 30 / 45 / 60 days | editable |

### 2.4 Amenities — no panel (removed after prototype review)

RentCast listing data contains **no amenity fields** — amenities cannot filter the comp pull, and the coefficient-checkbox panel from the reference mock is dropped. Amenity comparison happens by eye via **external links on every comp** (see §6.4): a Zillow deep link (`zillow.com/homes/{address-slug}_rb/` — photos + listing history) and a Google Street View link (built from lat/long — building condition/curb appeal). What the user sees there folds into the comp's manual weight.

---

## 3. Data layer

### 3.1 Endpoints used

| Endpoint | Role |
|---|---|
| `GET /listings/rental/long-term` | Primary comp pull |
| `GET /markets?zipCode&dataType=Rental&historyRange=N` | Drift index (v2) + zip context (median DOM, listing counts) |
| `GET /avm/rent/long-term` | Optional benchmark (v2): RentCast's own estimate + comps as a sanity check |

Auth: `X-Api-Key` header. Base: `https://api.rentcast.io/v1`.

### 3.2 Year-agnostic window queries

There is no `listedDate` filter; `daysOld` (days since listed) supports numeric ranges. For each year *y* in 0..N−1:

```
windowStart_y = (windowStartMonthDay in year: currentYear − y)
windowEnd_y   = (windowEndMonthDay   in year: currentYear − y)
daysOldMin    = today − windowEnd_y   − PAD   (floor at 1)
daysOldMax    = today − windowStart_y + PAD
```

`PAD = 90 days` both sides, because RentCast **resets `listedDate` on re-list** — a stitched listing's true start can fall inside the window even when its latest re-list doesn't. Post-stitch, records are filtered to those whose *stitched* start month-day falls inside the window.

Per year, two calls (the `status` param is single-valued): `status=Active` and `status=Inactive`, each with `limit=500` + pagination via `offset`. Common params: address+radius, propertyType (multi-value), bedrooms, bathrooms.

### 3.3 Call budget & caching

Per full pull: ~2 calls × N years (+ pagination). Caching is first-class:

- Cache key = hash of all per-search inputs; stores **raw** API responses (so pipeline changes re-run on cached data for free)
- Re-running a cached search → modal: **Use cached (free) / Refresh (~X API calls)** with the actual call count
- Footer shows cache date on every results view

### 3.4 Verification items (first live pull)

1. Whether `history` reliably preserves prior spells on re-listed properties. Fallback if spotty: stitch by matching separate records on normalized address+unit.
2. How far back `/markets` `historyRange` actually returns on the user's plan (v2 drift). Fallback: build the drift index from our own wide listing pulls (median $/sqft per month within the radius).
3. What each property type actually returns near the subject; trim the default type set empirically.

---

## 4. Processing pipeline

Order is load-bearing. Stitching is a **primary cleaning measure** — it runs before anything downstream sees the data.

```
raw listings (padded pull, both statuses, all years)
  → 1. DEDUPE     on listing id / normalized address+unit
  → 2. GROUP      spells per address+unit (from records + history events)
  → 3. STITCH     merge consecutive spells with gap < threshold (42d default)
  → 4. DERIVE     per stitched listing:
                    initialAsk, effectiveDOM, censored flag,
                    removalClass (leased / provisional / pending),
                    withdrawalSuspect flag,
                    cutHistory [(date, from, to)], relistCount, gapDays
  → 5. WINDOW     keep records whose stitched start falls in the year-agnostic window
  → 6. COHORT     assign each record to its year cohort
  → 7. PREMIUM    initial $/sqft ÷ cohort weighted median $/sqft − 1
  → present for manual selection
```

**Stitching rules (historical):** same address+unit; gap < 6 weeks ⇒ same vacancy spell — DOM-counter laundering, refreshes, failed applicants, and even fell-through leases all merge (a lease that collapses at week 5 is defensibly continued vacancy). Off market ≥ 6 weeks ⇒ **complete**, counts as leased. Merged record keeps first spell's price as initial ask; price differences across spells register as cuts. UI marks stitched records ("re-listed ×2, 9-day gap") so the user can override by eye.

**Recent removals — three-state classification** (historical rule can't apply because the observation window hasn't elapsed): off market < 7 days ⇒ **pending** ("removed 4d ago — classifying", excluded from leased stats); ≥ 7 days ⇒ **provisional lease**, counted as leased with a marker; ≥ 6 weeks with no re-list ⇒ **confirmed**, marker drops. Refresh re-classifies automatically — a provisional that re-lists is stitched back into its spell.

**Withdrawal-suspect flag:** a complete spell whose unit re-lists 6 weeks–6 months later gets flagged "removed, re-listed later — lease uncertain" on the row and in bucket stats. A real lease implies ~12 months of tenancy, so a quick reappearance means withdrawal, eviction, or a broken lease — this catches the winter-withdrawal case that the 6-week rule alone would count as a fast lease. Display-only; the user judges.

**Cohort median source:** selected comps in that cohort. If a cohort has < min size (default 4) selected, fall back to *all pulled* comps in the cohort and flag it in the UI.

**Missing sqft:** flag the comp and default-exclude (weight 0); user can manually re-include. Row shows a "no sqft" badge.

---

## 5. Algorithms

### 5.1 Manual selection & weights

- Every comp row has an include toggle and a **weight** field, default 1, free numeric, no cap. Weight 0 ≡ excluded-but-visible. Toggle-off is a weight-0 shortcut.
- Each comp shows its **effective % contribution** next to the weight so the user can see when one comp dominates.
- Weights propagate everywhere: anchor median, cohort medians, kNN, KM.

### 5.2 Anchor (today's market rent)

```
for each selected comp:
    adjPsf = comp initial $/sqft × drift(comp.cohortYear → today)
anchorRent = weightedMedian(adjPsf, weights) × subjectSqft
```

**Drift factor (MVP = manual):** a slider in the rail with a sensible default (7%/yr), label "source: manual". V2 adds zip $/sqft growth (bedroom-matched, 3-month smoothed, ratio-based) and cohort-ratio cross-check.

**Sensitivity is mandatory UI:** drift error passes 1:1 into the premium read. Display: `Anchor $2,250 (assumes +7%/yr drift · at +5% → $2,207 · at +9% → $2,293)`. All downstream outputs (buckets, price test) render as bands across this range, not points. Current-cohort comps that land near/far from the drift-projected anchor are surfaced as confirmation/warning.

### 5.3 Bucket overview (static read)

Three buckets on initial premium, half-width configurable (±4% default). Labels show **both** the % definition and the live dollar boundary (dollars derive from the anchor and shift with weights/drift; % is the stable definition):

```
BELOW MARKET          AT MARKET               ABOVE MARKET
< −4%  (< $2,160)     ±4%  ($2,160–$2,340)    > +4%  (> $2,340)
```

Per bucket: comp count · leased DOM median + range (provisional leases marked, pendings excluded) · **cut-before-lease rate** (the knee detector: when most of the above bucket had to cut, the ask didn't hold at all) · withdrawal-suspect count · censored count with floors ("2 active at 45+, 60+"). Every count clicks through to its comps. (Per-bucket mini-KM sparklines moved to v2.)

Buckets do **not** interpolate: if no comps exist near a premium, that absence is shown, not smoothed over.

### 5.4 Price test: kNN retrieval → weighted KM aggregation

The interactive core. kNN does retrieval only; survival math does the statistics.

```
input: candidateRent
premium  = candidateRent / anchorRent − 1        (shown as % and bucket)
neighbors = k comps nearest by |premium − comp.premium|,
            weighted by user weights (weight 2 ≈ counts twice)
curve    = weighted Kaplan-Meier over neighbors:
            leased neighbor  → event at effectiveDOM
            censored neighbor  → censored at current DOM (contributes
              "survived vacant past X" — consumed properly, not floored)
output   = P(still vacant at day t) curve + readouts at display horizons
            ("still vacant at day 30: 62%"), rendered as a band across
            the drift sensitivity range
```

Below the curve, the k neighbors are listed **individually** — premium, DOM or floor, cut history, weight — because the neighbors *are* the answer; the curve is a summary.

**Honest failure modes (hard requirements):**
- All k neighbors censored, or < 3 usable neighbors within ~±3 premium points → render **"insufficient evidence at this premium"** with the nearest comps and their premium distances shown, never a fake curve.
- Nearest neighbors far from the candidate premium → show the actual premium distances; no interpolation.

**Expected-cost readout (derived):** expected vacant days ≈ area under the KM curve (truncated at horizon) → `expected vacancy cost ≈ days × candidateRent/30`, enabling direct comparison of candidate prices.

### 5.5 Out of MVP (v2)

Per-bucket mini-KM sparklines · two-price comparison overlay · AFT model overlay (Weibull/log-normal accelerated failure time, single covariate, wide bands, visually subordinate) · automatic drift sources (zip index, cohort ratio) · RentCast AVM benchmark card · per-cohort drift auto-validation · CSV export · second-unit side-by-side. Ruled out entirely: kernel/LOESS (censoring-blind), GPs/trees (sample size, opacity), Cox (hazard ratios less usable than time multipliers).

---

## 6. UI — Desktop

### 6.1 Screens

```
① HOME            recents + new search
② RESULTS         map (top) + comp list (below) + right stats rail
③ ANALYSIS        anchor header + buckets + price test
```

Persistent top bar: app mark · subject address + specs · by anchor (live) · refresh (goes through cache modal) · tab switch COMPS / ANALYSIS.

All views must scroll properly at laptop viewport heights (~700–800px) — visible scrollbars; no content trapped below a fixed fold. The Analysis screen's nearest-neighbor cards in particular must always be reachable.

### 6.2 Home

NEW SEARCH primary button; recent searches table (address, specs, radius, anchor, age) paginated; click → Results from cache (modal offers refresh when stale).

### 6.3 New Search

Modal. All §2.1 fields. Date window as two month-day pickers + years-back stepper (1–5, default 2) with a preview line: *"Jun 15–30 · 2026, 2025 (2 cohorts) · est. N API calls."*

### 6.4 Results — map + comp list + stats rail

Layout: map fills top ~55% of viewport, full-width minus a 320px right rail; comp list scrolls beneath the map; rail is sticky.

**Map (Leaflet):**
- Subject: amber teardrop, glow, always on top
- Comp pins by state: green = included · grey = excluded · rust = client-side-filtered (visible on map, hidden from list; click to re-include)
- Pulsing ring on censored/active pins; pin size up on hover/select
- Pin click → tooltip card: address, initial ask, premium vs cohort, DOM or floor, cut/re-list badges, INCLUDE/EXCLUDE button, Zillow + Street View link icons
- Map ↔ list two-way sync: pin click highlights row and scrolls to it; row hover highlights pin
- Legend bottom-right

**Comp row:**

```
[߳ toggle] 3156 S Morgan St            [wt: 1.0] [3.2%↑]   $2,050/mo
  INACTIVE · 3bd·2ba · 1,380sf · 0.33mi · 2024 cohort      leased 34d
  ✂ 2,150 → 2,050 (day 21)   ⟲ re-listed ×1 (6d gap)
```

Fields: include toggle · address · weight input · effective contribution % · **premium vs cohort** (signed, colored) · initial ask · **$/sqft** (with "verify sqft" flag when deviating >~30% from cohort median — wrong sqft silently corrupts premiums; the Zillow link is the verification path) · outcome (`leased 34d` / `leased 12d ·provisional` / `removed 4d — classifying` / `active 45+ d` amber) · withdrawal-suspect badge · cohort year · cut history line · stitch badge · missing-sqft flag · expand for full detail + **Zillow deep link** (`zillow.com/homes/{address-slug}_rb/`) and **Google Street View link** (from lat/long) — RentCast has no listing URLs or photos, so these are the visual-inspection path. ALL / NONE bulk toggles kept.

**Filter bar** (client-side post-pull; filtered comps leave the list but stay rust on the map, INCLUDE overrides): max distance · hide/show censored · leased-only. Collapsed "N filtered · show" footer with per-row INCLUDE.

**Right rail:** top → bottom:
1. **ANCHOR** — big number, drift assumption + sensitivity band, manual drift slider
2. **COMP BREAKDOWN** — included / censored / excluded / filtered counts; per-cohort counts with thin-cohort warnings ("2026: 2 comps — anchor leaning on drift")
3. **→ ANALYSIS** button (disabled with reason if < 5 included comps)

(V2 rail slot reserved between 1 and 2 for the RentCast AVM benchmark card.)

### 6.5 Analysis — buckets + price test

Full-width, replaces map region; comp list remains reachable via tab.

1. **Anchor strip:** anchor ± band, subject specs, included-comp count, drift value
2. **Bucket table** (§5.3): three columns, dual %/$ labels, stat rows (count, DOM median+range, cut rate, withdrawal-suspect count, censored floors), counts click through to comp subsets; **map pins recolor by bucket** in this view
3. **PRICE TEST:** rent input → premium readout + bucket highlight → guard check (§5.4) → KM vacancy curve (band across drift sensitivity) with horizon markers → expected-vacant-days + vacancy-cost line → **k neighbor cards** listed individually → honest-failure states per §5.4

### 6.6 Cache modal

Cache date, USE CACHED (free) vs REFRESH with real call count, cancel.

---

## 7. States & edge cases

| Situation | Behavior |
|---|---|
| Pull returns 0 comps | Suggest widening radius/window/types; show which constraint bound |
| Cohort below min size | Flag; cohort median falls back to all-pulled (marked) |
| Current cohort sparse | Expected (that's why the tool exists): anchor leans on drift-adjusted older comps; rail warning + sensitivity band widens |
| All neighbors censored / < 3 usable in range | "Insufficient evidence at this premium" — no curve |
| Comp missing sqft | Default-excluded with `no sqft` badge; manual re-include allowed |
| Comp $/sqft deviates >30% from cohort median | "verify sqft" flag; user checks via Zillow link |
| Removal < 7 days old | Pending state — excluded from leased stats until classified |
| Complete spell re-lists within 6w–6mo | Withdrawal-suspect flag on row + bucket stats; display-only |
| One weight dominating | Effective-contribution % makes it visible; no hard cap by design |
| `history` missing prior spells | Stitch via address-matched separate records |
| API 401 / rate limit | Surface plainly; never partial-render stale-mixed data |

---

## 8. Success definition, non-goals & deadline

**Success definition (engineering — the release gate):** every epic flow F1–F14 is executable end-to-end, and each flow has a **Playwright** regression test built alongside it — a flow is not "done" until its test exists and passes. (Playwright over Puppeteer: built-in test runner, auto-waiting, fixtures for seeding cached workspaces.)

**Success definition (product — the feedback loop):** at each pricing decision, log `{candidate ask, predicted vacancy band, bucket, neighbor set, date}`. When the unit actually leases, compare. Success = actual DOM inside the predicted band and the ask held without a cut. Leading indicators: pricing decision reached in one evening per unit; ≥15 usable comps per pull; guard trip rate <50% across the price range actually under consideration.

**Non-goals (permanent exclusions, not deferrals):** not an automated pricer (tool informs, user decides — by design) · not a rent maximizer (objective is vacancy-adjusted revenue, not peak rent) · not multi-market (Chicago small-multifamily assumptions baked into property types and stitching heuristics) · not multi-user (single-user caching/settings assumed throughout) · no amenity modeling at this sample size, ever (eyes + weights, permanently).

**Deadline anchor: 7/29/2026.** The tool must be able to price a real unit by this date. **Working beats pretty, without exception, for this build.** If 7/29 arrives before V1 is complete, the bar drops to whatever's furthest along the pipeline end-to-end — an ugly-but-complete walking skeleton that can actually price a unit beats a polished screen sitting on an unverified stitcher. This is not a fallback to feel bad about; it's the intended reading of the build order in §9 under time pressure. The PM tracks progress against this date (see `PROJECT_MANAGER.md`'s "MVP exit gate" and "Deadline awareness") and reports status rather than silently grinding on polish if the date arrives first.

---

## 9. Build order

**Gate (before committing to the build):** verification harness against the real subject address (~10 API calls) answering §3.4 — data coverage is the killer assumption (zero live calls made to date). If a real window pull yields too few comps, the fallback is redesign (wider radius/window or different data source), not a bugfix.

**Walking skeleton (first vertical slice):** one hardcoded search flowing end-to-end — pull → stitch → plain list → anchor → bucket table → price test, zero styling. Proves every integration seam early and can price a unit if time runs out.

**V1 (MVP):** search form → padded pulls + cache → stitching + derivation (incl. three-state removal classification + withdrawal-suspect) → map/list/rail with selection & weights → anchor with manual drift + sensitivity → buckets → price test (kNN + weighted KM + insufficient-evidence guard) → cache modal → Zillow/Street View links → pin tooltips → decision log → Playwright flow tests throughout (see §8).

**V2:** automatic drift sources (zip index + cohort ratio) · per-bucket mini-KM sparklines · RentCast AVM benchmark · AFT overlay · two-candidate comparison · per-cohort drift auto-validation · CSV export of cleaned comp set · second-unit side-by-side.

Open items: exact numeric-range syntax for `daysOld`/`bedrooms` params (verify against RentCast docs on first call) · §3.4 verification items · Zillow deep-link format.
