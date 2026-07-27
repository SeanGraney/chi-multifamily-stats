# F4-S7 — First-Live-Pull Verification Report (spec §3.4)

**Story:** T-S3 / F4-S7 · **Author:** developer agent · **Date:** 2026-07-26
**Data:** entirely offline analysis of the committed round-2 broad-pull fixtures —
`fixtures/live-samples/fe9de5158f036802.json` (Active, 39 records) and
`fixtures/live-samples/6327600317b11d16.json` (Inactive, 500 records), pulled
2026-07-26 with `daysOld=1:1095`, radius 2.0 mi, `bedrooms=3:4`, five property
types, no bathrooms filter. Round-1 windowed fixtures (5 empty + 1 single-comp
response) are the sparsity evidence that forced the broad redesign and are not
re-analyzed here. Zero live calls were made for this report.

Totals: 539 raw records, 539 distinct addresses (no duplicates), 337 in-window
distinct (padded seasonal windows 07-28..08-20 ±90d for 2026/2025/2024 — the
GO basis in `gate-decision.md`). Per-window in-window records: 2026: 70 ·
2025: 155 · 2024: 112.

---

## 1. Does `history` preserve prior spells on re-listed properties? — YES

`history` is a dict keyed by event date; every event observed is type
`"Rental Listing"` and carries `price`, `listedDate`, `removedDate` (absent on
the still-active segment), and `daysOnMarket` per segment.

**Distribution of history sizes (539 records):**

| history events | records |
|---|---|
| 0 (no history object) | 44 |
| 1 | 422 |
| 2 | 63 |
| 3 | 9 |
| 5 | 1 |

**73 records (13.5%) carry ≥2 dated events.** 580 events total, 541 with a
`removedDate`. Splitting multi-event records by the gap between one event's
`removedDate` and the next event's start date:

