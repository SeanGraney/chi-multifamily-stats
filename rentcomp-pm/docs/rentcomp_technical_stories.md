# RentComp — Technical Stories (MVP backlog)

Stories grouped under the epics from *RentComp — Epics (MVP)*, plus an F0 foundations epic the epics doc implies but doesn't own. Tags: `[BE]` data/logic · `[FE]` UI · `[API]` RentCast integration · `[TEST]` verification. Precision varies deliberately: broad stories where implementation freedom is fine, precise stories (with acceptance criteria, **AC**) where the chat-derived logic is load-bearing and getting it subtly wrong corrupts results.

---

## F0 · Foundations (implied epic)

**F0-S1 [FE] Project scaffold.** Broad. Desktop web app (React + TypeScript suggested), dark monospace theme per spec tokens, three-view routing (Home / Results / Analysis) with the persistent top bar.

**F0-S2 [BE] Reactive derivation graph.** Precise — this is the reactivity invariant. Single derivation chain: `(rawComps, selections, weights, filters, driftPct, config) → cleanedComps → cohortMedians → premiums → anchor → buckets → priceTest`. Any input mutation re-derives everything downstream in one pass.
**AC:** no memoized stage can serve stale output after an upstream change; full re-derive at 100 comps completes < 100ms; there is exactly one code path that computes each derived value (no UI-local recomputation).

**F0-S3 [BE] Weighted statistics module.** Precise — shared by anchor, cohort medians, kNN, KM. Implement `weightedMedian(values, weights)` (lower weighted median: smallest v where cumulative weight ≥ 50% of total) and `weightedQuantile`. Weight 0 entries excluded before computation.
**AC:** unit tests: uniform weights ≡ plain median; a weight-3 comp equals three weight-1 duplicates; empty/all-zero-weight input returns null, never NaN.

**F0-S4 [API] RentCast client.** API key entered in Settings, stored locally, sent as `X-Api-Key`; base `https://api.rentcast.io/v1`; typed wrappers for `/listings/rental/long-term`; 401 and 429 surface as distinct user-facing errors (spec §7 — never partial-render).

**F0-S5 [BE] Config store.** All §2.3 knobs with defaults (stitch gap 21d, k=7, bucket ±4%, min cohort 4, PAD 90d fixed, KM horizons 14/30/45/60); persisted; consumed only via the derivation graph so a knob change re-derives like any other input.

---

## F1 · Home / Recent Searches

**F1-S1 [FE] Home view.** Broad. NEW SEARCH primary button + recents table (address, specs, radius, anchor, age), newest-first, paginated.

**F1-S2 [BE] Workspace persistence.** Precise. A workspace = `{searchParams, rawResponses, selections, weights, filters, driftPct, candidateRent, cacheDate}`. Persist to IndexedDB keyed by cache key (F3-S1); recents list is an index over stored workspaces.
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

**F3-S3 [BE] Refresh atomicity.** Precise. Refresh writes to a staging area; old workspace replaced only on full success.
**AC:** kill the network mid-refresh → old workspace intact and loadable; no state where fresh year-1 data coexists with stale year-2 data.

---

## F4 · Pull & Clean (the pipeline epic — most precise stories live here)

**F4-S1 [BE] Query planner.** Precise, per spec §3.2. For each year y in 0..N−1 compute the daysOld window with PAD=90 on both sides, `daysOldMin` floored at 1; emit two queries per year (`status=Active`, `status=Inactive`), `limit=500`, offset pagination until short page.
**AC:** unit tests with a frozen "today" covering: window in current year partially in the future (daysOldMin floor), year boundaries (Dec–Jan windows spanning year end), leap day. Planner output is a pure function of (params, today).

**F4-S2 [BE] Dedupe + spell extraction.** Precise. Dedupe on listing `id`; normalize address+unit (case, abbreviations, unit designators) as the grouping key. Extract spells from both top-level record fields and `history` events — the RentCast `history` object is keyed by date with `listedDate`/`removedDate`/`price` per event.
**AC:** a record whose history holds two prior spells yields three spell rows; two records at the same normalized address+unit merge into one group.

**F4-S3 [BE] Stitcher.** Precise — primary cleaning measure. Sort a group's spells by listedDate; merge consecutive spells where `gap = nextListed − prevRemoved ≤ threshold` (21d default). Merged record: initialAsk = first spell's price · effectiveDOM = final removal − first listed (**gap days count**) · censored if last spell active · cutHistory = all price changes across spells (a re-list at a lower price **is** a cut) · relistCount, gapDays retained for badges.
**AC:** property tests: threshold 0 ⇒ no merging; spells with gap = threshold merge, threshold+1 don't; DOM of a merged chain ≥ sum of spell DOMs; a chain ending in an active spell is censored with DOM = today − first listed. Golden-file test on a hand-built fixture of ~15 pathological listings (laundered DOM, triple re-list, price-up re-list).

