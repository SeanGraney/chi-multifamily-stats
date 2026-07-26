# RentComp — Technical Stories (MVP backlog)

> **Stack-bound.** These stories assume the decisions in `rentcomp-pm/ARCHITECTURE.md` (FastAPI backend owning all derivation · Vite/React view layer computing no statistics · JSON files on disk, no DB · no ML/kNN libraries). Where a story cites a decision (D1–D20), that decision is binding and not the developer's to revisit.

Stories grouped under the epics from *RentComp — Epics (MVP)*, plus an F0 foundations epic the epics doc implies but doesn't own. Tags: `[BE]` data/logic · `[FE]` UI · `[API]` RentCast integration · `[TEST]` verification. Precision varies deliberately: broad stories where implementation freedom is fine, precise stories (with acceptance criteria, **AC**) where the chat-derived logic is load-bearing and getting it subtly wrong corrupts results.

---

## F0 · Foundations (implied epic)

**F0-S1a [BE] Backend scaffold.** Broad. Python 3.12 + pip + venv, `pyproject.toml`; FastAPI app; `python -m rentcomp` / `rentcomp` (console script, after `pip install -e .`) entry point serving API + built static UI on `localhost:8000` (ARCHITECTURE.md D7); package layout per ARCHITECTURE.md §2; pytest wired.

**F0-S1b [FE] Frontend scaffold.** Broad. Vite + React 18 + TypeScript + Tailwind; dark monospace theme per spec tokens; three-view routing (Home / Results / Analysis) with the persistent top bar; `openapi-typescript` codegen wired against the FastAPI schema (D12); build output consumed by the backend's static serving.

**F0-S2 [BE] Derivation pipeline — `POST /api/derive`.** Precise — this is the reactivity invariant, and **the architecture checkpoint story (ADR + owner sign-off before implementation).** Single stateless endpoint: body = full curation state `(selections, weights, filters, driftPct, candidateRent)`; response = complete `DerivedState` (anchor + sensitivity, cohort medians + thin flags, buckets, price test, breakdown counts). Chain: `cleanedComps → cohortMedians → premiums → anchor → buckets → kNN → guard → KM`. Frontend side: the `useDerive` hook (D13) — 150ms debounce, one `AbortController` per request so the latest response wins during slider drags.
**AC:** endpoint is stateless and idempotent — identical body always yields identical response; full derive at 100 comps < 100ms server-side; **no statistic is computed anywhere in TypeScript** (asserted by review); out-of-order responses cannot land (abort test).

**F0-S3 [BE] Weighted statistics module.** Precise — shared by anchor, cohort medians, kNN, KM. Python + numpy, no UI dependency (can run parallel with frontend scaffolding). Implement `weighted_median(values, weights)` (lower weighted median: smallest v where cumulative weight ≥ 50% of total) and `weighted_quantile`. Weight 0 entries excluded before computation.
**AC:** unit tests: uniform weights ≡ plain median; a weight-3 comp equals three weight-1 duplicates; empty/all-zero-weight input returns null, never NaN.

**F0-S4 [API] RentCast client.** API key entered in Settings, stored locally, sent as `X-Api-Key`; base `https://api.rentcast.io/v1`; typed wrappers for `/listings/rental/long-term`; 401 and 429 surface as distinct user-facing errors (spec §7 — never partial-render). **Two modes (hard constraint — 50 calls/month plan):** `fixture` (default: serves `fixtures/live-samples/` saved by the gate + synthetic fixtures, zero network) and `live` (explicit env flag + key required; never default). All dev, tests, and QA run in fixture mode.
**AC:** with no env flag set, the client cannot reach the network (asserted by test); live mode without a key fails loudly; fixture responses are byte-identical to the gate's saved raw responses.

**F0-S5 [BE] Config store.** All §2.3 knobs with defaults (stitch gap 42d, provisional-lease 7d, withdrawal-suspect window 6mo, k=7, bucket ±4%, min cohort 4, PAD 90d fixed, KM horizons 14/30/45/60); persisted; consumed only via the derivation graph so a knob change re-derives like any other input.

