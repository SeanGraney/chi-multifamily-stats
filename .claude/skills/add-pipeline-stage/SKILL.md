---
name: add-pipeline-stage
description: Scaffold a new stage in the RentComp comp-processing pipeline — Group A record shaping (dedupe -> spells -> stitch -> classify -> window -> cohort -> psf, memoized once per pull) or Group B curation derivation (cohortMedians -> premiums -> anchor -> buckets -> kNN -> guard -> KM, every request). Use when adding a new transformation step between raw DTOs and DerivedState — not for one-off bug fixes or endpoint work (use /new-endpoint for that).
argument-hint: <stage-name> <insert-after-stage>
allowed-tools: Read, Write, Edit, Glob, Grep
paths: ["backend/src/rentcomp/pipeline/**"]
---

Scaffold a new pipeline stage: $ARGUMENTS (stage name, and which existing
stage it runs after).

Read [reference.md](reference.md) first — it has the current stage order and
the input/output contract each stage must honor.

1. **Decide the stage's group before writing a line** — Group A (record
   shaping: depends only on the raw pull, config and `as_of`; memoized once
   per pull) or Group B (curation derivation: depends on the request body,
   recomputed every request). `reference.md` has the table. This is the one
   decision that is expensive to get wrong: a curation-dependent stage placed
   in Group A gets memoized and silently stops responding to the user.
2. Create `backend/src/rentcomp/pipeline/<stage_name>.py` with a single
   function taking the previous stage's output type and returning its own —
   check `reference.md` for the exact types at that point in the pipeline.
3. Wire it into the pipeline orchestrator at the position given in
   `$ARGUMENTS`, not at the end by default.
4. Add a pytest unit test in `backend/tests/unit/` for the pure function.
   If the stage has boundary conditions (a cutoff, a window edge, a
   threshold) add a `hypothesis` property test too — this pipeline has a
   history of edge-case bugs living exactly at stage boundaries.
5. Do not let DTO fields (`dto.py`, all-Optional) leak past this stage
   unvalidated — anything the stage depends on should already have been
   made non-Optional by an earlier stage, per the DTO -> domain layering
   rule in CLAUDE.md.

Stop and ask before renaming or reordering existing stages — this scaffolds
an addition, not a refactor.
