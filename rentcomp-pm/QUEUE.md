# Story Queue — live state

**API call ledger (50/month hard cap):** used **8** · remaining **42** · gate spent 8 of its ≤10 reserve · reserved for owner's real pulls ~40. No live call without a ledger entry (WORKFLOW.md §6). Detail ledger: `fixtures/live-samples/ledger.json`.

**Spent 2026-07-26 (owner-authorized):** gate run, subject `3651 S Wood St, Chicago, IL 60609` (4bd / ba `1.5:2` per owner call, 1.0mi, window 07-28→08-20, 3yr) = 6 calls, two-phase as planned. All HTTP 200, syntax verified working (`X-Total-Count` echoed). Result: **1 comp total → NO-GO**. Queue halted pending owner redesign decision.

**Planned spend (owner-authorized 2026-07-26, round 2):** broad diagnostic pull = **2 calls** (Active + Inactive): `daysOld=1:1095`, radius 2.0mi, `bedrooms=3:4` (owner-approved comp-pool expansion), propertyType `Multi-Family|Apartment|Townhouse|Single Family|Condo`, NO bathrooms filter, limit 500. Seasonal windowing applied locally to returned data; verdict on in-window distinct comps.

**Deadline: 7/29/2026** — the tool must be able to price a real unit by this date. Working beats pretty; see `PROJECT_MANAGER.md`'s "Deadline awareness."

Owned by the project manager. Every dispatch/completion/reorder is an edit here, committed to main. States: `BLOCKED · READY · DISPATCHED · IN_DEV · IN_REVIEW · REGRESSION · DONE`.

**Global rule:** everything is blocked by `T-S3` (the go/no-go gate) until it passes. `F4-S7` (harness) is built *as part of* the gate. `T-S4` (Playwright specs) is not a queue item — it rides inside every story's QA work. `T-S1` (golden files) rides with `F4-S3`.

**Lanes** (parallelism guide — dispatch parallel stories only across different lanes):
`PIPE` = data pipeline & stats · `UI` = screens/components · `INFRA` = persistence, API client, forms · `TEST` = standalone test items

## Queue (initial ordering)

