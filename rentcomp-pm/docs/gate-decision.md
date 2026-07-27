# T-S3 Go/No-Go Gate — Decision Record

**Run date:** 2026-07-26
**Subject:** 3651 S Wood St, Chicago, IL 60609
**Windows:** [{'year': 2026, 'daysOldMin': 1, 'daysOldMax': 88}, {'year': 2025, 'daysOldMin': 250, 'daysOldMax': 453}, {'year': 2024, 'daysOldMin': 615, 'daysOldMax': 818}]
**Calls spent this run:** 6
**Raw records pulled:** 1
**Distinct addresses/ids:** 1

**Verdict: NO-GO — insufficient comp coverage**

(Threshold: >=15 distinct comps pre-stitching, matching spec §8's leading indicator
of >=15 usable comps per pull. This is a raw-count sanity check, not the final
usable-comp count — real dedupe/stitch/window/cohort filtering happens in F4 and
will reduce this number. If this raw count is already under 15, the pipeline's
filtered count will be lower still, which is why this is a gate, not a formality.)

Raw responses saved to `fixtures/live-samples/` — these seed the entire build's
fixture-mode development going forward (WORKFLOW.md §6).
