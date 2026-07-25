# Pipeline stage order and contracts

Per `rentcomp` architecture §2 (repository layout) and §3 (model layers).
This file exists so the `add-pipeline-stage` skill doesn't have to carry the
full architecture doc inline — it's the bundled-reference-file pattern:
`SKILL.md` stays short and links here for detail.

## Current stage order

1. **dedupe** — collapse duplicate raw listings (same unit, same source)
2. **spells** — group a comp's raw snapshots into `Spell` records (listed, removed, price, active)
3. **stitch** — merge spells into one `StitchedComp` per physical unit (`initial_ask`, `effective_dom`, `censored`, `cut_history`, `relist_count`, `gap_days`)
4. **classify** — assign `removal_class`: `pending | provisional | leased`
5. **window** — apply the observation window, flag `withdrawal_suspect`
6. **cohort** — assign `cohort_year`, compute cohort membership
7. **premium** — compute `premium` (size-normalized distance from cohort market) and `psf`

Output of `premium` is what `stats/knn.py` and `stats/km.py` consume to
build `DerivedState`.

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