---

## F1 · Home / Recent Searches

**F1-S1 [FE] Home view.** Broad. NEW SEARCH primary button + recents table (address, specs, radius, anchor, age), newest-first, paginated.

**F1-S2 [BE] Workspace persistence.** Precise. **JSON files on disk, Python-owned — not IndexedDB** (ARCHITECTURE.md D2/§5). Curation state `{selections, weights, filters, driftPct, candidateRent}` → `~/.rentcomp/workspaces/<cache-key>.json`; raw responses live separately and immutably under `cache/<cache-key>/raw/`. Recents list is an index over stored workspaces, served by `GET /api/workspaces`.
**AC:** open recent → full state restored (including candidate rent and filter settings) with zero API calls; corrupt entry → error row with refresh offer, never a crash.

---

## F2 · New Search

**F2-S1 [FE] Search form.** Broad. All §2.1 fields, prefill from subject where sensible, inline validation (address required, sqft required, min≤max).

**F2-S2 [BE] Bed/bath range parser.** Precise. Accept `"2"` or `"1-3"` per field; emit RentCast query syntax. Exact range-parameter syntax is an **open item** — verify against RentCast docs on first live call and encode the answer in one function with tests.
**AC:** parser rejects malformed input at the form layer; single/range both round-trip through a query-string builder unit test.

**F2-S3 [BE] Call estimator.** Precise. Preview line computes `2 calls × yearsBack` (+ known pagination) before submit and feeds the same number to the cache modal — one function, two consumers, no drift between displayed and actual counts.

---

## F3 · Cache Decision Modal

**F3-S1 [BE] Cache key + raw-response store.** Precise. Key = hash of canonicalized per-search inputs (sorted keys, normalized address casing/whitespace). Store **raw** API responses, not pipeline output, so pipeline changes re-run free on cached data.
**AC:** identical params re-hash identically across sessions; changing any single param produces a different key; pipeline version bump does not invalidate cache.

**F3-S2 [FE] Modal.** Cache date · USE CACHED (free) · REFRESH with real call count from F2-S3 · cancel. Triggers: param-match on submit, stale recent (>7d), REFRESH button.

**F3-S3 [BE] Refresh atomicity — display-atomic, storage-durable.** Precise, and the distinction is the whole story (ARCHITECTURE.md D24/§5a). Refresh **displays** atomically (old complete set keeps serving until the new set is whole) but **persists** incrementally (every response that arrives is written to disk immediately and never rolled back). A partial refresh leaves a partial staging set that the retry completes for the cost of the remainder only.
**AC:** kill the network after 3 of 4 calls → (a) old workspace intact and still loadable, (b) **all 3 fetched responses present on disk**, (c) retry issues exactly 1 call, (d) ledger shows 3 spent; no state where fresh year-1 data coexists with stale year-2 data in the *displayed* workspace.

**F3-S4 [BE] Durable response cache + resumable pulls.** Precise — the API budget depends on it (D24). Per-call files named by query signature (`y2025-inactive-off000.json`); `meta.json` manifest tracks planned/satisfied/failed queries and calls spent; **raw bytes written before Pydantic validation** so a parse bug never costs a call; atomic `write .tmp → fsync → rename`; every fetch diffs planned queries against the manifest and requests only the missing ones; `ledger.json` increments when a request is *sent*, not when a batch succeeds.
**AC:** a deliberately-thrown validation error still leaves the raw response on disk and a re-run parses it with zero calls; a corrupted `.tmp` (simulated crash) is never mistaken for satisfied; re-running an identical complete pull issues **zero** calls; re-running a 3-of-4 pull issues exactly 1; failed calls recorded distinctly from successes.

---

## F4 · Pull & Clean (the pipeline epic — most precise stories live here)

