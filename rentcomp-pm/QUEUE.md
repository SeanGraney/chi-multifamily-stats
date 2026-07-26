# Story Queue — live state

**API call ledger (50/month hard cap):** used 0 · remaining 50 · reserved for gate ≤10 · reserved for owner's real pulls ~40. No live call without a ledger entry (WORKFLOW.md §6).

**Planned spend (owner-authorized 2026-07-26):** gate run for subject `3651 S Wood St, Chicago, IL 60609` (4bd / 1.5ba, 1.0mi radius, window 07-28→08-20, 3 years back) = 3 windows × 2 statuses = **6 calls**. Two-phase execution: 1 call → verify returned `listedDate`s fall inside the requested window → remaining 5 only if windowing holds. Worst case if syntax is wrong: 1 call burned, 5 preserved.

**Deadline: 7/29/2026** — the tool must be able to price a real unit by this date. Working beats pretty; see `PROJECT_MANAGER.md`'s "Deadline awareness."

Owned by the project manager. Every dispatch/completion/reorder is an edit here, committed to main. States: `BLOCKED · READY · DISPATCHED · IN_DEV · IN_REVIEW · REGRESSION · DONE`.

**Global rule:** everything is blocked by `T-S3` (the go/no-go gate) until it passes. `F4-S7` (harness) is built *as part of* the gate. `T-S4` (Playwright specs) is not a queue item — it rides inside every story's QA work. `T-S1` (golden files) rides with `F4-S3`.

**Lanes** (parallelism guide — dispatch parallel stories only across different lanes):
`PIPE` = data pipeline & stats · `UI` = screens/components · `INFRA` = persistence, API client, forms · `TEST` = standalone test items

## Queue (initial ordering)

| # | Story | Lane | Blocked by | State | Notes |
|---|---|---|---|---|---|
| 0 | T-S3 + F4-S7 — go/no-go gate + harness | TEST | — | **DISPATCHED** | 6 live calls planned (see ledger); needs owner's API key; failure ⇒ halt & escalate |
| 1a | F0-S1a backend scaffold (uv, FastAPI, entry point) | INFRA | T-S3 | BLOCKED | |
| 1b | F0-S1b frontend scaffold (Vite, Tailwind, codegen) | UI | F0-S1a | BLOCKED | needs FastAPI schema for codegen |
| 2 | F0-S3 weighted stats | PIPE | T-S3 | BLOCKED | pure Python — parallel w/ #1a and #1b |
| 3 | F0-S4 RentCast client | INFRA | T-S3 | BLOCKED | encodes range-syntax answer from gate |
| 4 | F0-S5 config store | INFRA | F0-S1 | BLOCKED | |
| 5 | F0-S2 derivation graph | PIPE | F0-S3, F0-S5 | BLOCKED | deepest dependency in the repo; **CHECKPOINT: ADR + owner sign-off before implementation** |
| 6 | **WS-1 walking skeleton** | PIPE | F0-S2, F0-S4 | BLOCKED | vertical slice: minimal pull→stitch→list→anchor→bucket→price test, zero styling; milestone — can price a unit if time runs out; **CHECKPOINT: architecture review after QA pass, before parallel dispatch opens** |
| 7 | F4-S1 query planner | PIPE | WS-1 | BLOCKED | formalize + AC tests |
| 8 | F4-S2 dedupe + spells | PIPE | F4-S1 | BLOCKED | |
| 9 | F4-S3 stitcher (+ T-S1 golden files) | PIPE | F4-S2 | BLOCKED | 42d strict `<` |
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
| 34 | F2-S2 range parser | INFRA | T-S3 (syntax answer) | BLOCKED | |
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
