# RentComp — Architecture & Stack

Binding technical decisions. Agents implement to this document; deviations require PM authorization. Where a decision has a non-obvious rationale, it's stated — so nobody re-litigates it mid-story.

**Shape in one line:** a local-only Python (FastAPI) service that owns all data and all math, serving a React SPA that is a pure view layer, with JSON files on disk instead of a database.

---

## 1. Decisions register

| # | Decision | Choice | Rationale |
|---|---|---|---|
| D1 | Deployment target | **Local only.** `localhost`, single user, no hosting | Cache + API key need a persistent local filesystem. *Vercel was considered and rejected: serverless has no local disk, so a deploy would require moving storage to a real DB and secrets to a vault — a V2+ rearchitecture, not a V1 constraint.* |
| D2 | Persistence | **JSON files on disk. No database.** | Data volume is ~100 records per workspace. A DB adds a schema-migration burden for zero benefit at this scale. Revisit only if multi-unit history outgrows the filesystem. |
| D3 | Backend language | **Python 3.12** | Pydantic for the RentCast contract; numpy for stats; `lifelines` available for test-time verification of the KM estimator (F11-S2 AC). |
| D4 | Backend framework | **FastAPI** | Pydantic-native (models *are* the API contract), auto-generates OpenAPI which we codegen into TS types (D12), serves static files for the one-command launch. |
| D5 | Compute location | **All derivation in Python.** Frontend never computes a statistic. | One implementation of every formula, verifiable in pytest. At ~40 comps the math is microseconds; a localhost round-trip is ~2–5ms — far inside the spec's 100ms budget. Kills the two-implementations drift risk. |
| D6 | Frontend | **Vite + React 18 + TypeScript + Tailwind** | Simplest local setup; no SSR machinery; builds to static files FastAPI serves. |
| D7 | Launch | **One command:** `rentcomp` (console script, after `pip install -e .` in the venv) → `localhost:8000` serving API + built UI | Matches a personal tool. Dev mode still runs Vite's hot-reload server separately against the API. |
| D8 | KM estimator | **Hand-rolled weighted product-limit (numpy), ~30 lines.** `lifelines` is a **dev-only** dependency used to verify fixtures. | Keeps pandas/scipy/matplotlib out of the runtime; F11-S2's AC is satisfied by the verification test, not by shipping the library. |
| D9 | Python packaging | **pip + venv**, `pyproject.toml` (setuptools backend) for the installable package + console-script entry point; `requirements.txt` for pinned deps | Standard-library-adjacent tooling, no extra install step for a solo contributor; owner preference over uv. |
| D10 | Map | **Leaflet via `react-leaflet`**, OSM tiles | Already specified; no API key, no quota. |
| D11 | Charts | **Hand-rolled SVG.** No charting library. | The KM curve is a step function with ~10 points needing custom rendering anyway (sensitivity band, horizon markers, censored ticks). Recharts would be ~500KB to draw an SVG path we'd have to override. |
| D12 | Type sharing | **OpenAPI → TypeScript codegen** (`openapi-typescript`), committed, regenerated when models change | Pydantic models are the single source of truth for the contract; TS types can never silently drift from them. |
| D13 | Frontend state | **Plain React state + a custom `useDerive` hook.** No Redux/Zustand/TanStack Query. | Curation state (selections, weights, filters, drift, candidate rent) is client-owned; derived state is whatever `/api/derive` last returned. One `AbortController` per request means the latest response wins during slider drags. |
| D14 | Money & precision | **float64 throughout; round at display only.** No `Decimal`. | 4-digit dollar values; float64 is exact well past our needs and keeps numpy paths clean. Premiums are ratios — never round them internally. |
| D15 | Dates | **Parse to `datetime.date` at the DTO boundary. No timezones anywhere internally.** | RentCast returns midnight-UTC ISO strings; DOM is a whole-day count. Timezone math is a pure source of off-by-one bugs here. |
| D16 | Secrets | API key in `~/.rentcomp/secrets.json` (mode 0600) **or** `RENTCAST_API_KEY` env var. Never in the repo; `.gitignore`d. | |
| D17 | Live-call guard | Live mode requires **both** `RENTCOMP_LIVE=1` **and** a key present. Default is fixture mode. | The 50-calls/month cap (WORKFLOW.md §6) is enforced in code, not by discipline. |
| D18 | Tests | **pytest** (Python unit/property) + **Playwright** (flow regression, TS) | Per stories doc. Playwright drives the one-command server in fixture mode. |
| D19 | kNN implementation | **No library. Two lines of numpy:** `d = np.abs(premiums - candidate); idx = np.argsort(d, kind="stable")[:k]` | The problem is 1-dimensional over ~40 points. scikit-learn's KD/ball-trees solve high-dimensional large-N retrieval — irrelevant here, and ~30MB of dependency to replace `argsort`. It also can't express our semantics: sklearn's `weights` parameter distance-weights a *prediction*, but our weights are **aggregation** weights consumed downstream by Kaplan-Meier. We need retrieval only, then hand the neighbor set to the KM estimator. `kind="stable"` satisfies F11-S1's deterministic-tie AC. |
| D20 | ML frameworks | **None. There is no machine learning in this project.** No scikit-learn, no torch, no pandas at runtime. | Audited: weighted median (sort + cumsum), 1-D kNN (argsort), Kaplan-Meier (product-limit accumulation), drift (arithmetic), buckets (comparisons). All are classical statistics or arithmetic, none are learned models. *The one future exception is the V2 AFT overlay — it would justify `lifelines` as a runtime dependency at that point, and not before.* |
| D21 | Test layering | **Three layers** (§9): pytest unit · pytest API-contract via FastAPI `TestClient` · Playwright flows. Assertions live at the *lowest* layer that can hold them. | D5 moved all math server-side, so most acceptance criteria are now assertable over HTTP in milliseconds. Playwright is reserved for what genuinely needs a browser. |
| D22 | E2E framework | **Playwright** — confirmed over Puppeteer, Cypress, Selenium | Built-in runner + `webServer` config (starts `rentcomp` for us) · `globalSetup` in Node can seed the filesystem fixture home, which Cypress's in-browser architecture makes awkward · trace viewer for debugging agent-reported failures · free parallelization. |
| D23 | Component tests | **Vitest for the `useDerive` hook only.** No React Testing Library suite. | The frontend is a pure view layer (D5) — component tests would mostly assert "given prop, render text," which is maintenance without insight. The one exception is `useDerive`'s debounce/abort/latest-wins logic: real behavior, timing-sensitive, painful in Playwright, trivial in Vitest with fake timers. |
| D24 | Cache durability | **Write-through, per-call, before parsing. A paid-for response is never lost or re-fetched.** Response persistence and pipeline atomicity are separate concerns (§5a). | A 50-call monthly cap means every wasted call is real damage. Naive "atomic batch" would roll back 3 good responses because the 4th 429'd. |

