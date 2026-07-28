# RentComp — chi-multifamily-stats

Local-only Python (FastAPI) service that owns all data and math, serving a React SPA that is a pure view layer. JSON files on disk instead of a database. See `CLAUDE.md` for the architecture summary and `rentcomp-pm/ARCHITECTURE.md` for the full decisions register (D1–D24).

## Setup on a fresh machine

Requires Python 3.12+ and Node 20+ (the system Node may be too old — use `nvm install 20` if so).

```bash
git clone https://github.com/SeanGraney/chi-multifamily-stats.git
cd chi-multifamily-stats

# Backend — one venv at repo root, nothing else
python3.12 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -r backend/requirements-dev.txt -e backend/

# Frontend
cd frontend && npm ci && cd ..

# E2E (Playwright)
cd e2e && npm ci && npx playwright install --with-deps && cd ..

# Secrets (only needed for live RentCast calls — default is fixture mode, D17)
cp .env.example .env
chmod 600 .env
# fill in RENTCAST_API_KEY if you have one; the app and test suite run fine without it
```

## Running it

```bash
source .venv/bin/activate.fish   # or the bash/zsh equivalent
rentcomp                         # serves API + built UI on localhost:8000
```

## Running the tests

```bash
.venv/bin/pytest backend/tests            # unit + API contract
cd frontend && npx vitest run && cd ..    # useDerive timing (once that suite lands)
cd e2e && npx playwright test && cd ..    # full E2E regression
```

All three green is the merge condition for any story — see `rentcomp-pm/WORKFLOW.md`.

## Project management

This build is run through an in-repo PM/QA/dev protocol, not ad hoc — see `rentcomp-pm/PROJECT_MANAGER.md` for the process and `rentcomp-pm/QUEUE.md` for live story state.
