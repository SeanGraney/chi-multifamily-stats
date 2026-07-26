# RentComp — Epics (MVP)

Reverse-engineered from the working prototype + functional spec. One primary persona: **a small-multifamily landlord pricing a vacant unit in a low-listing-volume micro-market.** Their question is always the same: *"If I ask $X, how long does this sit?"*

**The core loop** (everything below serves it):

```
Define unit & pull params → Pull + auto-clean comps → Curate & weight evidence
→ Set anchor (drift) → Read buckets → Test candidate prices → Decide ask
   …weeks later: re-open cached search → refresh → re-test
```

**Screen graph:**

```
HOME ──(new search)──► NEW SEARCH FORM ──(submit)──► [CACHE MODAL?] ──► RESULTS (COMPS)
  │                                                                        │  ▲
  └──(recent row)──► [CACHE MODAL?] ──────────────────────────────────────►│  │
                                                            (ANALYSIS tab) ▼  │ (COMPS tab)
                                                                        ANALYSIS
```

---

## F1 · Home / Recent Searches

**Story:** As a returning user, I want to jump back into a prior unit's workspace without re-entering anything or spending API calls.

**Entry:** App launch.

**Flow:**

1. User lands on Home → system shows NEW SEARCH button + recent searches table (address, specs, radius, anchor, age), newest first.
2. User clicks a recent row → system loads the cached workspace: raw pull, selections, weights, drift setting, anchor — restored exactly as last left.
3. User lands on Results (COMPS) with all prior state intact.

**Success:** Workspace restored in <1s, zero API calls.

**Edges:** No recents → table hidden, NEW SEARCH is the only path. Cache aged (e.g., >7d) → route through Cache Modal (F3) instead of loading silently. Corrupt/missing cache entry → row shows error state, offers refresh (full re-pull).

---

## F2 · New Search (define subject + pull)

**Story:** As a landlord, I want to define my unit and how wide to cast the comp net, so the pull matches my micro-market.

**Entry:** NEW SEARCH from Home.

**Flow:**

1. System opens the search form.
2. User enters: address · beds (single or min–max) · baths (single or min–max) · unit sqft · radius (mi) · date window (start/end month-day) · years back (1–5, default 2). Property types default Multi-Family + Apartment + Townhouse, editable.
3. System previews the cohort plan live: *"Jun 15–30 · 2026, 2025 (2 cohorts) · est. N API calls."*
4. User submits → if a cache exists for identical params → F3; else system runs the pull (F4).

**Success:** Valid params dispatched; user understands cost before spending calls.

**Edges:** Address fails geocoding → inline error, block submit. Sqft empty → block (anchor math requires it). Beds/baths min>max → inline swap-or-error. Absurd radius/window → soft warning, allow.

---

## F3 · Cache Decision Modal

**Story:** As a user on a metered API plan, I want an explicit choice before spending calls when equivalent data already exists.

**Entry:** Submitting a search that matches a cache key; or opening a stale recent; or clicking REFRESH (F13).

**Flow:**

1. System shows: cache date + **USE CACHED (free)** vs **REFRESH (~N API calls)** with the real call count, and cancel.
2. USE CACHED → workspace loads from disk. REFRESH → F4 with fresh calls; on success, cache and selections-by-address are re-applied where comps still match.

**Success:** No API call ever happens without the user having chosen it.

**Edges:** Refresh fails mid-pull → keep old cache untouched, show error; never mix stale and fresh data in one workspace.

---

## F4 · Pull & Clean (system flow, surfaced as loading → evidence)

**Story:** As a user, I want the raw listings laundered — re-lists stitched, initial prices recovered, actives flagged — before I ever see them, so my curation starts from honest records.

**Entry:** Search submit or refresh.

**Flow (system):**

1. For each cohort year: padded `daysOld`-range queries (Active + Inactive), paginated.
2. Progress indicator: per-year pull → stitching → deriving.
3. Pipeline: dedupe → group spells by address+unit → stitch gaps < 42 days (6 weeks) → derive initial ask, effective DOM, censored flag, cut history, re-list count → classify recent removals (pending: <7d since removal, excluded from stats · provisional: ≥7d, counted with a marker · confirmed: ≥6 weeks with no re-list) → flag withdrawal-suspects (a complete spell that re-lists 6 weeks–6 months later — display-only, lease uncertain, not auto-excluded) → window-filter on stitched start → cohort assignment → within-cohort premiums.
4. System lands user on Results with all comps **included by default at weight 1** (missing-sqft comps default-excluded, flagged).