**F4-S4 [BE] Window filter + cohort assignment.** Precise. Keep records whose *stitched* start month-day falls inside the year-agnostic window; assign cohort = calendar year of stitched start.
**AC:** a listing re-listed inside the window but originally listed before it is kept iff its stitched start is inside; padding-only records (pulled but stitched-start outside) are dropped and counted in a pipeline debug summary.

**F4-S5 [BE] Premium computation.** Precise. `premium = initial $/sqft ÷ cohort weighted median $/sqft − 1`, cohort median over **selected** comps in that cohort; if selected cohort count < min (4), fall back to all *pulled* comps in cohort and set a flag the UI must surface. Missing-sqft comps: flagged, default weight 0, excluded from every median.
**AC:** premiums are recomputed when selection changes (they depend on cohort medians); fallback flag flips correctly as comps are toggled across the threshold.

**F4-S6 [FE] Pipeline progress + empty state.** Broad. Per-year pull → stitch → derive progress; 0-comp empty state names the binding constraint and offers widen shortcuts.

**F4-S7 [TEST] First-live-pull verification harness.** Precise — spec §3.4. A dev-mode report answering: does `history` preserve prior spells on known re-listed properties; what does each property type return near the subject; actual pagination behavior. Output feeds decisions (fallback stitching path, default type set).

---

## F5 · Comp Curation — List

**F5-S1 [FE] Comp row component.** Broad. All spec §6.4 row fields incl. cut-history line, stitch badge, no-sqft badge, expand panel.

**F5-S2 [BE/FE] Selection & weight state.** Precise. Toggle-off ≡ weight 0 (one source of truth: the weight); weight edits debounce ~150ms then re-derive; contribution % = weight ÷ Σ selected weights, warning color above ~40%.
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

**F10-S1 [BE] Bucket assignment + stats.** Precise. Buckets on premium with configurable half-width; per bucket over **selected** comps: count · leased-only DOM median + min–max → cut-before-lease rate = (leased comps with ≥1 cut) ÷ (leased comps) · censored floors list. Empty bucket → nulls, rendered as dashes; **no interpolation anywhere**.

**F10-S2 [FE] Bucket table.** Dual labels: stable % definition + live dollar boundary derived from anchor (re-renders with drift/weights); counts click through; map pins recolor by bucket in Analysis view.

**F10-S3 [BE/FE] Mini-KM per bucket.** Reuses F11-S2's estimator over each bucket's comps; sparkline render.

---

## F11 · Price Test

**F11-S1 [BE] kNN retrieval.** Precise. Distance = |candidatePremium − compPremium| over selected comps; take k=7 nearest; user weights carried into aggregation (not into distance).
**AC:** ties broken deterministically (then by distance-to-subject); neighbors returned with their premium distances for display and for the guard.

**F11-S2 [BE] Weighted Kaplan-Meier estimator.** Very precise — the statistical core. Product-limit estimator with weighted risk sets: at each event time t (leased neighbor's effectiveDOM), `S(t) = S(t−) × (1 − d_w(t)/n_w(t))` where `d_w` = summed weights of comps leasing at t and `n_w` = summed weights still at risk (not yet leased, not yet censored) at t. Censored comps leave the risk set at their floor DOM without contributing an event. Day-1 events and ties require no special handling beyond correct risk-set accounting.
**AC:** verified against a reference implementation (e.g., Python `lifelines` with weights) on ≥5 fixtures including all-censored-after-first-event and heavy-tie cases; monotone non-increasing; S(0)=1.

**F11-S3 [BE] Insufficient-evidence guard.** Precise — hard requirement, runs **before** any curve renders. Usable neighbor = selected, non-excluded; guard trips if < 3 usable neighbors within ±3 premium points of candidate, or all k are censored.
**AC:** the observed prototype failure is the regression test: with evidence clustered at −2%…+4% and one comp at +10%, a +16.5% candidate must render the guard state (with nearest comps + their distances), never a curve. Guard state and curve state are mutually exclusive renders.

**F11-S4 [BE] Expected vacancy + cost.** Precise. Expected vacant days = area under the KM step function truncated at the last observable time (min of last event time and largest censoring floor — state the truncation in the UI: "expected vacancy ≥/∈ N days (truncated at X)"); cost = days × candidateRent/30. Rendered as a band across the drift sensitivity range (recompute at d−2/d/d+2).

**F11-S5 [FE] Price test UI.** Broad. Rent input (slider + typed field), premium/bucket readout, KM curve with horizon markers (14/30/45/60), neighbor cards listed individually below (address, premium + distance, outcome/floor, cuts, weight, cohort year), guard state rendering.

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

**T-S3 [TEST] Live smoke run.** F4-S7 harness against the real subject address on first API use; outputs the §3.4 verification answers into the repo as a decision record.