---

### D19a — Feature/target separation (implementation guard)

The kNN here has **exactly one feature and one target**, and conflating them is the most plausible way to silently corrupt the price test:

| | Field | Role |
|---|---|---|
| **Feature (X)** | `premium` — size-normalized distance from that comp's cohort market | The *only* dimension distance is computed in |
| **Target (y)** | `(effective_dom, censored)` — a **pair**, not a scalar | Never enters the distance calculation |

**`effective_dom` must never appear in `distance()`.** Selecting neighbors partly by their outcome is target leakage — it would find comps that both priced like the candidate *and* leased in a similar time, then report that time as a prediction. Circular, and convincing-looking at n≈40.

**Why one feature suffices:** manual curation already spent the other dimensions. Every selected comp is pre-vetted on beds, sqft, location, and condition (Zillow/Street View). Price position is the only remaining axis that predicts vacancy — this is the degenerate case where 1-D kNN is at its strongest, not a simplification of a richer model.

**Why the target is a pair:** a standard kNN regressor would average neighbor DOMs — turning `[12, 34, 47+active]` into "31 days" and silently counting a still-vacant unit as leased. Handing the pair to Kaplan-Meier (D8) is what preserves "47+, still counting" and satisfies the honesty invariant.

---

## 1a. Dependency budget

