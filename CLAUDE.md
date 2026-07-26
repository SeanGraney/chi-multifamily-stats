# RentComp — chi-multifamily-stats

Local-only Python (FastAPI) service that owns all data and math, serving a
React SPA that is a pure view layer. JSON files on disk instead of a
database. This file distills the project's architecture decisions register
(D1–D24) into what Claude should hold in working memory on every turn — the
`backend/`, `frontend/`, and `rentcomp-pm/` layout below is the target
structure, not yet built.

## Role — read this first

**If you are the top-level session for this repo, you are the Project Manager.** Read `rentcomp-pm/PROJECT_MANAGER.md` in full before doing anything else in this repo, and operate according to it for the rest of the session — queue management, dispatch, and verification only; you do not write code or tests yourself. (If you were spawned as a subagent instead, your dispatch already told you which role you are — `.claude/agents/developer.md` or `.claude/agents/qa.md` — this rule doesn't apply to you.)

## Where to find things

- Full spec, epics, stories, north star, semantic-change protocol: `rentcomp-pm/docs/`
- Architecture decisions D1–D24 (binding): `rentcomp-pm/ARCHITECTURE.md`
- Git/QA/regression workflow: `rentcomp-pm/WORKFLOW.md`
- Agent roles: `rentcomp-pm/AGENT_DEVELOPER.md`, `rentcomp-pm/AGENT_QA.md`
- Live story queue: `rentcomp-pm/QUEUE.md`

Google Drive is a historical snapshot only — never a source of truth. Read docs from this repo.

## Stack

- **Backend:** Python 3.12, FastAPI, Pydantic, numpy, pip and venv for packaging/venv
- **Venv:** `.venv/` at repo root (gitignored) is the project's **only** Python environment — every `pip`, `pytest`, and `rentcomp` invocation runs inside it (`source .venv/bin/activate.fish`, or call `.venv/bin/<tool>` directly; never system pip). Bootstrap state: created on Python 3.10 with only `httpx`, just enough to run `scripts/gate.py` (T-S3) before the backend exists. F0-S1a recreates it on Python 3.12 with the full backend install (`pip install -e .`) — 3.12 must be installed on this machine first. Never create a second/parallel venv.
- **Frontend:** Vite + React 18 + TypeScript + Tailwind
- **Launch:** `pip install -e .` once, then `rentcomp` (console script, D7) — serves the API and the built UI together on `localhost:8000`
- **Types:** OpenAPI → TypeScript codegen (`openapi-typescript`), committed — never hand-edit generated types
- **Storage:** `~/.rentcomp/` (config, secrets, cache, workspaces, decisions) — no database

## Hard rules (violating these breaks the architecture, not just style)

- **All derivation happens in Python.** The frontend never computes a statistic — it renders what `/api/derive` returns.
- **No ML frameworks.** No scikit-learn, no torch, no pandas at runtime. Everything here is classical stats/arithmetic (weighted median, 1-D kNN via `argsort` by default, Kaplan-Meier product-limit). See D19/D20 and `rentcomp-pm/docs/SEMANTIC_CHANGE_PROTOCOL.md` before reaching for a library.
- **kNN distance uses `premium` only.** `effective_dom` must never enter `distance()` — that's target leakage (D19a).
- **Cache writes are write-through, per call, before parsing.** A paid-for RentCast API call is never lost or rolled back on a later failure (D24). Pipeline atomicity (what's *displayed*) is a separate concern from response persistence (what's *kept* on disk).
- **Live RentCast calls require both `RENTCOMP_LIVE=1` and a key present.** Default is fixture mode. Never write code that calls the network without this guard (D17). The monthly call cap is enforced in code.
- **Secrets never enter the repo.** API key lives in `.env` (mode 0600) via `RENTCAST_API_KEY`. Don't read, print, or log it.
- **Three model layers stay separate:** `dto.py` (wire truth, everything Optional) → `domain.py` (our truth, pipeline-guaranteed fields) → `responses.py` (API contract, codegens to TS). Don't let a DTO leak past the pipeline boundary.
- **Story requirements are tagged `[INVARIANT]` (locked meaning) or `[DEFAULT]` (suggested implementation, free to deviate with a logged reason).** See `rentcomp-pm/docs/rentcomp_technical_stories.md`. A change that alters what a number means — not just how it's computed — requires the Semantic Change Protocol, not a unilateral call.

## Testing — three layers, assert at the lowest one that can hold it

1. **pytest unit** (`backend/tests/unit/`) — pure functions, `hypothesis` property tests for stitcher/DOM/KM invariants
2. **pytest API contract** (`backend/tests/api/`) — FastAPI `TestClient`, most story acceptance criteria live here
3. **Playwright E2E** (`e2e/`) — only what genuinely needs a browser (layout, map sync, cache-consent modal)

Plus one Vitest file for `useDerive` (debounce/abort/latest-wins timing).

Full gate: `pytest && npx vitest run && npx playwright test` (with `.venv` active — see Venv bullet above) — all three green is the merge condition. Use `/test` (see `.claude/commands/test.md`).

## Conventions

- Money: float64 throughout, round at display only — never `Decimal`
- Dates: parse to `datetime.date` at the DTO boundary, no timezones internally
- Don't add a runtime dependency without checking it against the dependency budget in the architecture doc first
