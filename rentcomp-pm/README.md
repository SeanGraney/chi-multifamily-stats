# rentcomp-pm — Project Manager Agent Context

Drop this directory into the root of the new RentComp git repository. It is the complete operating context for the **project-manager agent** and the two subagent roles it coordinates.

## Layout

```
rentcomp-pm/
  README.md              ← you are here
  PROJECT_MANAGER.md     ← PM agent charter: queue management, dispatch, completion
  ARCHITECTURE.md        ← binding stack decisions: FastAPI + Vite/React, JSON on disk, no DB
  WORKFLOW.md            ← git branching protocol, dev↔QA feedback loop, regression gate
  AGENT_DEVELOPER.md     ← developer subagent role prompt
  AGENT_QA.md            ← QA/regression subagent role prompt
  SKILLS_MAP.md          ← installed skills on this machine → which agent uses which
  QUEUE.md               ← the story queue: order, dependencies, blocked/ready state
  docs/
    rentcomp_functional_spec.md    ← WHAT to build (source of truth for behavior)
    rentcomp_epics_mvp.md          ← user flows F1–F14 + cross-cutting invariants
    rentcomp_technical_stories.md  ← the backlog: stories + acceptance criteria
    rentcomp_skill_review.md       ← known risks, assumption analysis, review actions
```

## Reading order for any agent joining cold

1. `docs/rentcomp_functional_spec.md` — what the product is and every algorithm's exact definition
2. `docs/rentcomp_epics_mvp.md` — the flows and the five invariants every change must respect
3. `ARCHITECTURE.md` — the stack, model layers, API surface, storage layout (binding)
4. `docs/rentcomp_technical_stories.md` — the story you were assigned, with its AC
5. Your role file (`AGENT_DEVELOPER.md` or `AGENT_QA.md`) + `WORKFLOW.md`

## Test layering (ARCHITECTURE.md §9)

Three layers — pytest unit · pytest API-contract · Playwright flows (+ one Vitest file). **Every assertion goes at the lowest layer that can hold it.** Developers own Layer 1; QA owns Layers 2–3 and runs the decision procedure in `AGENT_QA.md` over each acceptance criterion, submitting the resulting AC→layer table in its PASS report. The PM verifies that table before marking a story DONE.

## Non-negotiables (inherited from the epics doc, enforced by QA)

1. **Evidence-first** — every aggregate ≤1 click from its comps
2. **Honesty** — censored ≠ leased; no interpolation; guard before curve
3. **Reactivity** — one derivation pass, no stale panels
4. **Cost** — no API call outside the cache-modal consent path
5. **Reversibility** — nothing destructive except user-confirmed refresh
