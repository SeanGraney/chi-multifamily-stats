---
description: Run the full three-layer regression gate (pytest, vitest, playwright)
---

Run the full RentComp regression gate, in order, and report a clear pass/fail summary for each layer:

1. `pytest` — from `backend/`, all unit + API-contract tests, using the repo-root `.venv` (activate it or call `.venv/bin/pytest`; see CLAUDE.md's Venv bullet)
2. `npx vitest run` — from `frontend/`, the `useDerive` hook tests
3. `npx playwright test` — from `e2e/`, the full accumulated flow-spec suite (fixture mode only — confirm `RENTCOMP_LIVE` is unset before running)

If any layer fails, stop and report which layer, which test(s), and the failure output — don't proceed to the next layer on a red result. All three green is the merge condition (`WORKFLOW.md` §4); this is what QA runs before every merge and what the PM expects in a PASS report's regression line.
