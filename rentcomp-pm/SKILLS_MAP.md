# Skills Map — installed skills → agent assignments

Full inventory of skills installed on this machine, with assignments. Agents load **only** their assigned skills; the "unused" list is deliberate — loading irrelevant skill context degrades focus.

## Project Manager

| Skill | When |
|---|---|
| `product-management:sprint-planning` | Constructing/reconstructing the queue; sizing against agent availability |
| `product-management:roadmap-update` | Any reprioritization — state what changed, what moves, before/after |
| `operations:status-report` | Owner-facing status (green/yellow/red per epic, risks, blocked) |
| `product-management:stakeholder-update` | Optional: milestone announcements to the owner (gate result, walking skeleton done, MVP done) |

## Developer subagent

| Skill | When |
|---|---|
| `engineering:code-review` | Mandatory self-review before every QA handoff |
| `engineering:debug` | QA repro resists diagnosis |
| `engineering:architecture` | A story forces a real design decision → short ADR, flag PM |

## QA / Regression subagent

| Skill | When |
|---|---|
| `engineering:testing-strategy` | Designing each story's Playwright spec: coverage, edges, fixtures |
| `engineering:code-review` | Reviewing the dev diff for missed AC before testing |
| `engineering:deploy-checklist` | The end-of-project full regression pass, treated as a release |

## Per-story skill additions

The PM's dispatch message names the base role skills **plus** these story-specific additions. Stories not listed use base skills only.

| Story | Agent | Additional skill | Why |
|---|---|---|---|
| T-S3 + F4-S7 (gate) | dev | `data:validate-data` | Sanity-check the first real pull before the go/no-go verdict |
| F0-S2 (derivation graph) | dev | `engineering:architecture` | Mandatory ADR, owner sign-off, before implementation |
| WS-1 (walking skeleton) | dev | `engineering:architecture` | Post-QA architecture review checkpoint before parallel dispatch opens |
| F11-S2 (weighted KM) | dev + qa | `data:statistical-analysis` | Estimator correctness; verification against reference implementation |
| F4-S3 (stitcher) | qa | `data:explore-data` | Profiling the golden-file fixture set for edge coverage |
| Final regression pass | qa | `engineering:deploy-checklist` | Already base for QA — listed here as the release-treatment reminder |

## Installed but deliberately unused (and why)

- `design:*` (critique, handoff, a11y, ux-copy, research) — UI design is settled in the spec + prototype; no design phase in this build. Exception: if a UI story genuinely stalls on a visual decision, PM may authorize a one-off `design:design-critique`.
- `data:*` (analyze, viz, SQL, dashboards) — RentComp's statistics are specified in the stories with exact formulas; no exploratory analysis belongs in the build. Exception: `data:validate-data` may be used once during the T-S3 gate to sanity-check the first real pull.
- `product-management:write-spec`, `brainstorm`, `synthesize-research`, `competitive-brief`, `metrics-review` — spec phase is complete; reopening it is an owner decision, not an agent one.
- `operations:*` (except status-report) — no CAB, vendors, capacity plans, or compliance in a solo project.
- `engineering:standup`, `incident-response`, `tech-debt`, `documentation`, `system-design` — wrong scale or wrong phase; `tech-debt` becomes relevant post-MVP only.
- Document/file skills (`docx`, `pptx`, `xlsx`, `pdf`, `pdf-viewer:*`) — no document deliverables in the build loop.
- `skill-creator`, `web-artifacts-builder`, `chat-driven-product-discovery`, `schedule`, `setup-cowork` — out of scope for this repo.
