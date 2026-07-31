---
description: Run QA's mandatory sync ritual before a test run (WORKFLOW.md §4) — merge the LOCAL dev branch, then LOCAL main
argument-hint: <story-id>
---

Run the sync ritual from `rentcomp-pm/WORKFLOW.md` §4 for story `$ARGUMENTS`, on the current branch (should be `story/$ARGUMENTS-qa`):

1. `git merge story/$ARGUMENTS` — absorb the developer's latest work
2. `git merge main` — absorb latest main
3. If either merge brought changes or produced a conflict: report exactly what changed and resolve any conflict now, on this branch, before anything else happens. **Any test results from before this sync are void — rerun them.**
4. If both merges were clean no-ops, confirm the branch is up to date and ready for a test run.

## ⚠ Merge the LOCAL branch names. Never `origin/*`.

**This command used to say `git fetch origin` / `git merge origin/story/...` / `git merge origin/main`. That was wrong, and it was wrong in the most dangerous possible way: it succeeds, reports "Already up to date," and skips everything that actually landed.**

**This repo has no per-story remote push.** All story branches are local, and `main` runs 100+ commits ahead of `origin/main` by design. So `git merge origin/main` is **a no-op that looks like a sync and isn't one** — it silently skips whatever landed on local `main` since your branch was cut, which is exactly the class of miss this ritual exists to prevent.

This is not hypothetical. F3-S1's QA verify followed the old wording, got "already up to date", and consequently **never tested against F2-S2's same-session merge to local `main`.** It was harmless that time only because the two stories touched disjoint files and the PM's own post-merge regression caught it — luck, not the gate working.

*(Corrected 2026-07-30 after F4-S8/9b's QA verify noticed this file still contradicted `WORKFLOW.md` §4. It followed §4 rather than the command, and was right to — but a slash command is precisely where an agent trusts without checking, which is what made this worth fixing rather than just noting.)*

## Sync only

Do not run any tests as part of this command. This exists so the sync step is never skipped or half-remembered.