Small on purpose. Every runtime dependency is one more thing that can break a tool you need working on the evening a unit goes vacant.

**Backend runtime:** `fastapi` · `uvicorn` · `pydantic` (via fastapi) · `httpx` (API client) · `numpy`

**Backend dev-only:** `pytest` · `hypothesis` (property tests the stories doc calls for — stitcher boundaries, DOM monotonicity) · `lifelines` (verifies the KM estimator against a reference implementation, F11-S2 AC)

**Frontend runtime:** `react` · `react-dom` · `leaflet` + `react-leaflet` · `tailwindcss`

**Frontend dev-only:** `vite` · `typescript` · `openapi-typescript` (D12) · `@playwright/test`

Note on numpy: at n≈40 it is a *convenience*, not a performance requirement — pure Python `sorted` + `itertools.accumulate` would compute every statistic we have. It earns its place by making the stats code read like the formulas in the spec and by speaking the same array types as `lifelines` during verification. If it ever becomes a packaging problem, it is removable without redesign.

---

## 2. Repository layout

```
rentcomp/
├── rentcomp-pm/             # PM + agent operating context (this directory)
├── backend/
│   ├── pyproject.toml        # pip-installable package + console-script entry point
│   ├── requirements.txt      # pinned deps
│   └── src/rentcomp/
│       ├── __main__.py       # one-command launch (D7)
│       ├── api/              # FastAPI routers (§4)
│       ├── models/
│       │   ├── dto.py        # Layer 1: raw RentCast shapes
│       │   ├── domain.py     # Layer 2: Spell, StitchedComp
│       │   └── responses.py  # Layer 3: DerivedState & friends
│       ├── pipeline/         # dedupe → spells → stitch → classify → window → cohort → premium
│       ├── stats/            # weighted.py, km.py, knn.py
│       ├── storage/          # cache.py, workspace.py, config.py, decisions.py
│       └── client/           # rentcast.py (fixture | live)
│   └── tests/
├── frontend/
│   ├── package.json          # Vite + React + TS + Tailwind
│   └── src/
│       ├── api/              # generated types (D12) + useDerive hook (D13)
│       ├── components/       # CompRow, Map, BucketTable, KMCurve, ...
│       └── views/            # Home, Results, Analysis
├── fixtures/
│   ├── live-samples/         # committed raw responses from the gate (T-S3)
│   └── synthetic/            # hand-built pathological cases (T-S1)
├── e2e/                      # Playwright specs, one per flow F1–F14
└── .github/workflows/        # optional CI (WORKFLOW.md §5)
```

---

## 3. Model layers (Pydantic)

Three layers, never collapsed — mixing them is how bad API data leaks into math.

**Layer 1 — `dto.py` (wire truth).** Faithful mirror of RentCast responses. *Every field except `id` is Optional* — the API omits fields freely and a validation error here would discard a usable comp. No behavior, no computed properties.

**Layer 2 — `domain.py` (our truth).** `Spell` (listed, removed, price, active) and `StitchedComp` — the atomic comp record carrying `initial_ask`, `effective_dom`, `censored`, `removal_class` (`pending|provisional|leased`), `withdrawal_suspect`, `cut_history`, `relist_count`, `gap_days`, `cohort_year`, `premium`, `psf`, `sqft_suspect`. Fields here are non-Optional where the pipeline guarantees them; the DTO→domain transition is where missing data becomes an explicit flag rather than a `None` that silently propagates.

**Layer 3 — `responses.py` (API contract).** `DerivedState` = anchor + sensitivity band, per-cohort medians and thin-cohort flags, bucket stats, price-test result (guard state *or* curve), breakdown counts. This is what `/api/derive` returns and what codegens into TS.

---