**Success:** Comp list where every row's DOM and initial ask are trustworthy; stitched records visibly badged.

**Edges:** 0 comps → empty state naming the binding constraint ("0 in radius; nearest match at 1.3mi") + widen shortcuts. Cohort < min size → warning registered for F9. API error/rate limit → plain error, cache preserved. A removal <7 days old is not yet counted as leased or vacant ("removed 4d ago — classifying") — this is expected pending-state behavior, not an error.

---

## F5 · Comp Curation — List

**Story:** As the person who knows this micro-market, I want to hand-pick and weight the evidence, because comp quality judgment is mine, not the algorithm's.

**Entry:** Results (COMPS), default view.

**Flow:**

1. User scans rows: address · status · specs · distance · cohort year · outcome (`leased 34d` / `active 47+ d`) · cut history (`✂ 2150→2050 (day 21)`) · re-list badge (`⟲ ×1, 6d gap`) · initial ask · premium vs cohort.
2. User excludes a bad comp (toggle) → system recomputes anchor, premiums-display, breakdown, buckets instantly.
3. User raises/lowers a weight (default 1; 0 ≡ excluded) → same instant recompute; **contribution %** on every row updates so dominance is visible.
4. User expands a row → full detail + Zillow/Street View links (F12) → adjusts weight based on what the photos show.
5. Iterate until the included set feels like the market.

**Success:** A curated, weighted evidence set; every recompute <100ms; no hidden state.

**Edges:** Below 5 included comps → ANALYSIS button disables with reason. Missing-sqft row → excluded by default, `no sqft` badge, manual re-include allowed. One comp >~40% contribution → its contribution % renders in warning color (no hard cap by design).

---

## F6 · Comp Curation — Map

**Story:** As someone with street-level knowledge, I want to judge comps by *where they are*, because two blocks can change the market.

**Entry:** Map occupies top of Results, always visible with the list.

**Flow:**

1. User orients: amber = subject; green = included; grey = excluded; rust = filtered-out (still visible — the map never hides evidence); pulsing ring = still-active (censored).
2. User clicks a pin → tooltip card: address, ask, premium, DOM/floor, cut/re-list badges, INCLUDE/EXCLUDE, Zillow + Street View icons; corresponding row highlights and scrolls into view.
3. User excludes/includes directly from the tooltip → same instant recompute as F5.
4. Row hover ↔ pin highlight (two-way sync).

**Success:** Selection decisions can be made entirely from the map; map and list never disagree.

**Edges:** Rust pin click → tooltip offers INCLUDE override (un-filters it). Overlapping pins → spiderfy/offset on click.

---

## F7 · Client-side Filters

**Story:** As a user with a noisy pull, I want to thin the list without deleting anything, so I can focus without losing evidence.

**Entry:** Filter strip between map and list.

**Flow:**

1. User sets max distance / hide censored / leased-only → matching comps leave the list, turn rust on the map, and exit all calculations.
2. Collapsed footer appears: "N filtered · show" → expand shows dimmed rows with per-row INCLUDE override.
3. ALL / NONE bulk-set inclusion for currently visible comps.
4. Reset clears filters; manual overrides survive.

**Success:** Filtering is always reversible and always visible — counts in the breakdown (F9) reconcile: included + excluded + filtered = pulled.

---

## F8 · Anchor & Drift

**Story:** As the decision-maker, I want today's market rent derived from *my* weighted comps — with the drift assumption exposed — because every downstream number hangs off it and drift error passes 1:1 into my premium read.

**Entry:** Top of right rail; always live.

**Flow:**

1. System displays anchor = weighted median of drift-adjusted comp $/sqft × subject sqft.
2. Sensitivity line always visible: *"assumes 7%/yr drift · at 5% → $2,290 · at 9% → $2,377."*
3. User drags the drift slider (manual source, MVP) → anchor, bucket dollar boundaries, and price-test premium all re-derive live.
4. User sanity-checks: current-cohort comps near the projected anchor = confirmation; far = reconsider drift.

**Success:** User can state what the anchor assumes and what it costs to be wrong.

**Edges:** Current cohort thin → rail warning: "2026: 3 comps — thin, anchor leans on drift." All included comps in one old cohort → widened band + warning.

---

## F9 · Comp Breakdown & Warnings

**Story:** As a user, I want a running reconciliation of my evidence so aggregates never silently rest on less data than I think.

**Flow:** Rail shows included / censored / excluded / filtered counts + per-cohort counts; thin-cohort warnings inline; every count is a click-through to its comps. Updates on every action in F5–F7.

---