**F4-S1 [BE] Query planner.** Precise, per spec §3.2. For each year y in 0..N−1 compute the daysOld window with PAD=90 on both sides, `daysOldMin` floored at 1; emit two queries per year (`status=Active`, `status=Inactive`), `limit=500`, offset pagination until short page.
**AC:** unit tests with a frozen "today" covering: window in current year partially in the future (daysOldMin floor), year boundaries (Dec–Jan windows spanning year end), leap day. Planner output is a pure function of (params, today).

**F4-S2 [BE] Dedupe + spell extraction.** Precise. Dedupe on listing `id`; normalize address+unit (case, abbreviations, unit designators) as the grouping key. Extract spells from both top-level record fields and `history` events — the RentCast `history` object is keyed by date with `listedDate`/`removedDate`/`price` per event.
**AC:** a record whose history holds two prior spells yields three spell rows; two records at the same normalized address+unit merge into one group.

**F4-S3 [BE] Stitcher.** Precise — primary cleaning measure. Sort a group's spells by listedDate; merge consecutive spells where `gap = nextListed − prevRemoved < threshold` (42d/6-week default). Off market ≥ threshold ⇒ prior spell is **complete**. Merged record: initialAsk = first spell's price · effectiveDOM = final removal − first listed (**gap days count**) · censored if last spell active · cutHistory = all price changes across spells (a re-list at a lower price **is** a cut) · relistCount, gapDays retained for badges.
**AC:** property tests: threshold 0 ⇒ no merging; spells with gap = threshold−1 merge, gap = threshold don't; DOM of a merged chain ≥ sum of spell DOMs; a chain ending in an active spell is censored with DOM = today − first listed. Golden-file test on a hand-built fixture of ~15 pathological listings (laundered DOM, triple re-list, price-up re-list, fell-through lease re-listed at week 5).

**F4-S8 [BE] Removal classification + withdrawal-suspect flag.** Precise — closes the removal≈leased blind spot (review A2). Three-state on recent removals: off market < 7d ⇒ `pending` (excluded from all leased stats, row shows "removed 4d — classifying"); ≥ 7d ⇒ `provisional` lease (counted as leased, marked); ≥ 42d with no re-list ⇒ `confirmed` (marker drops). Refresh re-classifies; a provisional that re-lists is stitched back into its spell. Separately: any complete spell whose unit re-lists 6w–6mo later ⇒ `withdrawalSuspect = true` ("removed, re-listed later — lease uncertain"), shown on row and counted in bucket stats. Display-only; never auto-excluded.
**AC:** state transitions are pure functions of (spells, today, config); fixture covering all four paths (pending→provisional→confirmed, provisional→re-list→stitched, confirmed→suspect, clean confirmed); bucket leased-DOM stats exclude pendings and mark provisionals.

**F4-S4 [BE] Window filter + cohort assignment.** Precise. Keep records whose *stitched* start month-day falls inside the year-agnostic window; assign cohort = calendar year of stitched start.
**AC:** a listing re-listed inside the window but originally listed before it is kept iff its stitched start is inside; padding-only records (pulled but stitched-start outside) are dropped and counted in a pipeline debug summary.

**F4-S5 [BE] Premium computation.** Precise. `premium = initial $/sqft ÷ cohort weighted median $/sqft − 1`, cohort median over **selected** comps in that cohort; if selected cohort count < min (4), fall back to all *pulled* comps in cohort and set a flag the UI must surface. Missing-sqft comps: flagged, default weight 0, excluded from every median.
**AC:** premiums are recomputed when selection changes (they depend on cohort medians); fallback flag flips correctly as comps are toggled across the threshold.

**F4-S6 [FE] Pipeline progress + empty/partial states.** Broad. Per-year pull → stitch → derive progress; 0-comp empty state names the binding constraint and offers widen shortcuts. **Partial-pull state** (D24/§5a): when the manifest shows missing windows, the workspace is still usable but names the gap precisely — "2025 inactive missing · 1 call to complete" with a resume action — since a missing cohort skews every downstream number and must never be silently absent.

**F4-S7 [TEST] First-live-pull verification harness.** Precise — spec §3.4. A dev-mode report answering: does `history` preserve prior spells on known re-listed properties; what does each property type return near the subject; actual pagination behavior. Output feeds decisions (fallback stitching path, default type set).

