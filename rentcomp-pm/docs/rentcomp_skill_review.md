# RentComp — Multi-Lens Review

The three project docs (functional spec, epics, technical stories) reviewed through three PM skill lenses: **write-spec** (PRD completeness audit), **product-brainstorming** (assumption stress-test), and **roadmap-update** (build order + MVP/V2 split audit). Findings only — nothing here rewrites the docs; the closing action list says what should change where.

---

## Lens 1 · Spec audit (write-spec)

Held against PRD structure: problem statement ✓ · user stories ✓ (epics doc) · requirements w/ acceptance criteria ✓ (technical stories) · open questions ✓ (§3.4, properly tagged). Three real gaps:

**1. No success metrics — the biggest omission.** The spec never defines how we'd know RentComp *worked*. For this tool the honest metrics are:

- **Prediction accountability (lagging, the one that matters):** at decision time, log `{candidate ask, predicted vacancy band, bucket, neighbor set}`. When the unit actually leases, compare. Success = actual DOM inside the predicted band; ask held without a cut. Two units/year is a tiny sample, but over turnovers it's the *only* feedback loop the tool has — without it, RentComp can be confidently wrong forever and never learn.
- **Leading:** pricing decision reached in one evening per unit; ≥15 usable comps per pull; guard trip rate <50% across the price range actually being considered (if the guard trips everywhere, the pull was too thin to decide with).

**2. No explicit non-goals.** The v2 list defers features; non-goals *exclude* them. Proposed: not an automated pricer (tool informs, user decides — by design, not deferral) · not a rent maximizer (objective is expected-vacancy-adjusted revenue, not peak rent) · not multi-market (Chicago small-multifamily assumptions are baked into property types and stitching heuristics) · not multi-user (single-user caching/settings assumed everywhere) · no amenity modeling ever at this sample size (eyes + weights, permanently).

**3. No timeline anchor.** The real deadline is external: the tool must be usable before the next unit lists. That date should be written down and drive scope cuts — a working pipeline with an ugly UI prices a unit; a beautiful UI with an unverified stitcher doesn't.

---

## Lens 2 · Assumption stress-test (product-brainstorming)

Assumptions ranked by (impact if wrong × current evidence):

**A1 — KILLER: RentCast has the data.** Everything assumes the pull returns enough small-multifamily listings near the subject, with usable history, 2+ years back. Current evidence: **zero live calls made.** If a real June-window pull yields 4 comps, the product premise fails regardless of build quality. *Cheapest test:* 3–4 real queries (~10 API calls) — this is why the verification harness must run **before committing to the build**, not merely as sprint-1-story-1. It's a go/no-go gate, and if it fails the fallback is a redesign (wider radius, wider window, or a different data source), not a bugfix.

**A2 — SIGNIFICANT, partially unhandled: removal ≈ leased.** The pipeline treats every removal as a lease. Withdrawals (landlord gives up, pulls listing, re-lists next season beyond the 21d stitch threshold) are recorded as leases at whatever DOM — and withdrawals concentrate in exactly the above-market bucket where accuracy matters most, biasing it optimistic. *Proposed mitigation (cheap, recommend pulling into MVP):* a **withdrawal-suspect flag** — a removed spell followed by a new spell at the same unit within ~6 months (beyond stitch threshold) is marked "removed, re-listed later — lease uncertain" on the row and in bucket stats. Consistent with the honesty invariant; the user judges.

**A3 — MODERATE: sqft values are correct.** Missing sqft is handled; *wrong* sqft (common in small-multifamily records sourced from assessor data) silently corrupts premiums. *Mitigation:* show $/sqft on every row and flag comps whose $/sqft deviates >~30% from cohort median as "verify sqft" — the Zillow link is the verification path.

**A4 — ACCEPTED: premium→DOM relationship is stable across years.** Pooling 2024 comps with 2026 assumes the market punishes overpricing similarly across rate environments. Untestable at this n; mitigated by cohort-year visibility on every card. Named and accepted.

**A5 — MITIGATED: manual drift is close enough.** The sensitivity band is the mitigation; adequate for MVP.

**Devil's advocate, strongest case against building:** "Two units, priced once a year — a Zillow session and a spreadsheet gets 80% of this." The honest counter is specific: the two things eyeballing Zillow *cannot* give are the initial ask of leased comps (Zillow shows final price — you never see that the $2,400 listing actually started at $2,600) and true DOM (re-list laundering resets Zillow's counter). Those two corrections are the product. Verdict: build stands, and the MVP-lean scope is the right hedge against the effort side of this argument.

---

## Lens 3 · Roadmap audit (roadmap-update)

**Build order (spec §8) restated as Now/Next/Later with dependency check:**

| Phase | Items | Dependency verdict |
|---|---|---|
| **Gate** | Verification harness (F4-S7/T-S3) + A1 go/no-go | Knowledge dependency — correctly precedes everything; elevate from "sprint 1 item" to **pre-commitment gate** |
| **Now** | F0 foundations → F4 pipeline → F5/F6/F7 curation → F8 anchor | Correct: derivation graph (F0-S2) is the deepest dependency; pipeline before any UI that displays its output |
| **Next** | F10 buckets → F11 price test → F3/F13 cache+refresh → F12 links | Correct: analysis consumes curation state; guard (F11-S3) ships with the curve, never after |
| **Later (V2)** | auto-drift, AVM benchmark, AFT, two-price, CSV, side-by-side | One re-order recommended below |

**Three findings:**

1. **Add a walking-skeleton milestone.** Between the gate and full F5–F8: one hardcoded search flowing end-to-end (pull → stitch → plain list → anchor → one bucket table → price test, zero styling). It proves every integration seam early, and — given the external deadline from Lens 1 — it's the artifact that can price a unit if time runs out. The build order as written is layer-by-layer; this adds a vertical slice first.

2. **V2 queue re-order:** the **prediction-accountability log** (new, from Lens 1) should be first in V2 — arguably last-in-MVP, since logging a JSON blob at decision time is trivial and the data is unrecoverable if not captured from the first real pricing decision. Everything else in V2 stays ordered as-is.

3. **Zero-sum adjustment.** Lenses 1–2 propose three MVP additions (withdrawal-suspect flag, sqft-outlier flag, decision log). Roadmaps are zero-sum, so the offered trade: **move per-bucket mini-KM sparklines (F10-S3) to V2.** Bucket stat rows plus the main price-test KM carry the decision; the sparklines are corroboration. Net scope change ≈ zero, honesty coverage strictly better.

---

## Consolidated actions

| # | Action | Doc affected | Size |
|---|---|---|---|
| 1 | Run verification harness as pre-commitment **go/no-go gate** (A1) | stories (reframe T-S3) | ~10 API calls |
| 2 | Add success-metrics section: decision log + leading indicators | spec | small |
| 3 | Add explicit non-goals section | spec | small |
| 4 | Write down the external deadline (next unit listing date) | spec | one line |
| 5 | Add withdrawal-suspect flag story (A2) | stories, new F4-S8 | small |
| 6 | Add sqft-outlier flag story (A3) | stories, amend F5-S1 | small |
| 7 | Add prediction-accountability log story | stories, new F11-S6 (or first V2) | small |
| 8 | Add walking-skeleton milestone to build order | spec §8 | reorder only |
| 9 | Move F10-S3 (mini-KM sparklines) to V2 | spec + stories | reorder only |

Net effect: no material scope growth, one new gate, and the three docs close their real gaps — a success definition, a withdrawal blind spot, and a missing feedback loop.
