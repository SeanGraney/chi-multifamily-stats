---
description: Run the full three-layer regression gate (pytest, Vitest, Playwright)
allowed-tools: Bash(uv run pytest*), Bash(npx vitest*), Bash(npx playwright*)
---

Run the project's full test gate, in order, and report results as a punch list:

1. `uv run pytest` — backend unit + API-contract tests
2. `npx vitest run` — `useDerive` hook timing tests
3. `npx playwright test` — E2E flows (fixture mode, no live API calls)

Stop and report immediately if a layer fails — don't run later layers past a
failure unless asked to. For each failure, name the file and the specific
assertion, not just "tests failed." All three green is the merge condition
for this project.