---

## F5 · Comp Curation — List

**F5-S1 [FE] Comp row component.** Broad. All spec §6.4 row fields incl. cut-history line, stitch badge, no-sqft badge, removal-class markers (provisional/pending), withdrawal-suspect badge, expand panel. Plus: display **$/sqft** on every row with a "verify sqft" flag when a comp's $/sqft deviates >~30% from its cohort median (review A3 — wrong sqft silently corrupts premiums; Zillow link is the verification path).

**F5-S2 [BE/FE] Selection & weight state.** Precise. Curation state is client-owned React state (D13); toggle-off ≡ weight 0 (one source of truth: the weight); edits debounce ~150ms then `POST /api/derive` via the `useDerive` hook — **contribution % is computed server-side** like every other derived value (weight ÷ Σ selected weights), warning color above ~40% applied in the view.
**AC:** toggling and setting weight 0 produce identical derived state; ALL/NONE operate on currently visible (unfiltered) comps only.

**F5-S3 [FE] Analysis gate.** ANALYSIS button disabled with reason below 5 included comps.

---

## F6 · Comp Curation — Map

**F6-S1 [FE] Leaflet map.** Broad. OSM tiles, subject teardrop always on top, pin states (green/grey/rust), pulsing ring for censored, legend.

**F6-S2 [FE] Pin tooltip card.** Precise (it was missing in the prototype). Click → card with address, ask, premium, DOM/floor, cut/re-list badges, INCLUDE/EXCLUDE, Zillow + Street View icons; row highlight + scroll-into-view; rust pin click → re-include override.
**AC:** map and list never disagree on a comp's state; overlapping pins offset/spiderfy on click so every comp is reachable.

**F6-S3 [FE] Two-way hover sync.** Row hover ↔ pin highlight.

---

## F7 · Client-side Filters

**F7-S1 [BE/FE] Filter engine.** Precise about semantics: filtered comps leave the list **and all calculations** but stay rust on the map; manual INCLUDE overrides survive filter resets; reconciliation invariant `included + excluded + filtered = pulled` asserted in dev builds.

**F7-S2 [FE] Filter strip + hidden footer.** Broad. Max distance, hide-censored, leased-only; collapsed "N filtered · show" footer with per-row INCLUDE.

---

## F8 · Anchor & Drift

**F8-S1 [BE] Anchor computation.** Precise. `adjPsf_i = initialPsf_i × (1 + drift)^(currentYear − cohortYear_i)`; anchor = weightedMedian(adjPsf) × subjectSqft. Note the exponent: drift is annual, compounded per cohort age — a 2-years-old comp gets `(1+d)²`.
**AC:** current-cohort comps are unchanged by drift; unit test that anchor with d=0 equals plain weighted median; anchor updates live with slider.

**F8-S2 [FE] Drift slider + sensitivity band.** Precise. Slider (range ~0–15%, default 7%), label "source: manual"; sensitivity line always rendered at d±2pts; the ±band propagates to bucket dollar boundaries and price-test output as a band, not a point.

**F8-S3 [FE] Thin-cohort warnings.** Rail warnings from F4-S5 flags + per-cohort counts; "anchor leans on drift" message when current cohort < min size.

---

## F9 · Comp Breakdown & Warnings

**F9-S1 [FE] Breakdown panel.** Broad. Included/censored/excluded/filtered + per-cohort counts, every count click-through to its comp subset (evidence-first invariant), live on every curation action.

---

## F10 · Bucket Overview

**F10-S1 [BE] Bucket assignment + stats.** Precise. Buckets on premium with configurable half-width; per bucket over **selected** comps: count · leased-only DOM median + min–max · cut-before-lease rate = (leased comps with ≥1 cut) ÷ (leased comps) · censored floors list. Empty bucket → nulls, rendered as dashes; **no interpolation anywhere**.

**F10-S2 [FE] Bucket table.** Dual labels: stable % definition + live dollar boundary derived from anchor (re-renders with drift/weights); counts click through; map pins recolor by bucket in Analysis view.

