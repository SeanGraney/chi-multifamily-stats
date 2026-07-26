# RentComp — North Star

## Why this exists

Sean has two rental units to price and wants an evidence-based prediction of how long a unit will sit vacant at a given asking rent — built from real comparable listings in a data-thin Chicago small-multifamily micro-market, not from a third-party model's opinion standing in for evidence.

## The thesis that must never be silently violated

**Every number this tool produces must trace back to real, observed comps** — actual listings, actual removal/lease events, actual price history. Never to a vendor's derived estimate substituting for evidence. This is why `/listings/rental/long-term` (real records) is the pipeline's foundation and `/avm/rent/long-term` (RentCast's own model output) is excluded from it entirely. Pulling in `/avm` data would produce numbers that *look* like evidence but are actually a restatement of someone else's model — and would pass every existing numeric-range test while quietly gutting the reason this tool exists instead of just paying for RentCast's estimate directly.

Any change that would blur this line — a different data source, a redefined statistic, anything that changes what a number *represents* — is a semantic change, not an implementation detail. See `SEMANTIC_CHANGE_PROTOCOL.md`.

## What each output number must mean

| Output | Meaning | Must not be confused with |
|---|---|---|
| **Anchor** | Weighted median of drift-adjusted comp $/sqft × subject sqft — "what a similar unit would rent for today, given real recent comps adjusted for assumed market movement since they were listed." | A personalized prediction for the subject unit specifically (it's a market reference point). Never rendered without its sensitivity band — it depends on a manual assumption (drift). |
| **Premium** | A comp's own initial $/sqft relative to its *own cohort's* median $/sqft at the time it was listed — time-local, no drift math needed since the comparison is within the same listing year. | The comp's premium relative to *today's* market (that's what drift-adjustment is for). Premiums from different cohort years are not directly comparable without adjustment. |
| **Drift** | A manually set, annually-compounding assumption about market movement since a comp's listing year. | A data-derived growth rate — MVP scope is manual only. Always shown with its ±sensitivity band; never rendered as fact. |
| **Bucket boundary** | A premium-percentage cutoff (e.g. ±4%) translated to a live dollar figure via the current anchor. Groups comps by how aggressively they were priced relative to their own cohort. | An interpolated or smoothed curve. Buckets never interpolate; an empty bucket renders as a dash, never an estimate. |
| **Contribution %** | A selected comp's weight ÷ sum of selected weights — how much a comp is influencing the current analysis. | Statistical significance or confidence in that comp. |
| **KM curve / expected DOM** | A Kaplan-Meier survival estimate over the weighted set of comps nearest in premium to the candidate rent — an empirical, censoring-aware estimate built only from real observed outcomes. | A fitted/parametric model's output. Must never render when evidence is too thin — guard state instead (see below). |
| **Guard state** | An explicit "insufficient evidence" state that renders *instead of* a curve when there are fewer than 3 usable neighbors within ±3 premium points, or all neighbors are censored. | A curve with wide error bars — it isn't a curve at all. It's a different render, and the two are mutually exclusive by construction, not by convention. |
| **Censored** | A comp still active (not yet leased) as of the pull; its DOM-so-far is a *floor*, not an observed outcome. | Leased. This is the single most important distinction in the whole system — censored comps are never counted as leased in any statistic. |
| **Pending / provisional / confirmed** | A confidence ladder on whether a removed listing actually leased: pending (<7d off market, too recent to trust, excluded from stats) → provisional (≥7d, counted but marked) → confirmed (≥42d with no re-list, high confidence). | Each other, in aggregate stats. Pendings are *excluded*, not just marked — mixing them in silently inflates apparent lease velocity. |
| **Withdrawal-suspect** | A listing that appeared to complete its spell but re-appeared 6 weeks–6 months later — casts doubt on whether the original "lease" actually happened. | Grounds for automatic exclusion. Display-only flag; the human makes the call. |
| **Expected vacancy days / cost** | Area under the KM step function up to the last observable time — an expected value under a *truncated* empirical curve, always stated with its truncation point. | A guarantee, or an estimate valid beyond the data's actual observation window. |

## What this document is not

It doesn't redefine any formula — the exact math lives in `docs/rentcomp_functional_spec.md` §5 and the story ACs in `docs/rentcomp_technical_stories.md`. This document is the layer above those: it's what an agent (or the owner) checks a proposed change against when the question isn't "is this AC satisfied" but "does this still mean what we need it to mean."
