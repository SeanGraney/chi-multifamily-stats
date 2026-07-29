# Git & QA Workflow

## 1. Repository

New dedicated git repository. `main` is protected by convention: nothing lands on main except via the merge protocol in §4. This `rentcomp-pm/` directory lives at repo root; `QUEUE.md` changes are committed by the PM directly to main (docs-only commits are exempt from the story protocol).

**Push `main` to `origin` after every story DONE, and after any milestone/epic completion — owner-authorized standing rule (2026-07-27), no per-push confirmation needed.** `git push origin main` after the merge + branch cleanup that already happens at DONE (§4 step 3). If a push ever fails non-trivially (diverged origin, conflict) stop and flag it rather than force-pushing.

## 2. Branches — two per story

```
story/<id>        e.g. story/F4-S3      ← developer branch, cut from latest main
story/<id>-qa     e.g. story/F4-S3-qa   ← QA branch, cut from main directly
```

- **QA cuts `story/<id>-qa` from `main` first** — before the developer branch exists — and writes the story's tests there against the story's AC and the agreed API/UI contract. These tests are red; there's nothing to pass yet.
- Once QA hands off (via the PM), the developer cuts `story/<id>` from `main` and implements.
- QA then merges `story/<id>` into `story/<id>-qa` for every test run (§4 sync ritual) — this is how QA's branch acquires real code to test against, never mocks of it.
- Neither branch is long-lived: both exist only for the story's lifecycle.
- **Isolated-worktree dispatches (`isolation: "worktree"`) only have one shared `.venv/` — the one at the main checkout's repo root.** Its editable install (`pip install -e .`) resolves imports against the main checkout's `backend/src`, not the worktree's own copy. Prepend `PYTHONPATH=<this-worktree>/backend/src` to `pytest`/`python` invocations run inside a worktree, or the run silently tests the *wrong tree's* code. Found by the F2-S2 dev (2026-07-27).

  - **⚠ On Windows the `PYTHONPATH` separator is `;`, not `:`.** A POSIX-separated multi-entry value collapses into one bogus path, the editable install's `.pth` entry wins, and **the run silently tests `main` while looking entirely correct.** This is the §2 miss in its most deceptive form — it does not error, it does not skip, it just answers about the wrong code. Found by F11-S2's QA (2026-07-28), which hit it directly. **Verify the loaded code is yours** (assert on a route, symbol, or behaviour that exists only on your branch) rather than trusting the invocation.

  - **Running Playwright from a worktree — use these two doors, never a junction.** `git worktree remove --force` follows directory junctions and destroyed the main checkout's `.venv` and `e2e/node_modules` on 2026-07-28 (INCIDENT #5); agents only created those junctions because they had no other way, and these remove the reason.
    1. **Dependency resolution:** set `NODE_PATH=<main checkout>/e2e/node_modules` and invoke `<main checkout>/e2e/node_modules/@playwright/test/cli.js` from the worktree's `e2e/`. Bare `npx` silently fetches a standalone Playwright where `@playwright/test` is unresolvable. (F2-S1 QA, 2026-07-28.)
    2. **Server-binary resolution:** `local-server.ts` resolves `VENV_RENTCOMP` from `REPO_ROOT`, which inside a worktree is *the worktree* — so `.venv/Scripts/rentcomp.exe` is missing and every live-server spec dies in `beforeAll`. **Copy** (never junction) main's `rentcomp.exe` into `<worktree>/.venv/Scripts/`. The launcher embeds an absolute path to main's `python.exe`, so it still resolves main's site-packages, and the spec's own `PYTHONPATH` then points it at the worktree's `backend/src`. It is gitignored and a **plain file**, so `git worktree remove --force` can only delete the copy — no INCIDENT #5 exposure. (F2-S1 dev, 2026-07-28.)
    3. **Static-UI resolution:** `app.py::_ui_dist_dir()` resolves `Path(__file__).resolve().parents[3] / "frontend" / "dist"`, so with a worktree package loaded it looks in the *worktree's* `frontend/dist`, which does not exist. Override with **`RENTCOMP_UI_DIR=<main checkout>/frontend/dist`** whenever `frontend/` and `e2e/` are byte-identical between your branch and main (check with `git diff --stat <main> HEAD -- frontend/ e2e/`) — then main's UI + main's specs against *your* backend is the exactly-right composition. Without it you get a reproducible `2 failed, 8 did not run` that is an artifact of the setup, not of your branch. (F11-S2 QA, 2026-07-28.)

  - **⚠ Run `pytest` BARE from the repo root. `pytest backend/tests` silently under-collects by 44 — and the 44 are the money tests.** `pytest.ini`'s `testpaths` names **both** `scripts/tests` and `backend/tests`; passing a path argument overrides it. PM verified on `main` 2026-07-28: **994 collected bare vs 950 with `backend/tests`**, and the missing 44 are `scripts/tests/test_gate*.py` — **the T-S3 gate harness, which guards the only code in this repo that spends real API calls.** A path-argument run reports a smaller number that still looks like a clean pass, so **any test count quoted from a path-argument invocation is under-reported** and must not be compared against a bare-run baseline. Found by F5-S2's dev (whose own first run read 969 instead of 1013). **Quote the invocation alongside the count in every handoff.**

  - **⚠ Port 8000 contention across parallel agents — a false green with no bad code anywhere.** `local-server.ts` spawns a server and then polls `127.0.0.1:8000`. If another agent already holds that port, the spawn's bind fails but **`waitForServer` succeeds against the other agent's build**, and the spec silently tests the wrong code while reporting green. Hit by F5-S2's dev, which waited rather than killing the other process — the right call. **This risk is created by running several Playwright-capable agents at once, so it is the PM's to manage:** stagger browser-leg dispatches, or have the spec bind an ephemeral port. Until then, **any worktree Playwright run must verify the server it reached is its own** (diff the route list, or assert on a symbol that exists only on the branch).

  - **A skip that exits 0 is indistinguishable from a pass** — the defect class that has now recurred four times in this project. `13 passed, 10 skipped, exit 0` is a FALSE GREEN and must never be reported as green. Prefer a **failing** harness-precondition test over a skip guard (F2-S1 QA's pattern, ratified 2026-07-28): it is the only form that cannot be mistaken for success.

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
git merge story/<id>            # qa branch absorbs latest dev work
git merge main                  # and latest LOCAL main — resolve conflicts NOW, not at merge time
npx playwright test             # full repo suite, not just this story's spec
```

**This repo has no per-story remote push** (`git status` on `main` runs 100+ commits ahead of `origin/main` by design — see `PROJECT_MANAGER.md`'s dispatch protocol, all branches are local). `git fetch origin` / `git merge origin/main` is therefore a **no-op that looks like a sync but isn't one** — it silently skips whatever landed on local `main` since the story branch was cut, which is exactly the class of miss this rule exists to prevent. Merge the **local** branch names (`story/<id>`, `main`), never the `origin/*` refs, unless this project's workflow changes to push per story. (Found 2026-07-27: F3-S1's QA verify merged `origin/main` per the old wording here, got "already up to date," and consequently never tested against F2-S2's same-session merge to local `main` — harmless that time since the two stories touched disjoint files and the PM's own post-merge regression on main caught it, but it was luck, not the gate working as designed.)

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