| # | Story | Lane | Blocked by | State | Notes |
|---|---|---|---|---|---|
| 0 | T-S3 + F4-S7 — go/no-go gate + harness | TEST | — | **DONE** | GO 2026-07-26: 337 in-window distinct comps (≥15). 8 calls spent. Decision record + f4-s7-first-pull-analysis.md on main |
| 1a | F0-S1a backend scaffold (uv, FastAPI, entry point) | INFRA | T-S3 | **DONE** | merged 2d000b0; app = rentcomp.app:app; 71/71 green on main |
| 1b | F0-S1b frontend scaffold (Vite, Tailwind, codegen) | UI | F0-S1a | READY | needs FastAPI schema for codegen. ⚠ create_app() mounts static at `/` LAST — any router added after the mount 404s when a UI build exists; F0-S1b QA must add a built-UI + real-API-route test |
| 2 | F0-S3 weighted stats | PIPE | T-S3 | **IN_DEV** | QA tests red on story/F0-S3-qa @ 92da7a0 (31, mutation-validated); dev implementing against installed scaffold |
| 3 | F0-S4 RentCast client | INFRA | T-S3 | READY | encodes range-syntax answer (gate.py's rentcast_range/rentcast_multi); **F4-S7 evidence adds scope: read X-Total-Count, page via offset until complete or manifest the gap (D24/F4-S6) — Inactive fixture is a 500/690 sample** |
| 4 | F0-S5 config store | INFRA | F0-S1 | BLOCKED | |
| 5 | F0-S2 derivation graph | PIPE | F0-S3, F0-S5 | BLOCKED | deepest dependency in the repo; **CHECKPOINT: ADR + owner sign-off before implementation**; ⚠ register /api/derive router BEFORE create_app()'s static mount (see F0-S1b note) |
| 6 | **WS-1 walking skeleton** | PIPE | F0-S2, F0-S4 | BLOCKED | vertical slice: minimal pull→stitch→list→anchor→bucket→price test, zero styling; milestone — can price a unit if time runs out; **CHECKPOINT: architecture review after QA pass, before parallel dispatch opens** |
| 7 | F4-S1 query planner | PIPE | WS-1 | BLOCKED | formalize + AC tests |
| 8 | F4-S2 dedupe + spells | PIPE | F4-S1 | BLOCKED | |
| 9 | F4-S3 stitcher (+ T-S1 golden files) | PIPE | F4-S2 | BLOCKED | 42d strict `<`; **F4-S7 evidence adds AC: merge contiguous history events (gap ≤1d = price change, not re-list; 23 real records) + synthesized single-spell fallback for no-history records (44 real, all have listedDate). T-S1: include 2453 W 46th Pl churn record + a price-change chain** |
| 10 | F4-S8 removal classification + withdrawal-suspect | PIPE | F4-S3 | BLOCKED | |
| 11 | F4-S4 window + cohort | PIPE | F4-S3 | BLOCKED | |
| 12 | F4-S5 premium computation | PIPE | F4-S4 | BLOCKED | |
| 13 | F3-S1 cache key + raw store | INFRA | F0-S4 | BLOCKED | parallel w/ F4 chain |
| 13a | **F3-S4 durable cache + resumable pulls** | INFRA | F3-S1 | BLOCKED | D24 — protects the 50-call budget; wanted early, before any story spends calls |
| 14 | F5-S2 selection & weight state | UI | F0-S2 | BLOCKED | parallel w/ F4 chain |
| 15 | F8-S1 anchor computation | PIPE | F4-S5 | BLOCKED | |
| 16 | F5-S1 comp row (incl. $/sqft verify flag) | UI | F4-S8, F4-S5, F5-S2 | BLOCKED | |
| 17 | F8-S2 drift slider + band | UI | F8-S1 | BLOCKED | |
| 18 | F8-S3 thin-cohort warnings | UI | F8-S1 | BLOCKED | |
| 19 | F6-S1 Leaflet map | UI | F0-S1, F4-S5 | BLOCKED | |
| 20 | F7-S1 filter engine | UI | F5-S2 | BLOCKED | |
| 21 | F6-S2 pin tooltip | UI | F6-S1, F5-S2 | BLOCKED | |
| 22 | F6-S3 hover sync | UI | F6-S2 | BLOCKED | |
| 23 | F7-S2 filter strip | UI | F7-S1 | BLOCKED | |
| 24 | F9-S1 breakdown panel | UI | F7-S1 | BLOCKED | |
| 25 | F5-S3 analysis gate | UI | F5-S2 | BLOCKED | |
| 26 | F10-S1 bucket stats | PIPE | F8-S1, F4-S8 | BLOCKED | |
| 27 | F10-S2 bucket table | UI | F10-S1 | BLOCKED | |
| 28 | F11-S1 kNN retrieval | PIPE | F4-S5 | BLOCKED | |
| 29 | F11-S2 weighted KM | PIPE | F0-S3 | BLOCKED | verify vs lifelines |
| 30 | F11-S3 insufficient-evidence guard | PIPE | F11-S1 | BLOCKED | hard requirement; ships before/with any curve UI |
| 31 | F11-S4 expected vacancy + cost | PIPE | F11-S2 | BLOCKED | |
| 32 | F11-S5 price test UI | UI | F11-S3, F11-S4, F10-S2 | BLOCKED | |
| 33 | F11-S6 decision log | INFRA | F11-S5 | BLOCKED | |
| 34 | F2-S2 range parser | INFRA | T-S3 (syntax answer) | READY | syntax answered: colon ranges, `*` open bounds, `\|` multi-values; single-value daysOld = range max (see gate.py builders + docs/rentcast-schema/) |
| 35 | F2-S1 search form + F2-S3 estimator | INFRA | F2-S2, F0-S5 | BLOCKED | |
| 36 | F3-S2 cache modal + F3-S3 atomicity | INFRA | F3-S1, F2-S3 | BLOCKED | |
| 37 | F1-S2 workspace persistence | INFRA | F3-S1 | BLOCKED | |
| 38 | F1-S1 home view | UI | F1-S2 | BLOCKED | |
| 39 | F12-S1 link builders + F12-S2 placement | UI | F5-S1, F6-S2 | BLOCKED | |
| 40 | F13-S1 refresh reconciliation + F13-S2 diff | INFRA | F3-S3, F4-S8 | BLOCKED | |
| 41 | F14-S1 tab state + F14-S2 autosave + F14-S3 scroll | UI | F11-S5, F1-S2 | BLOCKED | 744px regression spec |
| 42 | T-S2 invariant suite | TEST | F11-S5, F7-S1, F3-S2 | BLOCKED | |
| 43 | **FINAL — full regression pass** | TEST | everything above | BLOCKED | QA solo, fresh branch off main, all F1–F14 specs green = MVP done |

## Log

| Date | Story | Event |
|---|---|---|
| — | — | Queue initialized; awaiting gate (#0) |
| 2026-07-26 | setup | RentCast MCP verified; schema snapshots committed to docs/rentcast-schema/ (schema-read only, 0 ledger spend); QA subagent confirmed unable to call execute-request |
| 2026-07-26 | T-S3 | Owner authorized gate: subject 3651 S Wood St Chicago 60609, 4bd/1.5ba, 1.0mi, window 07-28→08-20, 3yr — 6 calls planned, two-phase (verify windowing after call #1) |
| 2026-07-26 | T-S3 | Pre-spend finding: gate.py sends nonexistent daysOldMin/daysOldMax params and comma-joined propertyType; RentCast syntax is `daysOld=min:max` (colon), `\|`-separated multi-values (developers.rentcast.io/reference/search-queries). Fix required before live run — this IS the range-syntax answer F0-S4/F2-S2 inherit |
| 2026-07-26 | T-S3 | DISPATCHED — QA spawned first per protocol (tests on story/T-S3-qa before any code) |
| 2026-07-26 | T-S3 | QA handoff accepted: 18 tests on story/T-S3-qa @ f0c3fe7 (9 red = defects, 9 green = D24 pins); plan table complete, all L1, no-Playwright deviation approved. PM accepted QA judgment: ±90 boundaries inclusive; missing listedDate ⇒ verdict not ok. → IN_DEV |
| 2026-07-26 | T-S3 | Dev handoff: story/T-S3 @ 6d1f31b+af09e4a — colon/pipe syntax via rentcast_range/rentcast_multi, --verify-window offline mode, X-Total-Count in ledger. 29/29 pytest green, 0 live calls. [DEFAULT]s logged. ⚠ operational: all run phases must share one calendar day (cache sig embeds today). → IN_REVIEW |
| 2026-07-26 | T-S3 | QA PASS report accepted (29-row plan table verified, both PM checks done). Merge sequence executed: qa→dev→main (ba13917), branches deleted, suite green on main. Live run pending owner's bathrooms-param call. → REGRESSION |
| 2026-07-26 | T-S3 | Owner set bathrooms to range `1.5:2` (AskUserQuestion). Live run executed two-phase: call #1 (2026 Active) → 0 records, HTTP 200, X-Total-Count 0 (filters applied server-side, not flood) → --verify-window OK → remaining 5 spent. 6/6 HTTP 200 |
| 2026-07-26 | T-S3 | **NO-GO**: 1 comp in 3 years of windows (3630 S Hermitage Unit 2R, 4bd/2ba Apt, $2800, DOM 31 — found only because ba was a range; exact 1.5 would have returned zero). Server total_counts confirm sparsity is real, not a syntax artifact. Fixtures + decision record committed. Queue halted; escalated to owner with redesign options (radius / propertyType incl. Single Family / drop ba filter / broad-pull-then-window-locally) |
| 2026-07-26 | T-S3 | **OWNER DECISION (round 2):** option 1 broad pull, AND bedrooms expanded to `3:4` — owner explicitly approved 3-bedroom comps in the pool ("yes we can do 3 or 4 bedrooms for sure ... expand the comp pool"). Rationale: 1-comp NO-GO shows filtered pool too thin. 2 calls authorized. Harness change dispatched QA-first, same story |
| 2026-07-26 | T-S3 | Round-2 QA handoff accepted: 7 red tests on fresh story/T-S3-qa @ 1d49658 (test_gate_broad.py), 29 round-1 tests green. PM rulings: --verify-window + --days-old combination FORBIDDEN (explicit error); missing listedDate = out-of-window in broad verdict (accepted); no 2-call hard-cap test (only 2 signatures exist; PM runs --max-calls 2). → IN_DEV |
| 2026-07-26 | T-S3 | Round-2 dev handoff: story/T-S3 @ 6b1f41e — broad mode, optional bathrooms, verify+days-old forbidden (exit 2), bare colon-less --days-old refused (dev guard, round-1 defect class), round-1 fixture signatures verified preserved. 44/44 green, 0 live calls. → IN_REVIEW |
| 2026-07-26 | T-S3 | Round-2 QA PASS accepted (13-row plan verified, both PM checks done). Merge sequence: qa→dev→main (cff7b98), branches deleted, 44/44 green on main |
| 2026-07-26 | T-S3 | **LIVE RUN ROUND 2 → GO.** 2 calls spent (ledger 8/50). 39 Active (complete) + 500 Inactive (of 690 total per X-Total-Count — truncated, 190 unfetched). 539 raw distinct; **337 in-window distinct** (2026: 70, 2025: 155, 2024: 112) vs ≥15 threshold. Decision record + fixtures committed. Note: --max-calls semantics are monthly-total not per-run (docstring says per-run) — logged as cleanup candidate, zero calls wasted. Gate PASSED; F4-S7 §3.4 analysis dispatched; foundations unblock at story DONE |
| 2026-07-26 | T-S3 | **DONE.** F4-S7 analysis on main @ 7380d6f: history preserves spells (73/539 multi-event, 50 true re-lists) but 23 contiguous price-change chains must merge (else fabricated re-lists); 44 no-history records need single-spell fallback; Multi-Family type nearly empty (3) — Apartment dominant (226), keep 5-type default; sqft missing 14.7%, yearBuilt 47.1%; pagination = X-Total-Count + offset. Evidence folded into F0-S4/F4-S3 queue notes. Owner decisions open: 1 call to un-truncate Inactive (190 records); two $/sqft outliers support F5-S1 verify-flag |
| 2026-07-26 | F0-S1a, F0-S3 | Queue un-halted. Both DISPATCHED, QA-first, parallel per lane rule (INFRA + PIPE, low file overlap: scaffold vs pure stats). F0-S4 and F2-S2 flipped READY |
| 2026-07-26 | F0-S1a | QA handoff accepted: 16-row structural plan on story/F0-S1a-qa @ 329cea7, red legibly (17F/4E, zero collection errors), layout pinned to ARCH §2. Open dev-contract item: app object location (QA resolver accepts 4 candidates; dev picks one, QA tightens). Port-8000 + static-serve assertions correctly deferred (Playwright webServer / F0-S1b) |
| 2026-07-26 | PM | **INCIDENT + RULING:** parallel subagents raced on the shared git worktree (F0-S1a QA commit briefly landed on story/F0-S3-qa; repaired, nothing lost). Ruling: git-touching subagent phases are SERIALIZED from now on — one agent owns the tree at a time. Consequence: F0-S1a dev dispatches only after F0-S3 QA returns; F0-S3 dev phase additionally waits for F0-S1a merge (stats needs the installed scaffold anyway) |
| 2026-07-26 | F0-S3 | QA handoff accepted: 31 tests (mutation-validated: suite proven to catch upper-median and weight-0-inclusion bugs) on story/F0-S3-qa @ 92da7a0, red legibly (1F import + 30 skip-until-import). Import target rentcomp.stats.weighted per ARCH §2. **PM contract rulings:** ValueError for negative weights / q∉[0,1] / length mismatch CONFIRMED (caller bugs must not launder into None="no evidence"); AC1 even-n reads as median_low — the locked lower-median [INVARIANT] wins over interpolating "plain median", not a semantic conflict; NaN/inf contract deliberately left to domain-layer guarantees |
| 2026-07-26 | PM | **INCIDENT #2 + REPAIR:** race also hit F0-S3 QA — its commit 92da7a0 landed directly on main (merge-protocol violation, main suite red). Repaired by rebasing main to excise it (nothing pushed; 3a72a86 → 31ace7c); story/F0-S3-qa keeps 92da7a0 as its own tip, parent d3e46ad. Main verified green (44 gate tests). Serialization ruling stands — F0-S1a dev now dispatched as sole tree owner |
| 2026-07-26 | F0-S1a | Dev handoff: story/F0-S1a @ 7a41071 — §2 layout, rentcomp.app:app ([DEFAULT]: assembly above routers, no import side effects), console script + __main__ share main(), UI mount can't shadow /openapi.json, 5 pinned deps exactly per §1a budget (numpy declared here for F0-S3), pytest.ini collects both suites. 71 passed claimed (44+21+6). Notes: starlette TestClient deprecation warning (queue-note candidate); requirements.txt pins direct deps only. → IN_REVIEW |
| 2026-07-26 | F0-S1a | **DONE.** QA PASS (21-row plan, resolver tightened to rentcomp.app:app @ 2d000b0, zero conflicts on sync, 71/71 rerun post-sync). Merge sequence executed, branches deleted, 71/71 green on main. QA finding carried forward: static mount at `/` swallows later-registered routes — noted on F0-S1b + F0-S2 rows. F0-S1b flipped READY |
| 2026-07-26 | F0-S3 | Dev dispatched against installed scaffold (sole tree owner per serialization ruling). → IN_DEV |
