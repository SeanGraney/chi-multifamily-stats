"""Row 9b — identity-field selection measured on the REAL committed pull.

QA-authored, written RED before any implementation exists. Layer 1:
`shape_raw_pull` over the two committed T-S3 gate responses
(`fixtures/live-samples/`) — real RentCast records, zero network (D17).

WHY THIS FILE EXISTS ALONGSIDE `test_r9b_identity_field_selection.py`
---------------------------------------------------------------------
That file proves the rules. This one proves they bite where the money is, on
named real records, with the numbers spelled out — and, just as importantly,
records how *little* of the input space the real pull actually exercises, so
nobody mistakes green here for coverage. Measured over the committed 539-record
pull, before writing anything (2026-07-29):

    567 comps, of which 49 are multi-spell chains
      chains whose spells disagree on sqft ............... 1  (one side null)
      ... on sqft with BOTH sides non-null ............... 0
      ... on unit ....................................... 4
      ... on lat/lng .................................... 0
      ... on beds ....................................... 0
      ... on baths ...................................... 0
      chains where chain[-1] is NOT the most complete .... 1
      raw records with squareFootage <= 0 ................ 0
      comps sitting at lat == 0 or lng == 0 .............. 0

So the entire real-world reach of this defect is **one comp**, and six of the
seven fields are decided by evidence this pull does not contain. That is the
argument for the synthetic file, stated as executable numbers rather than as a
comment: inheriting boundary coverage from whatever a real pull happens to hold
is worth nothing (the standing lesson from F4-S8's mutant M8).

⚠ TWO GRANULARITIES, TWO DIFFERENT NUMBERS — RESOLVED 2026-07-30
-----------------------------------------------------------------
An earlier revision of this file asserted "exactly one group disagrees about
squareFootage" and **measured two**, which was left unresolved when its author
was stopped. Measured to the bottom: **both numbers are right, at different
granularities, and the two granularities are not interchangeable.**

* **RECORD/GROUP level = 2.** Two `comp_key` groups hold records that report
  different square footages.
* **CHAIN level = 1.** Only one *chain* has spells that disagree — and a chain
  is what `_build_comp` folds, so a chain is the only granularity at which the
  positional accident can bite.

The second group is **`3016 W 40th St Unit 1`** (900 vs 1100 sqft) — **not**
`2453 W 46th Pl`, whose six spells all report 800 and which appears only in the
`unit` row above. Its two records are 193 days apart, far beyond the 42-day
stitch threshold, so they stitch into **two separate single-spell comps**, each
already carrying its own square footage. A one-spell chain has nothing to choose
between: `chain[-1]` *is* the whole chain. So it is invisible to this row's fix,
and row 9b's finding of exactly **one** affected comp stands as written.

The lesson is worth more than the count: a census that groups by `comp_key`
answers a question about *addresses*, and this row's question is about *chains*.
Both are asserted below, separately and by name, so neither can be silently
mistaken for the other again.

THE ONE COMP, AND WHY IT IS WORTH A ROW ANYWAY
-----------------------------------------------
`2350 S Leavitt St 1R` is two listing ids for one physical unit, which F4-S2's
normalized grouping put in one group and F4-S3's stitcher merged into one
128-day chain:

    id ...-Unit-1R  (Condo)      hist 2025-09-11 -> 2025-10-22  $2995  sqft None
                                 top  2025-10-22 -> 2025-12-11  $2695  sqft None
    id ...---1R     (Apartment)  top  2025-10-15 -> 2026-01-17  $2695  sqft 700

The chain is [2025-09-11, 2025-10-15, 2025-10-22]. The middle spell is the only
one that reported a square footage, and `chain[-1]` is not it — so the comp
publishes `sqft=None` while the pull holds `squareFootage: 700` for the same
unit, in the same group, in the same fixture. Via F4-S5 ("missing-sqft comps
... excluded from every median") that silently removes it from every cohort
median behind `premium`, from every bucket, and from every neighbour set.

⚠ ESCALATION, NOT AN ASSERTION — see
`test_the_comp_this_row_admits_is_the_pulls_most_extreme_psf`. Admitting it is
not free: 700 sqft for a 3-bed/3-bath at $2995 is **$4.28/sqft**, which would
be the highest $/sqft in the pull (today's maximum is $4.18) and a premium of
**+157%** against its cohort. The rule is right — a reported value beats an
absent one and cannot invent a number — but on this pull the single comp it
admits is one whose square footage looks wrong, and the flag designed to catch
exactly that (`sqft_suspect`, ">30% off the cohort median") was not built.

**F5-S1 BUILT IT (2026-08-03).** The flag is computed per request in
`pipeline/premium.py` — `abs(premium) > 30%` against the same cohort median
the premium reports against — and this comp raises it, so the row the owner
keeps looking at now says "verify sqft". It is a *display* flag: the comp is
still admitted, still in every median, and the human still makes the call
(NORTH_STAR). Because the flag is per-request it is no longer a field on
`StitchedComp` at all, which is why the assertions in this file measure the
comp's `psf` and stop there — the flag is asserted where it lives, at
`tests/api/test_f5s1_row_fields_on_the_wire.py`.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.keys import comp_key
from rentcomp.pipeline.shape import (
    _dedupe_by_id,
    _group_key,
    extract_spells,
    shape_raw_pull,
    stitch,
)
from rentcomp.storage.config import Config

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

#: `storage/pulls.py`'s `ws1-real` branch, verbatim — so what this file measures
#: is what `POST /api/derive` serves.
AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")

#: F4-S2/F4-S3 established this and row 9b must not move it: the selector
#: decides what a comp *says*, never how many comps there are.
EXPECTED_COMP_COUNT = 567

#: The comp at the centre of this row.
LEAVITT_ADDRESS = "2350 S Leavitt St"
LEAVITT_FIRST_LISTED = date(2025, 9, 11)
LEAVITT_KEY = comp_key(LEAVITT_ADDRESS, "Unit 1R")
LEAVITT_SQFT = 700.0
LEAVITT_INITIAL_ASK = 2995.0

#: F4-S3's other real chain — carried here purely as a no-regression guard.
PL_46TH_ADDRESS = "2453 W 46th Pl"
PL_46TH_FIRST_LISTED = date(2025, 10, 27)
PL_46TH_DOM = 205
PL_46TH_SQFT = 800.0

#: Comps carrying a $/sqft, before and after. Measured, not predicted.
SQFT_BEARING_BEFORE = 484
SQFT_BEARING_AFTER = 485

#: ⚠ The two granularities, kept apart on purpose (see the module docstring).
#: `comp_key` GROUPS whose raw records report differing square footages. Two:
#: `2350 s leavitt st|1r` and `3016 w 40th st|1`.
RECORD_LEVEL_SQFT_DISAGREEMENTS = 2
#: Stitched CHAINS whose spells disagree about square footage. One. A chain is
#: what `_build_comp` folds into a comp, so this — not the number above — is the
#: count of comps this row can move.
CHAIN_LEVEL_SQFT_DISAGREEMENTS = 1
#: The group that is in the first count and not the second, named so a future
#: reader does not have to re-derive why.
UNREACHABLE_GROUP = "3016 w 40th st|1"


def _raw() -> tuple[list[dict], list[dict]]:
    return json.loads(ACTIVE_FIXTURE.read_text()), json.loads(INACTIVE_FIXTURE.read_text())


@pytest.fixture(scope="module")
def real_comps():
    active, inactive = _raw()
    return shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)


def _comp(real_comps, address: str, first_listed: date):
    matches = [c for c in real_comps if c.address == address and c.first_listed == first_listed]
    assert len(matches) == 1, (
        f"oracle drift: expected exactly one comp at {address!r} starting {first_listed}, found "
        f"{len(matches)} ({[(c.unit, c.first_listed) for c in matches]})"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# preconditions — failing, never skipping
# ---------------------------------------------------------------------------


def test_the_committed_fixtures_are_present_and_shape(real_comps) -> None:
    """A failing precondition rather than a skip guard (WORKFLOW.md §2: a skip
    that exits 0 has been mistaken for a pass four times on this project)."""
    assert len(real_comps) == EXPECTED_COMP_COUNT, (
        f"the committed pull shaped into {len(real_comps)} comps, expected "
        f"{EXPECTED_COMP_COUNT}. Row 9b changes what a comp SAYS, never how many there are — a "
        f"different count means the chain or the grouping moved, not the selector."
    )


# ---------------------------------------------------------------------------
# the defect, on the record it is reachable on
# ---------------------------------------------------------------------------


def test_the_leavitt_comp_carries_the_square_footage_its_own_pull_reported(real_comps) -> None:
    """THE ACCEPTANCE CRITERION on real evidence.

    The pull holds `squareFootage: 700` for this unit. The comp must not publish
    "unknown" — that is the difference between no evidence and discarded
    evidence, and F4-S5 spends the difference by excluding the comp from every
    median.
    """
    comp = _comp(real_comps, LEAVITT_ADDRESS, LEAVITT_FIRST_LISTED)
    assert comp.sqft == LEAVITT_SQFT, (
        f"(pre-fix value was None) {LEAVITT_ADDRESS} {comp.unit!r} publishes sqft={comp.sqft}, "
        f"but one of the two listing ids in its own group reported squareFootage=700 for the "
        f"2025-10-15 spell. The identity fields are being read off the chain's last spell by "
        f"listed date, which is not that spell."
    )
    assert comp.initial_ask == LEAVITT_INITIAL_ASK, (
        "initialAsk is still the FIRST spell's price (F4-S3) — 2025-09-11 at $2995"
    )
    assert comp.psf == pytest.approx(LEAVITT_INITIAL_ASK / LEAVITT_SQFT), (
        "a comp with a square footage has a $/sqft, and $/sqft is what every median, bucket "
        "and neighbour set is built from"
    )
    assert comp.effective_dom == 128, "F4-S3's corrected DOM must be untouched by this row"
    assert comp.censored is False


def test_the_pull_gains_exactly_one_sqft_bearing_comp(real_comps) -> None:
    """The census, pinned. 484 -> 485 of 567.

    A deliberate change to these numbers is re-blessable (edit them in the same
    commit and say why); an accidental one is what this catches. If the count
    jumps by more than one, the fix is doing something wider than choosing among
    observations a chain already holds — there is exactly one chain in this pull
    whose spells disagree about `sqft` at all.
    """
    with_sqft = sum(1 for comp in real_comps if comp.sqft is not None)
    assert with_sqft == SQFT_BEARING_AFTER, (
        f"(pre-fix value was {SQFT_BEARING_BEFORE}) {with_sqft} of {len(real_comps)} comps "
        f"carry a square footage. Exactly one comp changes here — `2350 S Leavitt St 1R`. A "
        f"larger move means comps are gaining a sqft from somewhere other than their own "
        f"chain's observations."
    )
    assert sum(1 for comp in real_comps if comp.psf is not None) == SQFT_BEARING_AFTER, (
        "every comp with a positive sqft has a psf and vice versa"
    )


def test_the_46th_pl_comp_is_unchanged_by_this_row(real_comps) -> None:
    """No-regression guard on F4-S3's other real chain.

    Its six spells all agree on `sqft` (800) and it is `at` the anchor either
    way, so nothing about it may move — its 205-day DOM in particular, which was
    F4-S3's headline correction and is the Kaplan-Meier event time.
    """
    comp = _comp(real_comps, PL_46TH_ADDRESS, PL_46TH_FIRST_LISTED)
    assert comp.sqft == PL_46TH_SQFT
    assert comp.effective_dom == PL_46TH_DOM, (
        f"F4-S3 corrected this chain from 165 to 205 days; got {comp.effective_dom}"
    )
    assert comp.censored is False


# ---------------------------------------------------------------------------
# what a fix must not cost
# ---------------------------------------------------------------------------


def test_no_comp_key_in_the_pull_moves(real_comps) -> None:
    """F13-S1 [INVARIANT], on the whole pull at once.

    Four chains in this pull disagree about how to spell their `unit`
    (`Unit 1R` vs `# 1R`, `Apt 2` vs `Unit 2`, `Unit 3` vs `# 3`), so a change
    to which observation supplies that string is free to change what four rows
    print. It is **not** free to change the keys those rows are addressed by:
    every weight in a saved workspace is stored against a `comp_key`, and a key
    that shifts silently discards the owner's toggles.

    `pipeline.keys.comp_key` normalizes all of those spellings to the same
    token, so this holds under any of the candidate selectors — asserted so the
    fix cannot reach for a raw string where a normalized one belongs.
    """
    expected = {
        "2350 s leavitt st|1r",
        "2453 w 46th pl|1",
        "2835 w 38th pl|2",
        "3643 s hamilton ave|3",
    }
    keys = {comp_key(comp.address, comp.unit) for comp in real_comps}
    missing = expected - keys
    assert not missing, (
        f"these comps' keys moved: {sorted(missing)}. They are the four chains in this pull "
        f"whose observations disagree about the unit designator's formatting, so they are "
        f"exactly the rows a display-string change can break. Present keys at those addresses: "
        f"{sorted(k for k in keys if k.split('|')[0] in {m.split('|')[0] for m in missing})}"
    )


def test_no_comp_in_the_pull_is_placed_at_the_null_island(real_comps) -> None:
    """`extract_spells` coerces a missing latitude/longitude to 0.0, which is a
    real location in the Gulf of Guinea rather than an absence. The map is one of
    this release's named asks, so a comp there is a pin somebody has to explain.

    Zero comps are there today, and the synthetic sibling proves a two-
    observation chain can put one there. This is the guard that makes it loud on
    real data.
    """
    stranded = [
        (comp.address, comp.unit, comp.lat, comp.lng)
        for comp in real_comps
        if comp.lat == 0.0 or comp.lng == 0.0
    ]
    assert not stranded, f"comps with a null coordinate: {stranded[:5]}"


def test_no_comp_publishes_a_non_positive_square_footage(real_comps) -> None:
    """`StitchedComp.psf` declines to divide by `sqft <= 0`, so a bad value is
    caught for the statistics — and still reaches the wire as the unit's printed
    size. Zero records in this pull carry `squareFootage <= 0`; if a future pull
    does, the selector must not prefer it over a real measurement."""
    bad = [
        (comp.address, comp.unit, comp.sqft)
        for comp in real_comps
        if comp.sqft is not None and comp.sqft <= 0.0
    ]
    assert not bad, f"comps publishing a non-positive square footage: {bad[:5]}"


def test_every_identity_value_in_the_pull_came_from_an_observation(real_comps) -> None:
    """No comp may report an attribute nothing observed — checked against the
    raw fixtures rather than against the shaper's own intermediates, so this
    cannot agree with a bug by sharing one.

    For each comp, the set of raw records at its normalized address+unit is
    recovered from the fixtures, and its `sqft` / `baths` / `(lat, lng)` must
    each appear among what those records reported. This is what forbids
    averaging two square footages or composing a coordinate out of two
    observations.
    """
    active, inactive = _raw()
    reported: dict[str, dict[str, set]] = {}
    for rec in (*active, *inactive):
        address = rec.get("addressLine1") or rec.get("formattedAddress") or ""
        key = comp_key(address, rec.get("addressLine2"))
        slot = reported.setdefault(key, {"sqft": set(), "baths": set(), "point": set()})
        sqft = rec.get("squareFootage")
        slot["sqft"].add(None if sqft is None else float(sqft))
        baths = rec.get("bathrooms")
        slot["baths"].add(None if baths is None else float(baths))
        slot["point"].add((float(rec.get("latitude") or 0.0), float(rec.get("longitude") or 0.0)))

    invented = []
    for comp in real_comps:
        slot = reported.get(comp_key(comp.address, comp.unit))
        if slot is None:  # key disambiguation appends a suffix; skip those
            continue
        if comp.sqft not in slot["sqft"]:
            invented.append(("sqft", comp.address, comp.unit, comp.sqft, sorted(slot["sqft"], key=repr)))
        if comp.baths not in slot["baths"]:
            invented.append(("baths", comp.address, comp.unit, comp.baths, sorted(slot["baths"], key=repr)))
        if (comp.lat, comp.lng) not in slot["point"]:
            invented.append(("point", comp.address, comp.unit, (comp.lat, comp.lng), sorted(slot["point"])))
    assert not invented, (
        f"{len(invented)} identity value(s) on the wire were reported by no raw record at that "
        f"address+unit: {invented[:5]}"
    )


def test_shaping_the_real_pull_still_does_not_depend_on_response_order(real_comps) -> None:
    """As a multiset (a `set` comparison is satisfied by a result that gained or
    lost a duplicate — the exact failure F4-S2 exists to prevent).

    The two records behind `2350 S Leavitt St 1R` are two listing ids that
    arrive in different halves of the pull, so this row's whole subject is a
    pair of observations whose arrival order the owner does not control.
    """
    active, inactive = _raw()
    swapped = shape_raw_pull(inactive, active, Config(), AS_OF, *FULL_YEAR)
    forward = Counter(comp.model_dump_json() for comp in real_comps)
    reverse = Counter(comp.model_dump_json() for comp in swapped)
    assert forward == reverse, (
        "shaping the real pull depends on which response a record arrived in; multiset "
        f"difference: {sorted((forward - reverse) | (reverse - forward))[:3]}"
    )


# ---------------------------------------------------------------------------
# the two granularities — the open question this file was stopped on
# ---------------------------------------------------------------------------


def test_only_one_chain_in_this_pull_can_be_affected_at_all() -> None:
    """⭐ THE ANSWER to the record-level-vs-chain-level discrepancy.

    ``test_this_pull_cannot_decide_six_of_the_seven_fields`` counts **groups**
    and gets 2. This counts **chains** and gets 1. Both are correct; only the
    second one is this row's number, because `_build_comp` folds a *chain*, and
    a chain is the only place two observations ever compete to supply a field.

    Asserted GREEN today and green after the fix: it is a statement about the
    committed evidence, not about the selector. If a future re-pull makes it
    fail, the row's blast radius has genuinely widened and the anchor movement
    recorded in the queue must be re-measured before anyone trusts it.
    """
    active, inactive = _raw()
    groups: dict[str, list[dict]] = {}
    for copies in _dedupe_by_id(active, inactive):
        groups.setdefault(_group_key(copies), []).extend(copies)

    disagreeing_chains: list[tuple[str, list]] = []
    for key in sorted(groups):
        spells = extract_spells(groups[key])
        if not spells:
            continue
        for chain in stitch(spells, Config().stitch_gap_days):
            sqfts = {spell.sqft for spell in chain}
            if len(sqfts) > 1:
                disagreeing_chains.append((key, sorted(sqfts, key=repr)))

    assert len(disagreeing_chains) == CHAIN_LEVEL_SQFT_DISAGREEMENTS, (
        f"expected {CHAIN_LEVEL_SQFT_DISAGREEMENTS} chain whose spells disagree about sqft, got "
        f"{len(disagreeing_chains)}: {disagreeing_chains}. This is the count of comps row 9b can "
        f"move; the queue's recorded anchor movement is measured against exactly this set."
    )
    assert disagreeing_chains[0][0] == LEAVITT_KEY, (
        f"the one affected chain should be {LEAVITT_KEY!r}, got {disagreeing_chains[0][0]!r}"
    )


def test_the_second_disagreeing_group_is_out_of_this_rows_reach_and_why() -> None:
    """`3016 W 40th St Unit 1` — the group that makes the record-level count 2.

    It is **not** `2453 W 46th Pl` (whose six spells all report 800 sqft and
    which disagrees only about how to spell its unit). It is two listings of the
    same unit 193 days apart, reporting 1100 and 900 square feet.

    193 days clears the 42-day stitch threshold by a wide margin, so they are
    **two comps, not one chain** — and each is a single-spell chain that already
    carries its own square footage. There is nothing for a selector to choose
    between, at either end. That is why the pull's sqft-bearing count moves by
    one and not by two.

    This test asserts the *reason*, not just the outcome: if a future config
    lowered the stitch threshold enough to merge these two, they would become
    one chain with a genuine 1100-vs-900 conflict — the one shape this row's
    rule has no answer for (a reported value beating another reported value),
    and the case the synthetic sibling deliberately leaves to a PM ruling.
    """
    active, inactive = _raw()
    groups: dict[str, list[dict]] = {}
    for copies in _dedupe_by_id(active, inactive):
        groups.setdefault(_group_key(copies), []).extend(copies)

    assert UNREACHABLE_GROUP in groups, (
        f"{UNREACHABLE_GROUP!r} is no longer a group in this pull; the census note in this "
        f"module's docstring describes evidence that has moved and must be re-measured"
    )
    spells = extract_spells(groups[UNREACHABLE_GROUP])
    assert {spell.sqft for spell in spells} == {900.0, 1100.0}, (
        f"expected the two records to report 900 and 1100 sqft, got "
        f"{sorted({s.sqft for s in spells}, key=repr)}"
    )

    chains = stitch(spells, Config().stitch_gap_days)
    assert len(chains) == 2, (
        f"these two listings must remain SEPARATE comps, got {len(chains)} chain(s). If they "
        f"merged, this pull would contain a conflicting-reported-values case and row 9b's "
        f"single-comp blast radius would be wrong."
    )
    assert all(len(chain) == 1 for chain in chains), (
        "each is a single-spell chain, so `chain[-1]` is the whole chain and no selection "
        "rule — positional or completeness-based — can change what either comp publishes"
    )
    gap = (chains[1][0].listed - chains[0][-1].removed).days
    assert gap == 193, f"the off-market gap that keeps them apart is 193 days, measured {gap}"
    assert gap >= Config().stitch_gap_days, (
        f"the gap ({gap}d) must clear the stitch threshold ({Config().stitch_gap_days}d)"
    )


def test_the_46th_pl_chain_disagrees_about_its_unit_and_not_its_sqft() -> None:
    """Rules out the hypothesis that was current before this was measured.

    `2453 W 46th Pl` was the natural suspect for "the second affected comp",
    because row 9b already names it as the comp whose displayed `unit` flips
    `Unit 1` -> `# 1`. It is **not** the second sqft disagreement: all six of its
    spells report 800. Pinned so the wrong explanation cannot be re-adopted by
    plausibility.
    """
    active, inactive = _raw()
    groups: dict[str, list[dict]] = {}
    for copies in _dedupe_by_id(active, inactive):
        groups.setdefault(_group_key(copies), []).extend(copies)

    spells = extract_spells(groups["2453 w 46th pl|1"])
    assert {spell.sqft for spell in spells} == {PL_46TH_SQFT}, (
        f"every spell of this chain reports {PL_46TH_SQFT} sqft; got "
        f"{sorted({s.sqft for s in spells}, key=repr)} — if this ever differs, this comp joins "
        f"the affected set and the anchor movement must be re-measured"
    )
    assert len({spell.unit for spell in spells}) > 1, (
        "its disagreement is about the unit designator's spelling — presentational, and the "
        "reason it is NOT a second numeric effect"
    )


# ---------------------------------------------------------------------------
# the escalation, as executable evidence
# ---------------------------------------------------------------------------


def test_the_comp_this_row_admits_is_the_pulls_most_extreme_psf(real_comps) -> None:
    """⚠ NOT A REQUIREMENT — the owner FYI, pinned so it is a measurement rather
    than a claim in a handoff.

    `2350 S Leavitt St 1R` reports 700 sqft for a **3-bed/3-bath** at $2995,
    i.e. **$4.28/sqft**. Today's maximum $/sqft across all 484 sqft-bearing
    comps is $4.18, and the median 3-bed in this pull is 1200 sqft (only 5 of
    429 are at or under 700). So the single comp row 9b admits arrives as the
    most extreme $/sqft in the evidence base, at a premium of about +157%.

    Both things are true at once and neither cancels the other:

    * the RULE is right. A reported value beats an absent one, it cannot invent
      a number, and today's behaviour is a positional accident with no argument
      behind it at all;
    * the DATUM is suspicious, and the guard for a suspicious datum —
      `sqft_suspect`, ">30% off the cohort median $/sqft" (F5-S1) — is not built.
      `StitchedComp.sqft_suspect` is hardcoded `False` on every comp and
      `/api/derive` ships a `provisional_field` warning saying so.

    This test asserts only the measurement, so that whatever the owner decides
    is recorded against a number. It does **not** assert that admitting the comp
    is correct, and it must not be turned into that: if the answer is "exclude
    it", the answer is F5-S1's flag or an explicit curation toggle, never a
    silent return to reading `chain[-1]`.
    """
    comp = _comp(real_comps, LEAVITT_ADDRESS, LEAVITT_FIRST_LISTED)
    assert comp.psf is not None, (
        "this test measures the admitted comp's $/sqft; it has none, so the row's fix is not "
        "in place yet (see test_the_leavitt_comp_carries_the_square_footage_its_own_pull_reported)"
    )
    assert comp.psf == pytest.approx(4.2785714285714285), f"psf={comp.psf}"
    assert (comp.beds, comp.baths, comp.sqft) == (3.0, 3.0, 700.0), (
        "the shape of the suspicion: three bedrooms and three bathrooms in 700 square feet"
    )
    others = [c.psf for c in real_comps if c.psf is not None and c is not comp]
    assert comp.psf > max(others), (
        f"THE PROPERTY THIS TEST DEFENDS: the comp row 9b admits is the pull's most extreme "
        f"$/sqft. It publishes {comp.psf}, and some other comp publishes {max(others)}. If that "
        f"is no longer true, the escalation in this file's docstring is stale and the owner FYI "
        f"it supports must be re-read before anyone relies on it."
    )
    # ⚠ TOLERANCE, CORRECTED 2026-07-30 (row 9b verify) — a defect in this test, not in the fix.
    # This line previously read `pytest.approx(4.176)`. `pytest.approx` defaults to a RELATIVE
    # tolerance of 1e-6, i.e. ±4.176e-06, while the true value is 4.1759530791788855 — off the
    # 4-decimal literal by 4.69e-05, about 11x the tolerance. A rounded literal was written under
    # a 6-significant-figure default. It had never executed because the assertion above it
    # (`comp.psf is not None`) bailed first on the pre-fix source, so the row's own fix is what
    # made it reachable; measured identical (4.1759530791788855) on BOTH source trees, so it is
    # not caused by the fix.
    #
    # KEPT rather than deleted, because it defends something the ordering assertion above does
    # NOT: the escalation is quantified as "$4.28 vs a prior max of $4.18", and a runner-up that
    # crept to 4.27 would still satisfy `>` while making that characterisation wrong. The pin is
    # a documentation-drift guard on the quoted figure and on the size of the gap.
    # `abs=1e-3` is the tolerance the 4-decimal literal was always written at; the exact float
    # literal would over-pin to bit level for no gain, since nothing here is sensitive to the
    # last ulp and a legitimate float reassociation upstream should not fail this test.
    assert max(others) == pytest.approx(4.176, abs=1e-3), (
        f"the highest $/sqft among the OTHER comps is {max(others)}; this file's escalation note "
        f"quotes a prior maximum of $4.18 and should be re-read if that moved"
    )
    assert round(max(others), 2) == 4.18, (
        "the docstring's quoted figure, in the form it is quoted in"
    )
    # F5-S1: `sqft_suspect` used to be asserted here, `False`, with a note
    # saying it should flip to `True` when the flag was built. It was built —
    # and building it moved the flag off `StitchedComp` entirely, because the
    # cohort median it is measured against is taken over the SELECTED comps
    # and therefore only exists per request. So there is nothing to assert on
    # a shaped comp any more; the flip this note promised is asserted over the
    # wire instead, on this same address:
    # `tests/api/test_f5s1_row_fields_on_the_wire.py
    #  ::test_the_leavitt_comp_raises_the_verify_sqft_flag`.
    assert not hasattr(comp, "sqft_suspect"), (
        "a shaping-time `sqft_suspect` came back. It cannot be answered at shaping time: the "
        "cohort median it is measured against moves with the user's selection (F4-S5), so a "
        "value stored here could only ever be a stale one"
    )


def test_this_pull_cannot_decide_six_of_the_seven_fields(real_comps) -> None:
    """The reason the synthetic sibling exists, asserted rather than asserted in
    a comment (F4-S8's mutant-M8 lesson).

    Only `sqft` is decided by real evidence here, and only on one comp. If a
    future re-pull starts disagreeing about coordinates, bedrooms or bathrooms,
    this test fails and tells whoever re-pulled that the census in this file
    just became sensitive to a rule nothing in it covers.
    """
    active, inactive = _raw()
    by_key: dict[str, list[dict]] = {}
    for rec in (*active, *inactive):
        address = rec.get("addressLine1") or rec.get("formattedAddress") or ""
        by_key.setdefault(comp_key(address, rec.get("addressLine2")), []).append(rec)

    def spread(recs, field, cast=float):
        values = set()
        for rec in recs:
            raw = rec.get(field)
            values.add(None if raw is None else cast(raw))
        return values

    disagreements = Counter()
    for recs in by_key.values():
        if len(recs) < 2:
            continue
        if len(spread(recs, "squareFootage")) > 1:
            disagreements["sqft"] += 1
        if len(spread(recs, "bedrooms")) > 1:
            disagreements["beds"] += 1
        if len(spread(recs, "bathrooms")) > 1:
            disagreements["baths"] += 1
        points = {(rec.get("latitude"), rec.get("longitude")) for rec in recs}
        if len(points) > 1:
            disagreements["point"] += 1

    assert disagreements["sqft"] == RECORD_LEVEL_SQFT_DISAGREEMENTS, (
        f"expected {RECORD_LEVEL_SQFT_DISAGREEMENTS} comp_key GROUPS whose raw records report "
        f"different square footages; got {disagreements['sqft']}. ⚠ This is the RECORD-level "
        f"count and it is deliberately NOT this row's count — see "
        f"test_only_one_chain_in_this_pull_can_be_affected_at_all, and this module's docstring."
    )
    for field in ("beds", "baths", "point"):
        assert disagreements[field] == 0, (
            f"{disagreements[field]} group(s) now disagree about {field!r}. This pull could "
            f"previously say nothing about how to select that field, so the rule for it lives "
            f"only in test_r9b_identity_field_selection.py — real data can now see it, and the "
            f"choice needs re-reading against this evidence."
        )