**F10-S3 [BE/FE] Mini-KM per bucket.** ~~MVP~~ → **moved to V2** (review action 9, traded for F4-S8/F11-S6). Reuses F11-S2's estimator over each bucket's comps; sparkline render.

---

## F11 · Price Test

**F11-S1 [BE] kNN retrieval.** Precise. **One feature, one target — do not conflate them** (ARCHITECTURE.md D19a):

- **Feature (X):** `premium` only. Distance = `|candidate_premium − comp.premium|` over selected comps.
- **Target (y):** the pair `(effective_dom, censored)` — passed through to KM untouched. **`effective_dom` must never appear in `distance()`** — selecting neighbors by their outcome is target leakage and would manufacture convincing but circular predictions.
- **No library** (D19): `d = np.abs(premiums - candidate); idx = np.argsort(d, kind="stable")[:k]`. scikit-learn is explicitly rejected — its `weights` param distance-weights a *prediction*, whereas our weights are aggregation weights consumed downstream by KM. We need retrieval only.
- User weights carried into aggregation, **not** into distance.

**AC:** `distance()` accepts only premium values — a test asserts DOM is not reachable from its inputs; `kind="stable"` makes ties deterministic (insertion order, then distance-to-subject); neighbors returned with their premium distances for display and for the guard (F11-S3); k default 7 from config.