- **50 records contain a true re-list** (gap > 1 day) — prior spells ARE
  preserved, with their own price and DOM. 32 of these have a gap > 42 days
  (the spec's confirmed-lease threshold).
- **23 records are contiguous price-change chains** (gap ≤ 1 day): the same
  continuous spell re-keyed at each price change, NOT separate spells.

Gap distribution across all consecutive event pairs: ≤1d: 30 · 2–7d: 10 ·
8–42d: 13 · 43–180d: 11 · >180d: 21.

**Concrete re-list examples (address · spell 1 · spell 2 · gap):**

1. `2652 W 23rd Pl, Unit 3, Chicago, IL 60608` — listed 2025-03-17 at $1,790,
   removed 2025-05-21 (DOM 65) → re-listed 2026-03-25 at $2,400. Gap 308 days.
2. `2624 W 24th St, Unit 1F, Chicago, IL 60608` — listed 2025-01-22 at $2,400,
   removed 2025-04-13 (DOM 81) → re-listed 2026-06-12 at $2,450. Gap 425 days.
3. `504 W 32nd St, Apt 3F, Chicago, IL 60616` — listed 2025-06-18 at $1,695,
   removed 2025-08-11 (DOM 54) → re-listed 2026-06-10 at $1,750. Gap 303 days.

And a price-change chain that must NOT be read as re-lists:
`2411 W 34th Pl, Unit 2` — 2026-05-29 $2,250 → 2026-06-24 $2,650 →
2026-07-09 $2,250, each segment's `removedDate` equal to the next segment's
start. One spell, two price changes (this is the `cut_history` source).

**Decisions this feeds (F4-S3 stitching):**

- The primary stitching path works: history preserves prior spells with dates
  and prices, so within-record stitching over `history` is viable — no
  cross-pull fallback path needed for MVP.
- The stitcher MUST merge contiguous events (gap ≤ 1 day) into a single spell
  before applying the re-list gap threshold — treating every history key as a
  spell would fabricate re-lists out of price cuts (30 of 84 observed gaps are
  ≤1d). Price-change chains become `cut_history` on one spell.
- **Fallback needed for 44 records (all Inactive, 8.8% of the pull) that have
  NO history object at all.** All 539 records have top-level `listedDate`
  (100%) and Inactive records have top-level `removedDate`, so the fallback is:
  synthesize a single spell from the top-level fields. The DTO must not assume
  `history` exists.
- All 39 Active records have a history object and no top-level `removedDate` —
  the censored (still-active) case is cleanly identifiable.

## 2. Per-property-type yields and field completeness

**Counts by `propertyType`:**

| propertyType | all (539) | in-window (337) | Active (39) | Inactive (500) |
|---|---|---|---|---|
| Apartment | 226 | 151 | 28 | 198 |
| Single Family | 222 | 131 | 8 | 214 |
| Condo | 67 | 39 | 3 | 64 |
| Townhouse | 21 | 14 | 0 | 21 |
| Multi-Family | 3 | 2 | 0 | 3 |

**Findings for the default type set (F0-S4 / F4-S1):**

- **`Multi-Family` as a RentCast type is nearly empty (3 records).** Units in
  small multifamily buildings list as `Apartment` (e.g. "Unit 2F", "Apt 3F"
  addresses dominate the Apartment set). A type set built around "Multi-Family"
  semantically — as round 1's default was — starves the pull. `Apartment` is
  the load-bearing type for this micro-market.
- `Single Family` is 41% of the pull and 39% of in-window records — dropping it
  halves the evidence base. Whether an SF rental is a valid comp for a
  multifamily unit is a curation decision (spec's manual-vetting step), not a
  pull decision. Recommendation: default type set = all five pulled types;
  filter at curation, not at the API.
- Every record has `bedrooms` ∈ {3: 477, 4: 62} — the `3:4` range filter is
  respected server-side.

**Field completeness (539 records, missing or null):**

| field | missing | % | DTO/pipeline implication |
|---|---|---|---|
| `price` | 0 | 0% | — |
| `listedDate` | 0 | 0% | — |
| `bedrooms` | 0 | 0% | — |
| `bathrooms` | 0 | 0% | complete in THIS pull; keep Optional (D5 wire truth) — round 2 removed the server-side bathrooms filter, so completeness here is not guaranteed by construction |
| `daysOnMarket` | 0 | 0% | present, but derive DOM ourselves from spell dates (D15) |
| `removedDate` | 39 | 7.2% | exactly the 39 Active records — this is the censoring marker, not missing data |
| `history` | 44 | 8.2% | all Inactive; single-spell fallback from top-level fields (above) |
| `squareFootage` | 79 | 14.7% | premium is undefined for these — F4-S5's missing-sqft path (flag, weight 0, excluded from medians) will drop ~15% of comps; spread across types (Apt 36, SF 23, Condo 19, TH 1) |
| `yearBuilt` | 254 | 47.1% | display-only field; must be Optional and the UI must tolerate absence |

## 3. Pagination behavior — ANSWERED

From the run ledger (`fixtures/live-samples/ledger.json`, `includeTotalCount`
requested on every call):

- **Active:** `X-Total-Count: 39`, 39 records returned — complete.
- **Inactive:** `X-Total-Count: 690`, **500 records returned** — the server
  paginates at the `limit` ceiling (max 500). **The committed Inactive fixture
  is truncated: 190 matching records were not fetched.** `offset` is the
  paging mechanism (per the committed schema); a follow-up call with
  `offset=500` would complete the set at the cost of 1 call.

**Implication for F0-S4 (client):** the client MUST read `X-Total-Count` after
each call and either (a) page with `offset` until `fetched == total`, counting
each page against the ledger, or (b) stop at the budget and record the gap in
the cache manifest so F4-S6 can surface it ("Inactive: 500/690 — 1 call to
complete") per D24/§5a. Silent truncation is not an option: a truncated pull
skews every downstream count and the 690 figure proves real pulls will hit the
ceiling. The query planner (F4-S1) should also prefer parameter splits that
keep expected result sets under 500 where predictable.

## 4. Anomalies and sanity checks

- **`daysOld=1:1095` respected:** oldest `listedDate` is 2023-08-09 (1,082
  days before the pull) — zero records outside the requested range. Year
  distribution: 2023: 5 · 2024: 129 · 2025: 240 · 2026: 165.
- **Zero duplicate addresses**, both within and across the Active/Inactive
  responses — dedupe (F4-S2) has nothing to do on this pull, but stays as a
  guard.
- **Price range** $750–$7,120, median $2,000, no impossible values. But $/sqft
  extremes flag likely bad data: `4623 S Whipple St` at $750 for a claimed
  2,231 sqft SF home ($0.34/sqft) and `619 W 46th St` at $1,800 for 3,175 sqft
  ($0.57/sqft) look like wrong sqft or partial-property rentals; top end
  `3854 S Albany Ave` $7,120 / 1,705 sqft ($4.18/sqft). This is direct
  evidence for F5-S1's verify-sqft flag (>~30% deviation from cohort median
  $/sqft).
- **Churn extreme:** `2453 W 46th Pl, Unit 1` has 5 history events between
  2025-10-27 and 2026-03-15 — a stress case worth copying into the synthetic
  fixture set (T-S1).

## Queue-story candidates (for the PM)

1. **Inactive fixture completion decision (owner call, 1 API call):** fetch
   `offset=500` for the Inactive broad query to un-truncate the canonical
   dataset (500/690 committed). Cheap now; impossible to know what the missing
   190 records skew until fetched. Alternatively, explicitly record "canonical
   fixtures are a 500/690 sample" wherever fixture-derived conclusions are
   drawn.
2. **F0-S4 scope confirmation:** X-Total-Count reading + offset pagination +
   manifest gap accounting is now evidence-required, not speculative (see §3).
3. **F4-S3 AC addition:** stitcher must merge contiguous history events
   (gap ≤ 1 day) into one spell with `cut_history`, and must synthesize a
   single spell from top-level fields when `history` is absent (44 real
   records). Both behaviors have concrete fixtures to test against.
4. **T-S1 synthetic fixtures:** copy in the 5-event churn record and one
   contiguous price-change chain as pathological cases.
