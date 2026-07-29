# Pipeline stage order and contracts

Per `rentcomp` architecture §2 (repository layout) and §3 (model layers).
This file exists so the `add-pipeline-stage` skill doesn't have to carry the
full architecture doc inline — it's the bundled-reference-file pattern:
`SKILL.md` stays short and links here for detail.

## Current stage order — and the split that matters more than the order

> **Amended 2026-07-29 by the PM.** ADR-001 §"the one genuine conflict" ruled
> this split and left the edit as an explicit PM follow-up (`.claude/skills/`
> was out of that dispatch's scope). Two further errors are corrected below:
> the `removal_class` ladder ended at the wrong word, and `withdrawal_suspect`
> was attributed to the wrong stage. **Do not re-flatten this list.**

The pipeline is **two groups, not one chain**, and the load-bearing distinction
is *what each stage depends on*:

| Group | Stages | Depends on | Runs |
|---|---|---|---|
| **A — record shaping** | dedupe → spells → stitch → classify → window → cohort → **psf** | raw pull + config + `as_of` only. **Not** on curation | once per (pull, config), memoized |
| **B — curation derivation** | **cohortMedians → premiums** → anchor → buckets → kNN → guard → KM | Group A's output + the request body | **every request** |

Group A's output *is* `cleanedComps`: `tuple[StitchedComp, ...]`.

### Group A — record shaping

1. **dedupe** — collapse duplicate raw listings (same unit, same source). Note
   the unit designator is part of identity: `# 1`, `Unit 1` and `Apt 1` are the
   same physical unit (F4-S2 found 4 real units counted twice).
2. **spells** — group a comp's raw snapshots into `Spell` records (listed,
   removed, price, active)
3. **stitch** — merge spells into one `StitchedComp` per physical unit
   (`initial_ask`, `effective_dom`, `censored`, `cut_history`, `relist_count`,
   `gap_days`). Contiguous history events (gap ≤ 1d) merge as **price changes**
   *before* the 42d re-list threshold applies — 23 real records are price-change
   chains, not re-lists.
4. **classify** (F4-S8) — assign `removal_class`:
   **`pending | provisional | confirmed`**. ⚠ **The ladder ends at
   `confirmed`, NOT `leased`** — `models/domain.py:47` is the truth
   (`RemovalClass`), per a standing PM ruling recorded there as an erratum.
   **This stage also sets `withdrawal_suspect`** (a complete spell whose unit
   re-lists 6w–6mo later) — flagging happens here, *before* the window filter,
   and it is **display-only: never auto-exclude on it**.
5. **window** (F4-S4) — keep records whose **stitched** start month-day falls
   inside the year-agnostic window; drop padding-only records and count them in
   a pipeline debug summary. ⚠ This stage does **not** own
   `withdrawal_suspect` — see stage 4.
6. **cohort** (F4-S4) — assign `cohort_year` = calendar year of the **stitched**
   start; compute cohort membership
7. **psf** — `psf = initial_ask / sqft`, a property of the record alone. Stays
   in Group A because no curation input reaches it.

### Group B — curation derivation

`premium` lives **here**, not in Group A. F4-S5 is `[INVARIANT]` and defines
`premium = psf / cohort_median_psf − 1` where the cohort median is taken **over
selected comps** — so premium changes the moment a user toggles a comp, and it
cannot be memoized once per pull.

Group B's output is what `stats/knn.py` and `stats/km.py` consume to build
`DerivedState`.

**If you are adding a stage, decide its group first.** Getting this wrong is
not a style error: a curation-dependent stage placed in Group A gets memoized
and silently stops responding to the user, and a record-shaping stage placed in
Group B is recomputed on every request against a 100ms budget.

## Contract rules for any new stage

- **Input type is the previous stage's output type.** Don't reach backward
  into an earlier stage's intermediate state.
- **DTO fields are Optional; domain fields are not** (D-model-layers rule).
  A new stage inserted before `stitch` still sees Optional fields and must
  handle absence explicitly — don't assume RentCast populated something.
- **Never let `effective_dom` or `censored` influence anything upstream of
  `premium`.** Those are target fields for the kNN/KM logic downstream —
  see D19a in the architecture doc. A pipeline stage computing eligibility
  or cohort membership based on how long a comp took to lease is target
  leakage, not just bad layering.
- **Stages are pure functions.** No I/O, no cache reads/writes — that
  belongs in `storage/`. Testable with plain pytest, no fixtures beyond
  input data.
