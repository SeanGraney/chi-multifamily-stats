# Semantic Change Protocol

## The rule in one sentence

**A change to *how* something is computed is the agent's call, logged. A change to *what a number means* is the owner's call, escalated.**

## Two kinds of requirement in the stories doc

- **[INVARIANT]** — a locked outcome, property, or definition. Not the developer's or QA's to revisit. If one seems wrong, that's a report to the PM, not a silent change.
- **[DEFAULT: ...]** — a suggested implementation. Deviate freely with a good reason; log a one-line rationale in your handoff note (`WORKFLOW.md`). No escalation needed — *as long as every invariant in the story still holds after the swap.*

## The gray zone — when a "default" swap is secretly a semantic change

Some implementation choices that look like defaults are actually protecting an invariant underneath. Four worked examples:

1. **kNN retrieval library (F11-S1) — a safe default swap, with a trap next to it.** The default is hand-rolled `numpy.argsort`. Switching to a different sort/selection method, or even a well-chosen library, is a legitimate agent's-call swap — *as long as* the invariant holds: distance uses premium only, user weights never enter distance, only aggregation. Using `scikit-learn`'s `KNeighborsRegressor` with its own `weights` parameter would violate that invariant even though "use a library" sounds like a small convenience — its weights distance-weight *predictions*, ours are aggregation-only. This is why D19 rejects it specifically, not libraries generally.

2. **RentCast endpoint swap — the canonical bad example.** Swapping `/listings/rental/long-term` for `/avm/rent/long-term` anywhere in the pipeline would look like simplification (one call instead of many, always-available data) and might pass every existing numeric-range AC — but it silently replaces observed evidence with RentCast's own model output, corrupting what "premium," "anchor," and every downstream number *mean* (see `NORTH_STAR.md`). No test catches this. It always requires the 5-question analysis below and owner sign-off, no exceptions.

3. **A well-justified supplement — the canonical good example.** Proposing to pull `/markets` data as an *additional* input informing (not replacing) the drift index — e.g., using aggregate rent-growth stats to suggest a data-driven starting point for the currently-manual drift slider — is exactly the kind of change this protocol should *enable*. It doesn't substitute a model's opinion for comp evidence; it augments a currently-arbitrary manual input with real aggregate data, and the manual slider + sensitivity band stay as the safety net regardless. Still requires the write-up (it changes what "drift" is derived from) — but the answer might well be yes.

4. **Kaplan-Meier implementation (F11-S2) — same algorithm, different code, no escalation.** Restructuring the numpy implementation (vectorizing differently, caching intermediate risk sets) is a default swap — same algorithm, same meaning, still verified against `lifelines`. Substituting a *different* survival estimator entirely (e.g., a parametric Weibull fit instead of the nonparametric Kaplan-Meier product-limit estimator) would not be — different assumptions, different meaning for the resulting curve, even though "estimate expected vacancy" sounds like the same task either way.

## The 5-question semantic-impact analysis

Required whenever a proposed change reaches the owner:

1. What does this number mean today?
2. What would it mean after the change?
3. What new bias does this introduce, if any?
4. Does this serve the goal (a defensible, evidence-based prediction) or risk misleading it?
5. Recommendation — and what evidence would change your mind.

## Escalation path

Agent (dev or QA) drafts the 5-question write-up → flags it to the PM → **the PM does not adjudicate meaning** (that's fitness; only the owner judges fitness, per `PROJECT_MANAGER.md`) → PM relays the write-up verbatim to the owner and holds the story → owner decides → PM records the decision and rationale in `QUEUE.md`'s log, so there's a durable record of why the product looks the way it does → dispatch resumes per the decision.

## What doesn't need this protocol

Bug fixes. Performance improvements that don't change output values. Refactors. UI polish. Adding tests. Making an already-invariant-locked constant configurable — *unless* the default value would itself change without an explicit decision to change it.