**F11-S2 [BE] Weighted Kaplan-Meier estimator.** Very precise — the statistical core. Product-limit estimator with weighted risk sets: at each event time t (leased neighbor's effectiveDOM), `S(t) = S(t−) × (1 − d_w(t)/n_w(t))` where `d_w` = summed weights of comps leasing at t and `n_w` = summed weights still at risk (not yet leased, not yet censored) at t. Censored comps leave the risk set at their floor DOM without contributing an event. Day-1 events and ties require no special handling beyond correct risk-set accounting.
**Implementation (D8):** hand-rolled in numpy (~30 lines). `lifelines` is a **dev-only** dependency used solely by the verification test — it must not appear in runtime imports.
**AC:** verified against `lifelines` (weighted) on ≥5 fixtures including all-censored-after-first-event and heavy-tie cases; monotone non-increasing; S(0)=1; a test asserts no runtime module imports `lifelines`.

**F11-S3 [BE] Insufficient-evidence guard.** Precise — hard requirement, runs **before** any curve renders. Usable neighbor = selected, non-excluded; guard trips if < 3 usable neighbors within ±3 premium points of candidate, or all k are censored.
**AC:** the observed prototype failure is the regression test: with evidence clustered at −2%…+4% and one comp at +10%, a +16.5% candidate must render the guard state (with nearest comps + their distances), never a curve. Guard state and curve state are mutually exclusive renders.

**F11-S4 [BE] Expected vacancy + cost.** Precise. Expected vacant days = area under the KM step function truncated at the last observable time (min of last event time and largest censoring floor — state the truncation in the UI: "expected vacancy ≥/≈ N days (truncated at X)"); cost = days × candidateRent/30. Rendered as a band across the drift sensitivity range (recompute at d−2/d/d+2).

**F11-S5 [FE] Price test UI.** Broad. Rent input (slider + typed field), premium/bucket readout, KM curve with horizon markers (14/30/45/60), neighbor cards listed individually below (address, premium + distance, outcome/floor incl. provisional/suspect markers, cuts, weight, cohort year), guard state rendering.

**F11-S6 [BE] Prediction-accountability decision log.** Precise, small — the product feedback loop (review action 7). A "LOG THIS DECISION" action on the price test stores `{timestamp, subject, candidateRent, anchor, driftPct, premium, bucket, predicted KM readouts, expected vacancy band, neighbor ids+weights}` to local storage. When the subject unit actually leases, the user records actual DOM and whether the ask held; the app shows predicted vs actual.
**AC:** log write is one click; log survives cache refresh and pipeline changes; predicted-vs-actual view renders even for a single entry.

---

## F12 · External Verification Links

**F12-S1 [BE] Link builders.** Precise enough to test. Zillow: slugify `formattedAddress` (spaces→`-`, strip punctuation, preserve unit designators) → `https://www.zillow.com/homes/{slug}_rb/`. Street View: `https://www.google.com/maps?q&layer=c&cbll={lat},{lng}`. Both open new tab.
**AC:** slug unit tests incl. unit numbers (`Apt 2`, `Unit B`, `# 3`) and directionals (`W`, `S`); dead Zillow link degrades to their search page — acceptable by design.

**F12-S2 [FE] Placement.** Icons on expanded row + pin tooltip.

---

## F13 · Refresh / Re-pull

**F13-S1 [BE] Curation reconciliation.** Precise. After refresh, re-key selections/weights by normalized address+unit (not listing id — ids can churn). New comps arrive included at weight 1, marked NEW; vanished comps kept + flagged "no longer in source"; changed comps (new status, new spells) re-run the pipeline and keep their weight.
**AC:** refresh over an unchanged dataset is a no-op for curation state; diff summary counts (newly leased / new listings / re-lists stitched) reconcile with record-level changes.

**F13-S2 [FE] Diff surface.** Change summary banner post-refresh; NEW badges.

---

## F14 · Navigation & State

**F14-S1 [FE] Tab state preservation.** COMPS ↔ ANALYSIS round-trips lose nothing (candidate rent, scroll positions); curation changes re-derive analysis on return (free via F0-S2).

**F14-S2 [FE] Autosave.** Any workspace mutation persists (debounced) to the F1-S2 store; back-to-Home is not a save event, it's already saved.

**F14-S3 [FE] Scroll correctness.** All three views scroll at 700–800px viewport heights with visible scrollbars; regression test: neighbor cards reachable at 744px (the observed prototype failure height).

---

## Cross-epic test stories

**T-S1 [TEST] Pipeline golden files.** One fixture set of ~30 synthetic raw listings covering every §7 edge case; snapshot the full pipeline output; any pipeline change must re-bless the snapshot deliberately.

**T-S2 [TEST] Invariant suite.** Automated checks for the five cross-cutting invariants (evidence-first click-through paths exist; censored never counted as leased; no derivation staleness; no API call outside the modal path; no destructive operation except confirmed refresh).

**T-S3 [TEST] Go/no-go gate (reframed — runs BEFORE build commitment).** F4-S7 harness against the real subject address (**≤10 API calls — budgeted against the 50/month cap**) answering §3.4 plus the killer assumption: does a real window pull return enough usable comps? **Every raw response is committed to `fixtures/live-samples/`** — it is both the verification evidence and the canonical sample dataset the entire build develops against (fixture mode, F0-S4). Outputs a decision record into the repo. If coverage fails, the next step is redesign (wider radius/window, different source), not sprint 1.

**T-S4 [TEST] Three-layer regression suite — the engineering success definition.** Per ARCHITECTURE.md §9/D21, assertions live at the lowest layer that can hold them:

- **pytest unit** — stats, pipeline stages, planners, builders; `hypothesis` property tests where stories call for them
- **pytest API contract** (`TestClient`, temp `RENTCOMP_HOME`) — **the workhorse**; most story ACs live here (guard behavior, weight equivalence, drift math, bucket classification, reconciliation invariant, cache no-network rule, statelessness)
- **Playwright** — one spec per epic flow F1–F14, built alongside each flow (a flow is not "done" until its spec passes). Browser-genuine concerns only: flow completion, guard-vs-curve render exclusivity, panels updating together, map↔list sync, scroll at 744px, cache-modal consent path, evidence-first click-through. `globalSetup` seeds a temp fixture home; `webServer` launches `rentcomp` in fixture mode.
- **Vitest** — one file: `useDerive` debounce + AbortController latest-wins

**AC:** all layers run headless in CI; **zero live API calls** anywhere in the suite; a logic assertion found in a Playwright spec is a review defect; `pytest && vitest run && playwright test` green on a main-synced branch is the release gate for MVP.