## 4. API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/search` | Params in. Returns `{cache_status: hit|miss|stale, cached_at, estimated_calls, comps?}`. **Never hits the network unless `force_refresh: true`** — that flag is set only by the user's REFRESH click in the cache modal. This is the code-level expression of "use the cache unless the user confirms." |
| `POST /api/derive` | **The heart.** Body = full curation state (comp selections, weights, filters, drift %, candidate rent). Returns the complete `DerivedState`. Stateless and idempotent. |
| `GET /api/workspaces` · `GET/PUT /api/workspaces/{key}` | Recents list; load/save curation state |
| `GET/PUT /api/config` | Knobs from spec §2.3 |
| `POST /api/decisions` · `GET /api/decisions` | Prediction-accountability log (F11-S6) |
**Why one fat `/api/derive`:** F0-S2 requires "one derivation pass, no stale panels." With a single endpoint recomputing everything from scratch on every change, that invariant is structurally true rather than something we have to defend with careful cache invalidation. Partial-update endpoints would reintroduce exactly the staleness the invariant forbids.

---

## 5. Storage layout

```
~/.rentcomp/
├── config.json                    # knobs (spec §2.3)
├── secrets.json                   # API key, 0600, never in git
├── ledger.json                    # API calls used this month (WORKFLOW.md §6)
├── cache/<cache-key>/
│   ├── meta.json                  # params, fetched_at, calls_spent
│   └── raw/y2026-active-000.json  # immutable raw responses, one per call
├── workspaces/<cache-key>.json    # selections, weights, filters, drift, candidate rent
└── decisions/<date>-<slug>.json   # decision log entries
```

### 5a. Cache durability — never pay twice (D24)

Two concerns that must not be conflated:

| Concern | Rule |
|---|---|
| **Response persistence** | Write-through, **per call, immediately, before parsing**. Never rolled back. |
| **Pipeline atomicity** | The user never sees fresh and stale data mixed. Governs what's *displayed*, never what's *kept*. |

**Per-call file granularity.** Each API call writes its own file named by query signature — `y2025-inactive-off000.json` — so a partial set is meaningful and the missing pieces are computable. `meta.json` is the manifest: planned queries, which are satisfied, which failed and why, calls spent.

**Write raw bytes before validating.** Persist the response body to disk, *then* run Pydantic. A validation bug or a schema surprise must never cost an API call — the bytes are already safe and the parse can be re-run for free forever after.

**Atomic writes.** `write .tmp → fsync → rename`. Rename is atomic on POSIX, so a crash mid-write cannot leave corrupt JSON that looks satisfied.

**Resumable pulls.** Every fetch begins by diffing planned queries against the manifest and requesting **only what's missing**. A pull interrupted at 3/4 costs exactly 1 call to finish, whenever it's retried.

**Ledger at call time.** `ledger.json` increments when a request is *sent*, not when a batch completes — the quota is spent either way. Failed calls are recorded separately from successes so retries are informed, not blind.

**Failure semantics:**

- **Incomplete first pull** → usable, with the gap named loudly ("2025 inactive missing — 1 call to complete"). With a 50-call cap, refusing to show anything until the set is perfect would strand you. Nothing here is stale; it's absent, and absence is honestly showable.
- **Partial refresh** → keep serving the previous *complete* set (spec §7: never mix fresh and stale), while **retaining every newly fetched response on disk**. The staging area is not discarded on failure — it's a partial set the retry completes for the cost of the remainder.

---

`cache/` is **immutable raw API responses**; `workspaces/` is **mutable curation state**. The separation is what makes F3-S1 true: pipeline changes re-run for free on cached data, and refresh replaces `cache/` without touching curation. Cache key = SHA256 of canonicalized search params (F3-S1).

---

## 6. Interaction flow (a weight change, end to end)

```
user edits weight
  → React updates local curation state (instant, optimistic UI on the input itself)
  → 150ms debounce
  → POST /api/derive { selections, weights, filters, drift, candidateRent }
     (AbortController cancels any in-flight request)
  → Python: cohort medians → premiums → anchor → buckets → kNN → guard → KM
  → DerivedState replaces frontend derived state
  → anchor, contribution %, buckets, price test all re-render together
```

