# Git & QA Workflow

## 1. Repository

New dedicated git repository. `main` is protected by convention: nothing lands on main except via the merge protocol in §4. This `rentcomp-pm/` directory lives at repo root; `QUEUE.md` changes are committed by the PM directly to main (docs-only commits are exempt from the story protocol).

## 2. Branches — two per story

```
story/<id>        e.g. story/F4-S3      ← developer branch, cut from latest main
story/<id>-qa     e.g. story/F4-S3-qa   ← QA branch, cut from main directly
```

- **QA cuts `story/<id>-qa` from `main` first** — before the developer branch exists — and writes the story's tests there against the story's AC and the agreed API/UI contract. These tests are red; there's nothing to pass yet.
- Once QA hands off (via the PM), the developer cuts `story/<id>` from `main` and implements.
- QA then merges `story/<id>` into `story/<id>-qa` for every test run (§4 sync ritual) — this is how QA's branch acquires real code to test against, never mocks of it.
- Neither branch is long-lived: both exist only for the story's lifecycle.

## 3. The dev ↔ QA feedback loop

**The PM relays every step below — dev and QA never hand off to each other directly.**

```
QA drafts test-plan table + writes story tests on story/<id>-qa (red)        → PM
PM sanity-checks the plan, relays to dev                                     → dev
dev implements on story/<id> until QA's tests pass + writes its own
  supporting unit tests (usually all L1, most L2)                            → PM
PM relays to QA
QA: merge latest story/<id> into story/<id>-qa   ← sync BEFORE every test run
QA: run story spec (live Playwright + relevant unit tests) against the
  story's acceptance criteria + its epic flow, evaluating the developer's
  added tests too — not just whether the suite is green
  PASS → §4 regression gate
  FAIL → QA writes a feedback report (below) → PM relays to dev →
          dev fixes on story/<id> → PM relays back → loop
```

**QA feedback report format** (returned to the PM, who relays to the developer):

```
STORY: F4-S3
VERDICT: FAIL
AC VIOLATED: "spells with gap = threshold−1 merge, gap = threshold don't"
REPRO: fixture stitcher/gap-boundary.json, spec stitcher.spec.ts:41
OBSERVED: gap=42 merged
EXPECTED: gap=42 splits into two listings
NOTES: boundary comparison appears to be <= not <
```

Feedback is against the **story requirements**, not style preference. The loop continues until PASS. If the loop exceeds 3 rounds, PM checks whether the AC is ambiguous.

**QA PASS report format** (returned to the PM — this is what authorizes DONE):

```
STORY: F11-S3
VERDICT: PASS

TEST PLAN (every AC → layer, per AGENT_QA.md decision procedure):
  AC1  guard trips <3 neighbors within ±3pts   → L2  tests/api/test_price_test.py::test_guard_threshold
  AC2  guard state vs curve mutually exclusive → L3  e2e/f11-price-test.spec.ts:22
  AC3  nearest comps + distances surfaced      → L3  e2e/f11-price-test.spec.ts:41

AUTHORED BY: QA (AC2, AC3 — Playwright, written before dev started) /
             dev (AC1 — supporting unit test, written during implementation)
BRANCH SYNC: merged origin/story/F11-S3 (2 commits) + origin/main (clean) — results valid
REGRESSION:  pytest 143 passed · vitest 4 passed · playwright 9 passed
NOTES: (judgment calls, anything deferred, anything the PM should know)
```

The **test plan table is mandatory** — every acceptance criterion appears exactly once with its layer and the test that covers it. Writing it is what forces the layer decision to be made deliberately rather than by habit. A missing AC row, or an L3 row that could have been L2, is grounds for the PM to withhold DONE. The PM checks this table **twice**: lightly when QA first hands it off (before dev starts), and rigorously at DONE (after implementation, since tests sometimes evolve during the loop).

## 4. Sync discipline & the regression gate (the final technical hurdle)

**Hard rule: a branch's regression run only counts if the branch is up to date with the parent it merges into.** Concretely, before the final run QA must verify its branch is THE latest:

```
git fetch origin
git merge origin/story/<id>     # qa branch absorbs latest dev work
git merge origin/main           # and latest main — resolve conflicts NOW, not at merge time
npx playwright test             # full repo suite, not just this story's spec
```

If either merge brings changes, the previous test results are void — rerun. This is how we guarantee regression tests never break from unseen merge conflicts: conflicts are surfaced and resolved on the QA branch *before* the suite runs, and the suite that goes green is the suite that will exist on main after merge.

**Merge sequence after green:**

1. `story/<id>-qa` → `story/<id>` (tests join the code)
2. `story/<id>` → `main` (fast-forward or clean merge; if main moved since step 0's sync, go back to step 0)
3. Delete both branches; QA reports green to PM; PM marks DONE in QUEUE.md

**End-of-project:** the last queue item is a standalone full-regression pass — QA runs the entire accumulated suite on a fresh branch off main. All F1–F14 flow specs green = the engineering success definition (spec §8) is met.

## 5. CI — both options supported, local is the default

- **Local (default, zero infra):** `npx playwright test` headless is the gate; a `pre-push` git hook running the affected story's spec keeps feedback fast. The full suite runs at the §4 gate and end-of-project.
- **GitHub Actions (optional, if the repo is pushed to GitHub):** one workflow, `on: pull_request → main`, runs the full Playwright suite headless with `fail-fast`. Branch protection then enforces §4 mechanically ("require branches to be up to date before merging" mirrors the sync rule). Adopt any time; the local protocol is unchanged.

Both modes share the same invariant from the stories doc (T-S4): suite runs headless, **zero live API calls** — fixtures seed cached workspaces. The only live-API activity in the whole project is the T-S3 gate and real user pulls.

## 6. API call budget — HARD CONSTRAINT

The RentCast plan allows **50 calls/month**. Development therefore runs **entirely on saved sample responses and local caching**:

- **The gate is the only sanctioned live-call event during the build.** Budget: ≤10 calls. Every raw response the gate harness receives is committed to the repo at `fixtures/live-samples/` — it does double duty as the verification evidence *and* the canonical sample dataset the entire build develops against.
- **The API client (F0-S4) has two modes:** `fixture` (default — serves `fixtures/live-samples/` + synthetic fixtures, zero network) and `live` (requires an explicit env flag AND the API key; never the default anywhere). No agent enables live mode without PM authorization, and the PM doesn't authorize it without owner sign-off.
- **All tests, all dev servers, all QA runs: fixture mode.** This was already the T-S4 rule for tests; it now covers every process in the build.
- **The PM keeps a call ledger** at the top of QUEUE.md (calls used / remaining this month). Any proposed live call states its count against the ledger first.
- **No call is ever paid for twice** (ARCHITECTURE.md D24): responses are written to disk per-call before parsing, pulls are resumable from the manifest, and failures never roll back data already fetched. If the gate is interrupted at 6 of 10 calls, resuming costs 4 — not 10.
- Remaining budget after the gate (~40 calls) is reserved for the owner's real pricing pulls, not for development.

## 7. Commit hygiene

Story-scoped commits referencing the story ID (`F4-S3: stitch boundary uses strict <`). No drive-by refactors inside story branches — if a refactor is needed, PM queues it as its own story.
