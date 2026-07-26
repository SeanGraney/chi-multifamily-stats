# RentComp — chi-multifamily-stats

Local-only Python (FastAPI) service that owns all data and math, serving a
React SPA that is a pure view layer. JSON files on disk instead of a
database. This file distills the project's architecture decisions register
(D1–D24) into what Claude should hold in working memory on every turn — the
`backend/`, `frontend/`, and `rentcomp-pm/` layout below is the target
structure, not yet built.

## Where to find things

- Full spec, epics, stories, skill review: `rentcomp-pm/docs/`
- Architecture decisions D1–D24 (binding): `rentcomp-pm/ARCHITECTURE.md`
- Git/QA/regression workflow: `rentcomp-pm/WORKFLOW.md`
- Agent roles: `rentcomp-pm/AGENT_DEVELOPER.md`, `rentcomp-pm/AGENT_QA.md`
- Live story queue: `rentcomp-pm/QUEUE.md`

Google Drive is a historical snapshot only — never a source of truth. Read docs from the repo.

## Stack

- **Backend:** Python 3.12, FastAPI, Pydantic, numpy, pip and venv for packaging/venv
- **Frontend:** Vite + React 18 + TypeScript + Tailwind
- **Launch:** TODO (API + built UI, one command)
- **Types:** OpenAPI → TypeScript codegen (`openapi-typescript`), committed — never hand-edit generated types
- **Storage:** `~/.rentcomp/` (config, secrets, cache, workspaces, decisions) — no database

## Hard rules (violating these breaks the architecture, not just style)

- **All derivation happens in Python.** The frontend never computes a statistic — it renders what `/api/derive` returns.
- **No ML frameworks.** No scikit-learn, no torch, no pandas at runtime. Everything here is classical stats/arithmetic (weighted median, 1-D kNN via `argsort`, Kaplan-Meier product-limit). See D19/D20 before reaching for a library.
- **kNN distance uses `premium` only.** `effective_dom` must never enter `distance()` — that's target leakage (D19a).
- **Cache writes are write-through, per call, before parsing.** A paid-for RentCast API call is never lost or rolled back on a later failure (D24). Pipeline atomicity (what's *displayed*) is a separate concern from response persistence (what's *kept* on disk).
- **Live RentCast calls require both `RENTCOMP_LIVE=1` and a key present.** Default is fixture mode. Never write code that calls the network without this guard (D17). The monthly call cap is enforced in code.
- **Secrets never enter the repo.** API key lives in `.env` (mode 0600) via `RENTCAST_API_KEY`. Don't read, print, or log it.
- **Three model layers stay separate:** `dto.py` (wire truth, everything Optional) → `domain.py` (our truth, pipeline-guaranteed fields) → `responses.py` (API contract, codegens to TS). Don't let a DTO leak past the pipeline boundary.

## Testing — three layers, assert at the lowest one that can hold it

1. **pytest unit** (`backend/tests/unit/`) — pure functions, `hypothesis` property tests for stitcher/DOM/KM invariants
2. **pytest API contract** (`backend/tests/api/`) — FastAPI `TestClient`, most story acceptance criteria live here
3. **Playwright E2E** (`e2e/`) — only what genuinely needs a browser (layout, map sync, cache-consent modal)

Plus one Vitest file for `useDerive` (debounce/abort/latest-wins timing).

Full gate: `pytest && npx vitest run && npx playwright test` (inside the activated venv) — all three green is the merge condition. Use `/test` (see `.claude/commands/test.md`).

## Conventions

- Money: float64 throughout, round at display only — never `Decimal`
- Dates: parse to `datetime.date` at the DTO boundary, no timezones internally
- Don't add a runtime dependency without checking it against the dependency budget in the architecture doc first