Everything derived re-renders from one payload, so a partial or stale panel is not representable.

---

## 7. Open items — confirm before the gate

1. **Storage root:** `~/.rentcomp/` (survives repo re-clones, standard for a personal tool) vs `./data/` in-repo (self-contained, gitignored). *Recommend `~/.rentcomp/`.*
2. **Commit `fixtures/live-samples/` to git?** *Recommend yes* — CI and every dev/QA agent needs them, they contain public listing data, and they're the shared substrate the whole build develops against.
3. **Python version floor:** 3.12 assumed. Confirm what's installed on your machine.
4. **Node version:** 20 LTS assumed for Vite 5.

---

## 8. Test strategy — three layers

**Rule: an assertion belongs at the lowest layer that can hold it.** A logic assertion in a browser test is a slow, flaky version of a fast, reliable one.

### Layer 1 — pytest unit (`backend/tests/unit/`) — many, milliseconds

Pure functions: `weighted_median`, KM estimator (vs `lifelines`), kNN retrieval, each pipeline stage (stitcher, classifier, window, cohort, premium), query planner, link builders, cache-key hashing. **`hypothesis` property tests** where the stories call for them — stitcher gap boundaries, DOM monotonicity, KM non-increasing.

### Layer 2 — pytest API contract (`backend/tests/api/`) — the workhorse, milliseconds

FastAPI `TestClient` against a temp `RENTCOMP_HOME` seeded with fixtures. **This is where most story ACs live.** Examples that would otherwise have been browser tests:

- guard trips at +16.5% with evidence clustered at −2%…+4% → assert `price_test.state == "insufficient_evidence"` and the nearest-comp distances
- weight 3 ≡ three weight-1 duplicates → assert identical `anchor`
- drift `d=0` ⇒ anchor equals plain weighted median
- pendings excluded / provisionals marked in bucket stats
- `included + excluded + filtered == pulled`
- `/api/search` never touches the network without `force_refresh: true`
- identical body ⇒ identical `DerivedState` (statelessness)

### Layer 3 — Playwright E2E (`e2e/`) — 14 specs, seconds

Only what needs a real browser and the real wiring:

- the flow itself completes (F1–F14 as user journeys)
- **guard state vs curve renders mutually exclusively** (that the *right thing appears*, not that the guard logic is correct — Layer 2 owns that)
- weight edit → panels visibly update together, no stale panel
- map ↔ list two-way sync; pin tooltip; overlapping-pin reachability
- scroll correctness at **744px** (needs real layout; jsdom cannot)
- cache modal consent path — REFRESH is the only route to a network call
- click-through: every aggregate reaches its comps (evidence-first invariant)

**Fixture seeding:** `globalSetup` points `RENTCOMP_HOME` at a temp dir and copies from `fixtures/`; `webServer` runs `rentcomp` in fixture mode. Zero live API calls (WORKFLOW.md §6), deterministic state per spec.

### Layer 2.5 — Vitest, one file

`useDerive` only: debounce timing, AbortController latest-wins under rapid slider drags, no out-of-order state landing.

### The regression gate

WORKFLOW.md §4's "full suite" = **all layers**: `pytest && npx vitest run && npx playwright test`. Green across all three on a branch synced to main is the merge condition.

---

## 9. Consequences for the backlog

- **F0-S1** becomes two setup stories: backend scaffold (pip, venv, FastAPI, pyproject, one-command entry) and frontend scaffold (Vite, Tailwind, codegen wiring). PM should split it in QUEUE.md.
- **F0-S3 (weighted stats)** and **F11-S2 (KM)** are Python modules with pytest AC — no UI dependency, so they can run in parallel with frontend scaffolding.
- **F0-S2 (derivation graph)** is now concretely "the `/api/derive` pipeline + the `useDerive` hook." Its ADR (architecture checkpoint) covers the `DerivedState` shape — the contract every later story consumes.
- **F0-S4 (RentCast client)** implements D17's two modes; its AC ("cannot reach the network without the flag") is a pytest assertion.
