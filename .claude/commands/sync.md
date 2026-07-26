---
description: Run QA's mandatory sync ritual before a test run (WORKFLOW.md §4) — fetch, merge dev branch, merge main
argument-hint: <story-id>
---

Run the sync ritual from `rentcomp-pm/WORKFLOW.md` §4 for story `$ARGUMENTS`, on the current branch (should be `story/$ARGUMENTS-qa`):

1. `git fetch origin`
2. `git merge origin/story/$ARGUMENTS` — absorb the developer's latest work
3. `git merge origin/main` — absorb latest main
4. If either merge brought changes or produced a conflict: report exactly what changed and resolve any conflict now, on this branch, before anything else happens. Any test results from before this sync are void.
5. If both merges were clean no-ops, confirm the branch is up to date and ready for a test run.

Do not run any tests as part of this command — sync only. This exists so the sync step is never skipped or half-remembered.