## F10 · Bucket Overview

**Story:** As a landlord, I want to see what happened to units priced below / at / above market, so I can locate the knee — where DOM stops being weeks and becomes months.

**Entry:** ANALYSIS tab, below the anchor strip.

**Flow:**

1. System renders three buckets with dual labels — stable % definition + live dollar boundary (`AT MARKET ±4% · $2,240–$2,427`).
2. Per bucket: comp count · leased-DOM median + range · **cut-before-lease rate** (the knee detector) · censored floors ("2 active at 45+, 60+") · mini-KM curve.
3. User reads the knee (e.g., above-market: 5 of 7 cut before leasing) → clicks any count → underlying comps.

**Success:** The below/at/above story is readable in ~10 seconds and every number is one click from its evidence.

**Edges:** Empty bucket → dashes, never interpolation. Dollar boundaries visibly re-derive when drift/weights change.

---

## F11 · Price Test (the payoff)

**Story:** As a landlord about to list, I want to type a rent and see what actually happened to the comps that tried that premium — as a vacancy-probability curve, not a fake-precision point.

**Entry:** ANALYSIS, below buckets.

**Flow:**

1. User sets candidate rent (slider/input) → system shows premium vs anchor + bucket call (`$2,300 · −1.5% · AT MARKET`).
2. **Guard first:** system retrieves k≈7 nearest comps by |premium distance|, weighted.
   - **Insufficient path:** <3 usable neighbors within ~±3pts → **"INSUFFICIENT EVIDENCE AT THIS PREMIUM"** + nearest comps with their distances ("nearest evidence: +4.0%, 12.5pts below your ask"). No curve. *(Observed failure this guard exists to kill: prototype rendered "40 days" at +16.5% from mid-market comps.)*
   - **Sufficient path:** weighted Kaplan-Meier over neighbors (censored consumed properly) → "% still vacant at day t" curve + horizon readouts (14/30/45/60d) + expected vacancy days → vacancy cost ≈ days × rent/30.
3. Below the curve, always: the k neighbor cards individually (address, premium, outcome/floor, cuts, weight) — **the neighbors are the answer; the curve is a summary.**
4. User sweeps the slider across prices, watching where the curve degrades and where the guard trips → picks the ask; the bucket + cut-rate story (F10) corroborates.

**Success:** User leaves with an ask and can defend it: "at −2%, comps leased in 2–4 weeks; at +6%, most cut first."

**Edges:** All neighbors censored → insufficient path. Neighbors span cohorts → cohort year visible on each card. Guard threshold configurable, on by default.

---

## F12 · External Verification Links

**Story:** As the curator, I want to see photos and the street before weighting a comp, because RentCast carries no amenities or images.

**Flow:** Zillow icon (`zillow.com/homes/{address-slug}_rb/`) + Street View icon (from lat/lng) on every expanded row and pin tooltip → new tab → user folds what they see (renovation level, curb appeal, floor) into the weight → returns. Dead Zillow link is acceptable (search-page fallback).

---

## F13 · Refresh / Re-pull

**Story:** As a user re-visiting mid-leasing-season, I want fresh listing states without losing my curation.

**Flow:** REFRESH (top bar) → Cache Modal with call count → confirm → re-pull + re-clean → selections/weights re-applied by address match ("2 comps newly leased · 1 new listing · 1 re-list stitched") → new comps arrive included at weight 1, visually marked NEW.

**Edges:** Previously-selected comp vanishes from source → kept, flagged "no longer in source." Failure → old workspace untouched.

---

## F14 · Navigation & State

**Story:** As a user mid-analysis, I want to hop between evidence and conclusions without losing anything.

**Flow:** COMPS ↔ ANALYSIS tabs preserve full state both directions (candidate price survives round-trips; curation changes re-derive analysis on return). Back to Home auto-saves the workspace to cache. All views scroll properly at laptop heights — no content trapped below a fold (Analysis neighbor cards explicitly).

---

## Cross-cutting acceptance criteria

1. **Evidence-first invariant:** every aggregate (anchor, bucket stat, KM point) is ≤1 click from the comps behind it.
2. **Honesty invariant:** censored comps never counted as leased; empty evidence never interpolated; guard before curve, always.
3. **Reactivity invariant:** any curation change (toggle, weight, filter, drift) re-derives anchor → premiums → buckets → price test in one pass, no stale panels.
4. **Cost invariant:** no API call without explicit user consent via the cache modal path.
5. **Reversibility invariant:** exclude/filter/reset never destroys data; only REFRESH (user-confirmed) replaces the raw pull.
