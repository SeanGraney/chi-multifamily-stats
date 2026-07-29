# Semantic Change write-up — `contribution_share` claims influence a comp does not have

**Queue row:** 14a · **Raised by:** F5-S2 QA, in passing, on real data · **Drafted by:** QA subagent, 2026-07-28
**Status:** owner decision required. The PM does not adjudicate meaning (`SEMANTIC_CHANGE_PROTOCOL.md`).
**Evidence base:** the committed real pull `pull_ref="ws1-real"` (`fixtures/live-samples/`, 567 stitched comps).
Fixture mode only, zero live calls, ledger untouched at 8/50. No product code was changed.

---

## Plain-English summary (for the owner — you can stop after this)

Next to every comp's weight box, the tool shows a percentage that reads as "how much this comp matters
to your answer." It is actually just a share of the weights you typed: your weight divided by the sum
of everyone's weights. Those are not the same thing, and the gap is bigger than the bug report suggested.

Here is a real nine-comp example from your own Chicago pull. All nine show **11.1%**. The anchor is
$1,926. If you delete each comp one at a time and re-price, five of the nine change the anchor by
**exactly $0.00** — and one of those five is a comp with no square footage that could never have counted
at all. Four comps move it by $128. So the tool showed you nine identical 11.1%s over a set where the
true influence was $0, $0, $0, $0, $0, $128, $128, $128, $128. The missing-square-footage comp is not a
special case; it is just the one instance where the falsehood is provable from the payload itself, because
the tool also tells you elsewhere that it counted only 8 comps in the anchor.

The number is not wrong at arithmetic. It is wrong at labelling. The honest reading of it is "**this is
how much of your weighting this comp holds**" — a check on your own curation, exactly as the functional
spec describes it ("so the user can see when one comp dominates"). The dishonest reading is the one
`NORTH_STAR.md` currently prints: "how much a comp is influencing the current analysis."

My recommendation is the least glamorous of the options on the table: **do not change the number — change
what it is called**, on the screen and in the wire contract (`contribution_share` → `weight_share`), and
correct NORTH_STAR to say what it actually is. Changing the number (option A) fixes only the missing-sqft
comp and would leave four perfectly good comps still claiming 12.5% influence they do not have — and it
would make the number *more* believable while it stays *just as* wrong. There is a version of this where
we compute the real influence (option F) and it is genuinely appealing, but it is a new statistic, it does
not fit inside the current story, and a median's true influence is a lumpy "$0 or $128" figure rather than
a percentage. Separately, I found one thing I think is a real defect and not a matter of taste: the
planned "you need 5 comps before you can run the analysis" gate counts comps that carry no evidence — five
no-sqft comps open the gate onto an analysis with no anchor at all. That should be fixed on its merits,
whichever way you decide on the percentage.

---

## 1. The finding, reproduced exactly

Reproduced on `ws1-real` at the shape QA reported. Curate down to 8 sqft-bearing comps, then give one
missing-sqft comp an explicit weight of 1:

```
the ghost comp: 1025 w 31st pl|2|2024-09-13
  state=included  weight=1.0  contribution_share=0.1111
  sqft=None  psf=None  premium=None  bucket=None

anchor.rent.mid  $1,926.00   (identical before and after weighting it in)
anchor.n_comps   8           (breakdown.included = 9)
```

Two details the original report did not carry, both of which matter:

1. **It also corrupts the eight honest comps.** Before the ghost is weighted in, each of the eight reports
   `0.1250`. After, all nine report `0.1111`. So the defect is not one wrong row — a comp that contributes
   nothing silently *understates* the influence of every comp that does, by 11%.
2. **The anchor does not move at all.** `psf.mid` is bit-identical either way. The claim of influence is
   contradicted by the very same payload.

---

## 2. Evidence

### 2.1 How often is this reachable on real data?

| Measure on `ws1-real` | Count |
|---|---|
| Comps in the pull | 567 |
| **Comps with no `squareFootage`** | **83 (14.6%)** |
| Included at default weights | 484 |
| Anchor evidence (`anchor.n_comps`) | 484 |

**Not a 1-comp curiosity — an 83-comp hazard.** If a user weighted all 83 up (deliberately, one at a time
— the `ALL` bulk action does not do it, per the PM's F5-S2 ruling), they would collectively hold
**14.6% of the total stated contribution** while changing nothing: anchor `psf.mid` identical, `n_comps`
still 484, every bucket count identical (201/73/210), every cohort `selected_count` identical, expected
vacancy days identical at 40.71, and **zero** of them entering the kNN neighbour set.

### 2.2 Who else consumes `contribution_share`?

Traced every reference in the repo. The complete consumer list today:

- `backend/src/rentcomp/pipeline/weights.py::contribution_shares` — producer.
- `backend/src/rentcomp/pipeline/derive.py` — assigns it to `DerivedComp`.
- `backend/src/rentcomp/models/responses.py::DerivedComp.contribution_share` — the wire field.
- `frontend/src/api/schema.d.ts:379` — the generated type, **and nothing else in `frontend/src` reads it.**
  `Results.tsx` is the WS-1 walking skeleton and has no weight UI at all.
- Tests: `test_derive_contract.py`, `test_derive_dev.py`, `test_derivation_graph*.py`, and F5-S2's
  `test_f5s2_selection_weight.py` (on `story/F5-S2-qa`).

**It does not feed any summary.** The spec puts it on the comp row directly beside the weight input
(§6.4 layout, §5.1 "next to the weight so the user can see when one comp dominates"); F9-S1's breakdown
panel is specified over *counts* (included/censored/excluded/filtered + per-cohort), not over shares.
ADR-001 §5 forbids the view from combining two response fields arithmetically, so it cannot be
re-aggregated client-side either. **This is the containment that makes a rename sufficient**: the number
is only ever read within eyeshot of the weight it is a share of. It is also why this decision is cheap to
make *now* and expensive to make after F5-S1/F9-S1 render it.

### 2.3 Is `anchor.n_comps` the only observable trace?

No — the same payload contradicts the 11.1% in **seven** independent places:

| # | Cross-check on the 9-comp payload | Value |
|---|---|---|
| 1 | `breakdown.included` vs `anchor.n_comps` | 9 vs **8** |
| 2 | `anchor.comp_keys` contains the ghost | **False** |
| 3 | `Σ bucket.count` / any bucket lists it | 8 / **False** |
| 4 | `Σ cohort.selected_count` / any cohort lists it | 8 / **False** |
| 5 | The comp's own `premium` / `bucket` | `None` / `None` |
| 6 | `breakdown.missing_sqft` lists it | True (83 total) |
| 7 | `price_test.neighbors` contains it | **False** |

Plus an unconditional `missing_sqft` warning in `warnings[]`. The payload is honest everywhere except in
this one field. That is worth stating plainly: **the pipeline already knows the truth, and publishes it —
`contribution_share` is the single field that disagrees with the rest of its own response.**

### 2.4 The question that reframes the decision: is missing-sqft one instance of a general class?

**Yes, and the general class is not fixable by changing the denominator.** Measured on `ws1-real`
(484 included comps at default weights):

| Class | Included comps that contribute **nothing** to it | Their summed stated share |
|---|---|---|
| **Anchor / cohort medians** (needs `psf`) | 83 if weighted in | 14.6% |
| **Bucket outcome stats** (leased only; censored + pending excluded entirely) | **38** (33 censored, 5 pending) | **7.85%** |
| **KM curve / expected vacancy days / expected vacancy cost** (kNN neighbours only) | **463 of 484** | **95.7%** |

The last row is the one that matters. Twenty-one comps enter the price test; 463 included comps —
carrying 95.7% of the stated contribution — have **zero** influence on expected vacancy days, the number
this product exists to produce. They are not defective comps; they are simply not near the candidate in
premium space. The `[INVARIANT]` formula is doing exactly what it says, and the gloss is false for
almost every comp in the set, for reasons that have nothing to do with square footage.

**And it is false even for a fully-qualified comp, because the anchor is a weighted *median*.** On the
curated 8-comp set, raising the lowest comp's weight from 1 to 2:

```
weight=1  share 0.1250   anchor psf.mid 1.605000  rent.mid $1926.00   moved=False
weight=2  share 0.2222   anchor psf.mid 1.605000  rent.mid $1926.00   moved=False   <-- share x1.8, effect x0
weight=3  share 0.3000   anchor psf.mid 1.480000  rent.mid $1776.00   moved=True
weight=8  share 0.5333   anchor psf.mid 1.000000  rent.mid $1200.00   moved=True
```

Weight acts on a median through *rank*, not proportionally. Doubling the stated share moved the anchor by
$0. (On the full 484-comp set the distribution is dense enough that small weight bumps usually do move the
median — but the saturation is still there: weight 100 → share 0.1715 → psf 1.76235; weight 1000 → share
0.6743 → psf **1.76235**, unchanged.)

**The decisive measurement — leave-one-out on the reported 9-comp set:**

```
comp                                    stated share   actual effect on anchor
1025 w 31st pl|2|2024-09-13  (no sqft)      0.1111            +$0.00
1315 w 31st st|2                            0.1111          +$128.40
1315 w 31st st|b                            0.1111          +$128.40
1317 w 31st pl|1fl                          0.1111            +$0.00
1317 w 31st st|1fl                          0.1111            +$0.00
1320 w 50th st|                             0.1111            +$0.00
1320 w 50th st|2                            0.1111          +$128.40
1343 w 31st st|1                            0.1111            +$0.00
1347 w 47th st|2e                           0.1111          +$128.40
```

Five of nine comps have exactly zero influence on the anchor. **Four of those five have square footage
and are impeccable evidence.** By the property the gloss promises, the missing-sqft comp is
*indistinguishable* from four perfectly good comps.

> **This is the finding.** Missing-sqft is not the disease; it is the one symptom that happens to be
> provable from the payload. `weight ÷ Σ weights` is a share of a *mean*'s influence, and this product
> computes medians and a Kaplan-Meier product-limit estimator. Any resolution that only repairs the
> missing-sqft case repairs the visible 1/9 of the problem and leaves the other 4/9 in place — while
> making the number look audited.

### 2.5 A separate defect found while probing (flagging, not folding in)

`breakdown.included` is the count F5-S3's analysis gate is specified against ("disabled with reason below
5 included comps"). Weight five missing-sqft comps to 1 and nothing else:

```
breakdown.included = 5      -> the F5-S3 gate would OPEN
anchor             = None
price_test         = None
warnings           = [... 'missing_sqft', 'no_anchor']
```

The gate opens onto an analysis with no anchor and no price test. There is a `no_anchor` warning, so the
screen would not be silently wrong — but the gate is counting the wrong thing. **Recommendation: F5-S3
should gate on evidence (`anchor is not None`, or `anchor.n_comps >= 5`), not on `breakdown.included`.**
That is a story-level fix, not a semantic change, and it is worth making whichever way row 14a is decided.
It is also the one thing resolution (C) would fix as a side effect — see §3.3.

---

## 3. The five-question analysis, per resolution

The five questions are `SEMANTIC_CHANGE_PROTOCOL.md`'s, verbatim, applied to each candidate separately.

### 3.1 (A) Change the number — compute the share over the comps that actually enter the aggregate

*A comp contributing nothing reports 0 (or `None`).*

**1. What does this number mean today?**
`weight ÷ Σ weights of the comps labelled `included``. A statement about the user's weight vector,
restricted to the set the user has not zeroed or filtered away. Nothing else.

**2. What would it mean after the change?**
"This comp's weight as a fraction of the weights *of the evidence behind one named aggregate*." The change
forces a question it has no principled answer to: **which** aggregate? There are four, with four different
evidence sets — anchor/cohort medians (`included ∧ psf≠None`), bucket membership (`included ∧ premium≠None`),
bucket outcome statistics (further restricted to `removal_class ∈ {provisional, confirmed}`), and
kNN/KM/expected-vacancy (`included ∧ premium≠None ∧ among the k nearest at each drift edge`). If the answer
is "the anchor", the number silently becomes an anchor-specific statistic sitting on a row whose other
fields (bucket, outcome, DOM) belong to other aggregates. If the answer is "all of them", 463 of 484 comps
report 0 and the field becomes noise.

**3. What new bias does this introduce?**
The dangerous one: **a credibility bias**. The number would become *demonstrably right in the one case a
user can check* (the no-sqft comp reads 0, and `anchor.n_comps` agrees) while remaining wrong in the four
qualified-comp cases the leave-one-out table exposes — which a user cannot check, because the tool does not
publish leave-one-out. Today the field is uniformly a weight share and a careful reader can learn to
discount it; after (A) it is a weight share *dressed as* an influence measure, with one audited case
vouching for it. That is a worse honesty position than the status quo, not a better one.
Secondary: a `0.0` on a comp the user deliberately weighted to 1 reads as "the system overrode me",
which is precisely the outcome `pipeline/weights.py`'s docstring rejects ("the user's intent is echoed back
rather than overridden silently"). `None` avoids that but then means two different things in the same field
(`None` = not selected, and `None` = selected but contributes nothing).

**4. Does this serve the goal or risk misleading it?**
Mixed, and net negative. It removes one false statement and strengthens the four it leaves behind.
NORTH_STAR's thesis is not "no number may be provably false" — it is that a number must mean what it says.
(A) makes one instance unprovable rather than making the number true.

**5. Recommendation.**
**Do not take (A) on its own.** It is the *larger* change in meaning (it redefines a `[INVARIANT]`-tagged
formula and an output the user reads) for the *smaller* fraction of the actual problem. If the owner wants
the number to be about influence, the honest form of that wish is (F), not (A).
*Falsifier:* if the owner's judgement is that the no-sqft case is uniquely intolerable because it is the one
case where the payload self-contradicts — i.e. the objection is to *provable* falsehood specifically, not to
misleadingness in general — then (A) is exactly the right, targeted fix and my objection is over-reach.

---

### 3.2 (B) Change the gloss — ratify "share of your stated curation intent"

*A statement about the user's weighting, not about influence on the anchor. Correct NORTH_STAR's wording.*

**1. What does this number mean today?**
As §3.1. Note that this is *already* what the code says: `weights.py`'s docstring calls it "the curation
half of the graph", and the functional spec §5.1 states its purpose as "so the user can see when one comp
dominates" — a curation-hygiene signal. **NORTH_STAR's row is the outlier**, not the implementation.

**2. What would it mean after the change?**
Unchanged. Every byte of every response is identical. What changes is that the documentation stops
promising a property the number never had. The NORTH_STAR row would read, roughly:

> **Contribution %** — a selected comp's weight ÷ sum of selected weights: **the share of the user's own
> weighting that this comp holds**, so a dominating weight is visible. *Must not be confused with:* how
> much the comp moves the anchor (weights act on a median through rank, and a comp with no $/sqft is
> weighted but contributes to no median at all), statistical significance, or confidence.

**3. What new bias does this introduce?**
None in the data. One in the process, and it should be named honestly: ratifying a gloss to match an
implementation is the move that *would* be dangerous if applied habitually — it is how a spec erodes. The
defence here is that the pre-existing artefacts (spec §5.1, `weights.py`, ADR-001 §1.1) all already say
the narrower thing, and only NORTH_STAR says the broader one; this is correcting the outlier, not
retreating to the code.

**4. Does this serve the goal or risk misleading it?**
It serves it *only if the user reads the gloss*. He will not: he will read the field name and the column
header while curating. **This is why (B) is necessary but not sufficient** — see (E).

**5. Recommendation.**
**Take (B), together with (E).** It is unambiguously the **smaller change in meaning** — it is *zero* change
in meaning; it is a change in description. (It is also the smaller change in code, which is a coincidence,
not an argument.)
*Falsifier:* if the owner reads the current NORTH_STAR row and says "no, that gloss is what I want the number
to be, the doc is right and the code is wrong," then (B) is off the table entirely and this becomes a
build-(F) decision.

---

### 3.3 (C) Change the membership — a missing-sqft comp with an explicit weight is not `included`

**1. What does this number mean today?**
As §3.1. `included` today means "the user has not zeroed this comp and no filter removed it" — a statement
about *curation state*.

**2. What would it mean after the change?**
`included` would become "curated in **and** carrying a $/sqft" — a statement about curation *and evidence*,
conflated. `contribution_share` would inherit the fix for free (the no-sqft comp gets `None`), which is why
it looks attractive. But three things break or fail to generalise:

- It **contradicts a stated product requirement**: the F5 epic edge (`rentcomp_epics_mvp.md:114`) and spec
  §4 both say "missing-sqft row → excluded by default, manual re-include **allowed**". F5-S2's QA suite
  pins it: `test_a_missing_sqft_comp_starts_excluded_and_can_be_re_included_by_hand` asserts
  `comp["state"] == "included"` after an explicit weight. (C) is not a semantic tidy-up; it is a
  requirement reversal, and would need its own owner decision on top of this one.
- It **does not generalise**. Censored comps, pending removals and out-of-kNN comps are legitimately
  `included` and legitimately contribute nothing to specific aggregates (§2.4: 38 and 463 comps
  respectively). (C) cannot follow them without dissolving `included` altogether.
- It **breaks the F7-S1 partition's meaning**: `included + excluded + filtered == pulled` would still hold
  arithmetically, but "excluded" would now mean two different things (user-zeroed, and system-disqualified)
  with no way for the row to tell the user which — the exact silent override `weights.py` avoids.

**3. What new bias does this introduce?**
It removes the user's ability to state an intent the system disagrees with. That is a real loss: a user who
knows a listing's sqft from a Zillow check (spec §6.4 names the Zillow deep link as the verification path)
has a legitimate reason to weight it, and F5-S1 will eventually let him supply the sqft. (C) would make the
weight box lie back at him.

**4. Does this serve the goal or risk misleading it?**
It serves *one* goal well — it is the only option that fixes §2.5's analysis-gate defect as a side effect,
because the gate counts `breakdown.included`. But that gate should be fixed at the gate.

**5. Recommendation.**
**Do not take (C).** Take its one genuine benefit directly instead: **make F5-S3's gate count evidence
(`anchor.n_comps`) rather than `breakdown.included`.** That is a story-level correction requiring no
semantic ruling.
*Falsifier:* if the owner decides that "manual re-include of a no-sqft comp" is a mis-specified requirement
he does not actually want (it buys nothing until F5-S1 lets him enter the missing sqft), then (C) becomes
coherent — but it is then a *requirements* change to F5/spec §4, and should be decided as one.

---

### 3.4 (D) Surface the discrepancy — render the conflict visibly

*The row shows both its weight-share and that it contributes to no median.*

**1. What does this number mean today?** As §3.1.

**2. What would it mean after the change?** Unchanged on the wire. The row would read something like
`[wt: 1.0] [11.1% of weight] [no sqft — in no median]`.

**3. What new bias does this introduce?**
None, but note what (D) actually costs and what it actually covers. The data it needs **already exists** in
the payload for the missing-sqft case: `psf=None`, `premium=None`, `bucket=None`, membership absent from
`anchor.comp_keys` / every `bucket.comp_keys` / every `cohort.comp_keys`, plus the `missing_sqft` warning
and the `no sqft` badge F5-S1 already specifies. So for missing-sqft, (D) is **already funded** — it is a
render, and F5-S1's badge is most of it. For the general class it is not funded: showing "contributes to
no leased statistic" (38 comps) or "not among the price test's neighbours" (463 comps) would require new
payload fields, and rendering "not a neighbour" on 96% of rows is noise, not honesty.

**4. Does this serve the goal or risk misleading it?**
Serves it, partially, at zero cost for the one case — and cannot scale to the general case.

**5. Recommendation.**
**Take (D) only in its already-funded form**: F5-S1's `no sqft` badge sits on the same row as the share, so
a user seeing 11.1% next to `no sqft` has the contradiction in one glance. Do not build new fields for it.
(D) is a mitigation, never a resolution — it leaves the number saying the false thing and asks the user to
notice.
*Falsifier:* if leave-one-out (F) turns out to be cheap and stable enough to ship, (D) becomes redundant.

---

### 3.5 (E) Change the name — `contribution_share` → `weight_share` *(added; not in the PM's list)*

*(B) applied to the wire contract and the column header, not just to a document.*

**1. What does this number mean today?** As §3.1. The word "contribution" is the whole problem: in this
codebase "contributes" has a precise, load-bearing meaning — `cohorts.py`: "a comp with no `squareFootage`
cannot contribute to a $/sqft median"; `weights.py`: "silently weighting a comp that contributes nothing";
F5-S2 QA: "a share of an analysis a comp is not part of". The field name asserts exactly the thing the
codebase elsewhere denies.

**2. What would it mean after the change?** Identical arithmetic, accurate label. `weight_share` cannot be
misread as an evidence claim; it is self-evidently a share of the weights. The NORTH_STAR row (B) then
matches the field the user actually sees, and the "amber above 40%" rule keeps working unchanged and keeps
meaning what it was designed to mean ("one comp dominates your weighting" — spec §5.1's stated purpose).

**3. What new bias does this introduce?** None. Zero output values change. The one cost is a wire-contract
rename: `models/responses.py`, an `openapi-typescript` regeneration of `schema.d.ts`, and the field name in
~6 test files. **The rename has been done once already on this field** (`contribution_pct` →
`contribution_share`, PM ruling 2026-07-27) and the tests that policed it are still in place and reusable
(`test_the_contribution_field_is_named_share_not_pct`), so the mechanics are known and cheap.

**4. Does this serve the goal or risk misleading it?**
It serves it in the most direct way available: it makes the honest reading the *only* available reading at
the point of use, which is where the user actually is. NORTH_STAR's thesis is about numbers not stating
things the evidence does not support — a number that never claims influence cannot overstate influence.

**5. Recommendation.** **Take (E) with (B). This is my recommendation.**
*Falsifier:* if the field will ever feed a summary rather than sit beside its own weight — a "your top 3
comps carry 60% of the analysis" line in F9-S1, or a CSV export — a rename is not enough, because the
aggregate would re-import the influence reading. Today it feeds nothing (§2.2) and F9-S1 is specified over
counts. **If F9-S1's design brief acquires such a line, reopen this.** Timing matters: this is cheap now,
before F5-S1 renders it and F9-S1 is designed, and expensive after.

---

### 3.6 (F) Compute the real thing — leave-one-out influence *(added; not in the PM's list)*

*Report each comp's actual effect on the anchor: recompute without it, show the $ delta.*

**1. What does this number mean today?** As §3.1.

**2. What would it mean after the change?** Exactly what NORTH_STAR's current gloss promises: "how much
this comp is influencing the current analysis," measured rather than assumed. §2.4's table is a working
prototype — `$0.00 / $128.40` per comp, computed with nothing but the existing pipeline.

**3. What new bias does this introduce?**
Three, all real:
- **Leave-one-out on a median is lumpy, not proportional.** The honest answer is "$0 or $128", not a
  percentage. Five of nine comps read $0.00 — a user could reasonably conclude those comps are worthless
  and delete them, when in fact they are holding the median's rank in place and deleting them *would*
  move it. A jackknife on an order statistic is a genuinely tricky number to render without creating a
  new misreading. That risk is not hypothetical: it is the same shape as the WS-1a `too_few_in_range`
  guard that was true-by-formula and false-by-meaning.
- **Which aggregate again?** Influence on the anchor ≠ influence on expected vacancy days. Honestly done,
  this is 2–4 numbers per comp, not one.
- **Cost.** A full-derive leave-one-out over 484 included comps is ~3.2 s measured (6.6 ms × 484), which
  blows F5-S2's 150 ms debounce budget outright. An anchor-only jackknife is far cheaper and an
  order-statistic sensitivity is O(n log n) — feasible, but it is new math to specify, test and defend, and
  D19/D20 discipline applies.

**4. Does this serve the goal or risk misleading it?**
It is the only option that makes the *current* NORTH_STAR gloss true rather than retracting it, and on those
grounds it is the most ambitious answer to "does this serve a defensible, evidence-based prediction". It is
also the only option that could mislead in a *new* way.

**5. Recommendation.**
**Not now — but log it as the V2 story this write-up recommends creating**, and note that (B)+(E) are a
strict prerequisite either way: if a real influence number is ever built, it needs a name, and
`contribution_share` should be free for it. Taking (E) now *reserves* the honest name for the honest number.
*Falsifier:* if the owner says the influence question is the one he actually wants answered while curating —
"which of these comps is actually driving my price?" — then (F) stops being V2 and becomes the point, and
(B)+(E) become the interim step rather than the resolution.

---

## 4. Recommendation, in one line

**Take (B) + (E)** — correct NORTH_STAR's gloss *and* rename the field to `weight_share` on the wire and on
the row — **decline (A) and (C)**, accept (D) only in the form F5-S1's `no sqft` badge already provides, and
**open two follow-ups**: F5-S3's gate should count evidence not `breakdown.included` (§2.5), and a V2 story
for measured influence (§3.6).

**(A) vs (B), asymmetry stated plainly, as asked:** **(B) is the smaller change in meaning by a wide
margin** — it is a change of *zero* meaning, because it aligns the description with what the number has
always been and what the spec, the ADR and the implementation all already say. (A) is the larger change in
meaning: it redefines an `[INVARIANT]`-tagged formula and alters a number the user reads. That (B) is also
the smaller change in code is incidental, and should not be part of the argument.

**What would change my mind, in priority order:**

1. The owner reads the current NORTH_STAR row and says *that* is the number he wants. → the decision becomes
   (F), and (B) is off the table.
2. `contribution_share` acquires a consumer that aggregates it away from its own weight row (an F9-S1
   summary line, an export, a warning threshold computed over several comps). → a rename stops being
   sufficient; revisit (A) or (F).
3. The objection is specifically to *provable* self-contradiction inside one payload — not to
   misleadingness in general. → (A) is correctly targeted and my §3.1 objection is over-reach.
4. Evidence that weight share *is* a good proxy for anchor influence across realistic curated sets — i.e.
   that my leave-one-out table is an artefact of one 9-comp selection. I do not believe it is (the mechanism
   is the median's rank behaviour, not this sample), but a systematic sweep across many curated sets would
   settle it, and I would want that sweep run before anyone leans hard on the "$0.00 for four good comps"
   line in front of the owner.

---

## 5. Test impact (for whoever implements the decision)

- **(B)** — documentation only. No test moves.
- **(E)** — a wire rename. `test_the_contribution_field_is_named_share_not_pct`
  (`test_derive_contract.py:558`) and `test_the_weights_module_exports_contribution_shares_not_pcts`
  (`test_derivation_graph.py:537`) are the two guards that pin the *current* name; they were written for the
  previous rename and would be updated in place, not weakened. `schema.d.ts` must be regenerated — note the
  PM's standing instruction that F2-S1's dev owns that file right now; sequence accordingly.
- **(A)** — F5-S2's `test_contribution_share_is_weight_over_the_sum_of_included_weights` **survives** as
  written (its recipes only weight `sqft_bearing_keys`, so no no-sqft comp is ever `included` in it), which
  is what the PM was told and it checks out. But `test_payload_carries_every_quantity_the_view_would_
  otherwise_compute` (`test_derive_contract.py:533`, "every included comp has a non-`None`
  contribution_share") would need a ruling, and `contribution_shares()`'s own docstring — "`None` for comps
  that are not contributing at all — a share of an analysis a comp is not part of is not 0%, it is
  undefined" — would become the specification of the new behaviour rather than a description of the old.
- **(C)** — breaks `test_a_missing_sqft_comp_starts_excluded_and_can_be_re_included_by_hand`
  (`story/F5-S2-qa`), which asserts `state == "included"`. That test encodes a product requirement from the
  F5 epic, so it must not be weakened without the requirement changing first.
- **§2.5's gate fix** — an L2 test: "5 included comps that all lack sqft do not open the analysis gate."

## 6. Provenance

All numbers above were produced read-only against `pull_ref="ws1-real"` in fixture mode, via the main
checkout's `.venv` with `PYTHONPATH` pointed at this worktree's `backend/src`. No live RentCast call was
made; `RENTCOMP_LIVE` was never set; the ledger stands at 8/50. `git diff -- backend/src frontend/src` is
empty. Probe scripts were written to the session scratchpad, not to the repo.
