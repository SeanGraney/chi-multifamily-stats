"""Row 9b — where a stitched comp's IDENTITY fields come from.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Layer 1: decision procedure Q1 — `shape_raw_pull` is importable and
takes plain lists of dicts, so every case below is a direct call with plain
values. Zero network (D17); the suite-wide socket kill switch in
`tests/conftest.py` would stop one anyway.

THE DEFECT
----------
`_build_comp` reads `address / unit / lat / lng / beds / baths / sqft` off
`chain[-1]` — the chain's last spell **by listed date**. Nobody chose "the most
recent observation wins"; that is simply what indexing the end of a sorted list
does. F4-S3 already corrected the same positional accident for the chain's
*end* (`_chain_end`, "effectiveDOM = final removal − first listed") and
deliberately left these seven fields alone as its own out-of-scope note (PM
ruling E2), which is this row.

WHY IT IS NOT COSMETIC
----------------------
`sqft` is the one that reaches a number. F4-S5 [INVARIANT]: "Missing-sqft
comps: flagged, default weight 0, **excluded from every median**." So the
selector decides which comps have a `psf`, hence which comps are evidence for a
cohort median, hence every comp's `premium`, hence **which bucket a comp lands
in**, hence that bucket's DOM statistics — the number the owner asked to be able
to trust.

WHAT IS AND IS NOT PINNED HERE — READ THIS BEFORE ADDING AN ASSERTION
----------------------------------------------------------------------
F4-S3's [INVARIANT] enumerates the merged record's fields — `initialAsk`,
`effectiveDOM`, `censored`, `cutHistory`, `relistCount`, `gapDays` — and does
**not mention any of these seven**. There is therefore no invariant that says
where an identity field comes from, and `chain[-1]` is filling a spec vacuum
rather than implementing a rule. This file pins only what is decidable without
a ruling:

1. **A reported value never loses to an absent one** (the F4-S3 `_completeness`
   precedent: strictly more evidence, and it cannot invent a number). This is
   the direction, and it is not in doubt.
2. **No value is invented.** Every identity field a comp reports must equal a
   value some spell in its own chain actually reported. This is what forbids
   averaging two sqfts, interpolating a coordinate, or defaulting a missing one
   to zero.
3. **The selection is deterministic and value-derived**, not position-derived —
   the standing reproducibility requirement on this project.
4. **`comp_key` does not move** (F13-S1 [INVARIANT]: curation state is keyed on
   normalized address+unit, never on a churning id; a key that changes silently
   discards the user's toggles).

Deliberately **not** pinned, because they are the PM's call and not QA's:

* which *display formatting* wins when two observations both report a `unit`
  (`Unit 1R` vs `# 1R`) — "most complete" is not obviously the right selector
  for a display string, and both are equally reported;
* which `sqft` wins when two observations report **different non-null** values —
  see `test_two_conflicting_reported_sqfts_resolve_deterministically`. The
  precedent argument gives no direction at all there: neither value is "more
  evidence" than the other.

`_build_comp` is private and this file never names it. Every case drives
`shape_raw_pull`, so the fix is free to restructure the internals, add a seam,
or move the selection into `extract_spells` — none of that breaks these tests,
which is the point.
"""

from __future__ import annotations

from datetime import date
from itertools import permutations

import pytest

from rentcomp.pipeline import shape as shape_module
from rentcomp.pipeline.keys import comp_key
from rentcomp.pipeline.shape import extract_spells, shape_raw_pull, stitch
from rentcomp.storage.config import Config

#: The seven fields `_build_comp` currently takes off `chain[-1]`.
IDENTITY_FIELDS = ("address", "unit", "lat", "lng", "beds", "baths", "sqft")

AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")

ADDRESS = "10 Probe St"

#: Several observations of ONE listing id — `_dedupe_by_id`'s documented case (a
#: listing whose status flipped mid-pagination, or whose `history` restates it).
#: Required wherever a chain's observations must disagree about `unit`.
SHARED_ID = "one-listing"


# ---------------------------------------------------------------------------
# harness preconditions — FAILING, never skipping (WORKFLOW.md §2: "a skip that
# exits 0 is indistinguishable from a pass", the defect class that has recurred
# four times on this project)
# ---------------------------------------------------------------------------


def test_the_source_tree_under_test_is_this_worktrees() -> None:
    """The §2 worktree trap, asserted rather than trusted.

    A worktree shares the main checkout's `.venv`, whose editable install
    resolves `rentcomp` against the MAIN checkout's `backend/src`. A bare full
    `pytest` run is rescued only by collection order (`scripts/gate.py` inserts
    the worktree path first), and a single-file run is not rescued at all — it
    answers about `main`'s code while looking entirely correct.

    So this test names the tree. It cannot tell a worktree from the main
    checkout by path alone (both are legitimate), but it CAN prove that the
    `shape` module the assertions below run against is the one in the same
    repository as this test file — which is the thing that actually goes wrong.
    """
    from pathlib import Path

    module_root = Path(shape_module.__file__).resolve().parents[2]  # .../backend/src
    test_root = Path(__file__).resolve().parents[3] / "backend" / "src"
    assert module_root == test_root, (
        f"rentcomp.pipeline.shape was imported from {module_root}, but this test file lives "
        f"under {test_root}. The run is testing the WRONG SOURCE TREE (WORKFLOW.md §2) — set "
        f"PYTHONPATH=<this worktree>/backend/src (';' separator on Windows) and rerun. Every "
        f"result from this session is void until this passes."
    )


# ---------------------------------------------------------------------------
# record builders
# ---------------------------------------------------------------------------


def record(
    listing_id: str,
    *,
    listed: str,
    removed: str | None = None,
    price: float = 2000.0,
    address: str = ADDRESS,
    unit: str | None = "Unit 1",
    lat: float | None = 41.85,
    lng: float | None = -87.68,
    beds: float | None = 3.0,
    baths: float | None = 2.0,
    sqft: float | None = 1000.0,
    history: dict | None = None,
) -> dict:
    """One raw RentCast-shaped record. Field names match the committed samples.

    `None` for an optional attribute means the response OMITTED it — which is
    the whole subject of this row, so it is spelled explicitly at every call
    site rather than defaulted into existence.
    """
    return {
        "id": listing_id,
        "addressLine1": address,
        "addressLine2": unit,
        "latitude": lat,
        "longitude": lng,
        "bedrooms": beds,
        "bathrooms": baths,
        "squareFootage": sqft,
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": None if removed is None else f"{removed}T00:00:00.000Z",
        "price": price,
        "history": history or {},
    }


def shape(*records: dict, config: Config | None = None):
    """`shape_raw_pull` over `records`, all in the Inactive half."""
    return shape_raw_pull([], list(records), config or Config(), AS_OF, *FULL_YEAR)


def one_comp(*records: dict, config: Config | None = None):
    comps = shape(*records, config=config)
    assert len(comps) == 1, (
        f"harness precondition: these {len(records)} record(s) must stitch into exactly ONE "
        f"comp; got {len(comps)} "
        f"({[(c.address, c.unit, c.first_listed, c.effective_dom) for c in comps]}). Gaps must "
        f"stay under Config().stitch_gap_days (42) and every start inside the window."
    )
    return comps[0]


def chains_of(*records: dict, config: Config | None = None):
    """The stitched chains for one group, straight off the two public seams."""
    cfg = config or Config()
    spells = extract_spells(list(records))
    return stitch(spells, cfg.stitch_gap_days)


# ===========================================================================
# AC — the defect itself
# ===========================================================================


def test_a_reported_sqft_is_not_discarded_because_a_later_observation_omitted_it() -> None:
    """THE ACCEPTANCE CRITERION, in its smallest form.

    Two observations of one unit. The earlier one reports `squareFootage: 700`;
    the later one does not report it at all. Under `chain[-1]` the comp comes
    out with `sqft=None` — i.e. the pipeline holds a reported square footage in
    its own input and publishes "unknown".

    That is not "no evidence", it is *discarded* evidence, and F4-S5's
    [INVARIANT] ("missing-sqft comps ... excluded from every median") then
    excludes the comp from every cohort median, every bucket and every
    neighbour set on the strength of it. Preferring the reported value cannot
    invent a number — it is the identical argument the PM already approved for
    F4-S3's `_completeness` tie-break.
    """
    comp = one_comp(
        record("early", listed="2025-03-01", removed="2025-04-01", sqft=700.0),
        record("late", listed="2025-04-10", removed="2025-05-01", sqft=None),
    )
    assert comp.sqft == 700.0, (
        f"the pull reported squareFootage=700 for this unit and the comp published "
        f"sqft={comp.sqft}. A later observation that omitted the field is not evidence that "
        f"the field is unknown — and via F4-S5 this decides whether the comp is in every "
        f"cohort median behind `premium`."
    )
    assert comp.psf == pytest.approx(2000.0 / 700.0), (
        "a comp with a sqft has a $/sqft; without one it is excluded from every median"
    )


def test_the_reported_value_wins_from_whichever_position_it_sits_in() -> None:
    """Maximal disagreement between "most complete" and `chain[-1]`: the only
    observation that reported a sqft is the chain's FIRST spell, two re-lists
    back. Nothing about being listed later makes an observation more
    authoritative about the size of a unit — square footage is a property of
    the unit, not of the listing event."""
    comp = one_comp(
        record("a", listed="2025-02-01", removed="2025-03-01", sqft=850.0),
        record("b", listed="2025-03-05", removed="2025-04-01", sqft=None),
        record("c", listed="2025-04-05", removed="2025-05-01", sqft=None),
    )
    assert comp.first_listed == date(2025, 2, 1), "harness precondition: one chain of three"
    assert comp.relist_count == 2, "harness precondition: three spells in one chain"
    assert comp.sqft == 850.0, (
        f"the earliest of three observations is the only one that reported a square footage; "
        f"got sqft={comp.sqft}"
    )


def test_a_reported_baths_is_not_discarded_either() -> None:
    """`baths` is the other genuinely-Optional field on `Spell` (`beds`, `lat`
    and `lng` are coerced to 0.0 at the DTO edge and cannot express "absent" —
    see `test_absent_coordinates_do_not_become_a_real_place_on_the_map`).

    Unreachable on the committed pull (measured: zero chains disagree on
    `baths`), which is exactly why it is here — a rule that holds only where the
    real data happens to exercise it is not a rule.
    """
    comp = one_comp(
        record("early", listed="2025-03-01", removed="2025-04-01", baths=1.5),
        record("late", listed="2025-04-10", removed="2025-05-01", baths=None),
    )
    assert comp.baths == 1.5, f"a reported bathroom count was discarded; got {comp.baths}"


# ===========================================================================
# PROBES — the input space, not the acceptance criteria
# ===========================================================================


def test_a_chain_in_which_nothing_reported_a_sqft_still_reports_none() -> None:
    """No selector can invent a square footage, and none may try.

    The failure mode this forbids is a "helpful" default — 0.0, or the cohort
    mean, or the subject's own sqft. `psf` would then exist for a comp nobody
    measured, and NORTH_STAR's whole claim is that every number traces to an
    observation. 83 of the committed pull's 567 comps are in exactly this
    state and must stay in it.
    """
    comp = one_comp(
        record("a", listed="2025-03-01", removed="2025-04-01", sqft=None),
        record("b", listed="2025-04-10", removed="2025-05-01", sqft=None),
    )
    assert comp.sqft is None, f"a square footage was invented from nothing: {comp.sqft}"
    assert comp.psf is None, "no sqft means no $/sqft — never a fabricated zero"


def test_two_conflicting_reported_sqfts_resolve_deterministically() -> None:
    """⚠ THE CASE THE PRECEDENT DOES NOT DECIDE — flagged to the PM, not
    test-locked.

    Two observations of one unit report 700 and 900. "Prefer a reported value
    over an absent one" gives no direction whatsoever: both are reported, and
    neither is more evidence than the other. Unreachable on the committed pull
    (measured: exactly one chain disagrees on `sqft` and one side of it is
    `None`), so the choice is free — but two things are *not* free, and they are
    what this asserts:

    * the answer must be one of the two OBSERVED values. Averaging to 800 would
      publish a square footage no source ever reported, and `psf` computed from
      it would be a number with no provenance;
    * the answer must be the same every run.

    Whichever the fix picks, say so in the handoff so the PM can record it.
    """
    records = (
        record("a", listed="2025-03-01", removed="2025-04-01", sqft=700.0),
        record("b", listed="2025-04-10", removed="2025-05-01", sqft=900.0),
    )
    comp = one_comp(*records)
    assert comp.sqft in (700.0, 900.0), (
        f"the comp published sqft={comp.sqft}, which no observation reported. A blended, "
        f"averaged or defaulted square footage is a number with no provenance — pick one of "
        f"the observed values (and tell the PM which rule you picked)."
    )
    again = one_comp(*records)
    assert again.sqft == comp.sqft, "the same input resolved two ways across two calls"


def test_a_single_spell_chain_is_untouched() -> None:
    """The dominant case, and the one a regression here would be widest in: 518
    of the committed pull's 567 comps are single-spell chains. With one spell
    there is nothing to select between, so every identity field must be that
    spell's, verbatim."""
    only = record(
        "solo",
        listed="2025-06-01",
        removed="2025-07-01",
        unit="Apt 4B",
        lat=41.9001,
        lng=-87.6501,
        beds=2.0,
        baths=1.5,
        sqft=725.0,
    )
    comp = one_comp(only)
    assert comp.relist_count == 0, "harness precondition: a one-spell chain"
    assert (comp.address, comp.unit) == (ADDRESS, "Apt 4B")
    assert (comp.lat, comp.lng) == (41.9001, -87.6501)
    assert (comp.beds, comp.baths, comp.sqft) == (2.0, 1.5, 725.0)


def test_a_reported_sqft_of_zero_does_not_outrank_a_real_measurement() -> None:
    """⚠ THE TRAP IN THE OBVIOUS IMPLEMENTATION.

    F4-S3's `_completeness` scores a field as reported with `field is not
    None`. Under that test a `squareFootage: 0` is "reported", so an
    observation carrying it ties with one carrying 700 and the tie-break —
    position — can hand the comp a zero. `StitchedComp.psf` then returns `None`
    for `sqft <= 0.0`, so the comp is excluded from every median *anyway*, and
    the wire reports `sqft: 0` where the pull actually observed 700. The comp
    ends up worse off than before the fix, from a change made to improve it.

    Zero records in the committed pull carry `squareFootage <= 0` (measured), so
    this is unreachable today and reachable the moment the owner's next real
    pull returns one bad row.
    """
    comp = one_comp(
        record("real", listed="2025-03-01", removed="2025-04-01", sqft=700.0),
        record("zero", listed="2025-04-10", removed="2025-05-01", sqft=0.0),
    )
    assert comp.sqft == 700.0, (
        f"got sqft={comp.sqft}. A reported zero is not a measurement of a unit's size — it is "
        f"the absence of one, wearing a number. Completeness must be scored on 'is this a "
        f"usable measurement', not on `is not None`."
    )
    assert comp.psf == pytest.approx(2000.0 / 700.0)


def test_a_negative_sqft_is_never_published_as_a_units_size() -> None:
    """The other side of the same coin. A negative square footage is not data;
    publishing it puts an impossible number on the wire and in front of the
    owner (`sqft: -50`), even though `psf` correctly declines to divide by it.

    STRENGTHENED ON RESUME (2026-07-30): this previously accepted `sqft is None`
    as a pass, i.e. it would have gone green on a fix that threw the reported
    640 away. That is a strictly worse outcome than today's — the comp loses a
    real measurement AND leaves every median — and it is the same shape the
    sibling zero-sqft test above rejects outright. The two are one rule with one
    argument, so they now assert the same thing.
    """
    comp = one_comp(
        record("real", listed="2025-03-01", removed="2025-04-01", sqft=640.0),
        record("bad", listed="2025-04-10", removed="2025-05-01", sqft=-50.0),
    )
    assert comp.sqft == 640.0, (
        f"the comp published sqft={comp.sqft}. A negative area is not an observation, so the "
        f"real 640 must win — reporting None instead would discard a measurement the pull "
        f"holds and drop the comp out of every median, which is worse than the bug."
    )
    assert comp.psf == pytest.approx(2000.0 / 640.0)


def test_absent_coordinates_do_not_become_a_real_place_on_the_map() -> None:
    """⚠ A FINDING FOR THE FIX, not a selector question.

    `extract_spells` does `float(record.get("latitude") or 0.0)`, so a record
    with no coordinates becomes `lat=0.0, lng=0.0` — an actual location in the
    Gulf of Guinea. Two consequences the fix must not walk into:

    * a completeness selector **cannot be written for `lat`/`lng`/`beds`** the
      way it can for `unit`/`baths`/`sqft`: the domain type has already thrown
      away the difference between "reported 0" and "not reported", so `is not
      None` is always true for them. Whatever rule the fix picks for
      coordinates, it is not "prefer the reported one" unless the coercion
      changes too;
    * the map is one of this release's named asks, and a comp at (0, 0) is a pin
      in the ocean.

    Zero comps in the committed pull sit at (0, 0) (measured), so this is a
    guard, not a repair.
    """
    comp = one_comp(
        record("has", listed="2025-03-01", removed="2025-04-01", lat=41.85, lng=-87.68),
        record("none", listed="2025-04-10", removed="2025-05-01", lat=None, lng=None),
    )
    assert (comp.lat, comp.lng) != (0.0, 0.0), (
        "a comp whose coordinates were reported by one observation and omitted by another "
        "ended up at (0, 0) — the Gulf of Guinea, and a pin the map will draw"
    )


def test_a_comps_coordinate_is_a_place_some_observation_actually_reported() -> None:
    """Latitude and longitude are not independent fields — they are one datum in
    two columns. Selecting them field-by-field can compose a point that no
    observation ever reported: A's latitude with B's longitude is a third
    location, several blocks from either.

    Zero chains in the committed pull disagree on coordinates (measured), so
    this is unreachable there and is the reason it is asserted here.
    """
    pairs = {(41.85, -87.68), (41.87, -87.70)}
    comp = one_comp(
        record("a", listed="2025-03-01", removed="2025-04-01", lat=41.85, lng=-87.68),
        record("b", listed="2025-04-10", removed="2025-05-01", lat=41.87, lng=-87.70),
    )
    assert (comp.lat, comp.lng) in pairs, (
        f"the comp is at {(comp.lat, comp.lng)}, which is neither of the two points the pull "
        f"reported ({sorted(pairs)}). Latitude and longitude must be taken from the SAME "
        f"observation, or the comp is placed somewhere nothing observed it."
    )


def test_no_identity_field_is_invented(
) -> None:
    """The general form, over a deliberately ragged chain: every identity value
    the comp publishes must equal a value one of its own spells reported.

    This is the invariant that survives whatever per-field rule the PM settles
    on. It forbids blending, defaulting and interpolating in one assertion, and
    it is the property version of the two specific cases above.
    """
    records = (
        record(
            "a", listed="2025-02-01", removed="2025-03-01",
            unit="Unit 5", lat=41.85, lng=-87.68, beds=3.0, baths=None, sqft=900.0,
        ),
        record(
            "b", listed="2025-03-10", removed="2025-04-01",
            unit="# 5", lat=41.86, lng=-87.69, beds=3.0, baths=2.0, sqft=None,
        ),
    )
    comp = one_comp(*records)
    chain = chains_of(*records)[0]
    for field in IDENTITY_FIELDS:
        observed = {getattr(spell, field) for spell in chain}
        assert getattr(comp, field) in observed, (
            f"comp.{field} == {getattr(comp, field)!r} was reported by no spell in its own "
            f"chain (observed: {sorted(observed, key=repr)}). A shaped comp may only restate "
            f"its evidence."
        )


# ===========================================================================
# COMPLETENESS TIES — added on resume, 2026-07-30
#
# The dispatch asked specifically for adversarial probes of the INPUT SPACE:
# "ties on completeness, all observations equally incomplete, a chain of one,
# conflicting non-null values, disagreement in a field nobody looked at." Four
# of those five were already covered above. The fifth — a genuine TIE on the
# completeness score — was not, and its sharp form turns out to expose a design
# fork that the row's own wording ("the most-complete observation") does not
# resolve. See the first test below; it is the reason this section exists.
# ===========================================================================


def test_complementary_observations_do_not_lose_evidence_to_each_other() -> None:
    """⚠⚠ THE DESIGN FORK — A PM RULING, NOT A QA CHOICE. Flagged, not locked.

    Two observations of one unit, each reporting exactly what the other omits:

        A: sqft 950, baths MISSING
        B: sqft MISSING, baths 2.5

    They **tie** on any whole-observation completeness score — each reported one
    optional attribute. So "pick the most complete OBSERVATION and take all
    seven fields from it" cannot help but discard one of the two reported
    values, whichever way the tie breaks. Per-FIELD selection ("for each field,
    prefer an observation that reported it") keeps both.

    That is a real fork, and row 9b's wording does not settle it:

    * **whole-observation** selection is internally consistent — the comp is a
      coherent snapshot of one real observation, and `lat`/`lng` stay paired
      automatically (see `test_a_comps_coordinate_is_a_place_some_observation_
      actually_reported`, which per-field selection can violate);
    * **per-field** selection is strictly more evidence, which is the exact
      argument the PM already accepted for `_completeness` and the argument this
      whole row rests on. Under it this comp gets both 950 and 2.5.

    ⚠ This test asserts only the part BOTH readings agree on: whatever is
    published was observed, and nothing is invented. It deliberately does NOT
    assert that both values survive — that would silently pick per-field
    selection on QA's authority. **PM: this needs a ruling before the developer
    guesses.** Unreachable on the committed pull (measured: only one chain
    disagrees about any identity field at all), so the choice is free of
    real-data consequences today and will not stay that way.
    """
    records = (
        record("a", listed="2025-03-01", removed="2025-04-01", sqft=950.0, baths=None),
        record("b", listed="2025-04-10", removed="2025-05-01", sqft=None, baths=2.5),
    )
    comp = one_comp(*records)
    chain = chains_of(*records)[0]
    for field in ("sqft", "baths"):
        observed = {getattr(spell, field) for spell in chain}
        assert getattr(comp, field) in observed, (
            f"comp.{field}={getattr(comp, field)!r} was reported by no spell in this chain "
            f"(observed {sorted(observed, key=repr)})"
        )
    assert (comp.sqft, comp.baths) != (None, None), (
        "both observations reported something and the comp published neither — a tie on "
        "completeness must not resolve to the emptiest possible comp"
    )


def test_a_tie_on_completeness_resolves_the_same_way_every_time() -> None:
    """The half of the fork above that is NOT a ruling: whichever way a tie
    breaks, it must break identically on every run and in every input order.

    A completeness score is a coarse integer, so ties are common — far more
    common than the conflicting-values case — and a tie broken by list position
    is the same positional accident this row exists to remove, one level down.
    """
    records = [
        record("a", listed="2025-03-01", removed="2025-04-01", sqft=950.0, baths=None),
        record("b", listed="2025-04-10", removed="2025-05-01", sqft=None, baths=2.5),
        record("c", listed="2025-05-05", removed="2025-06-01", sqft=None, baths=None),
    ]
    seen = set()
    for order in permutations(records):
        comp = one_comp(*order)
        seen.add(tuple(repr(getattr(comp, field)) for field in IDENTITY_FIELDS))
    assert len(seen) == 1, (
        f"a completeness tie resolved {len(seen)} different ways depending on input order: "
        f"{sorted(seen)}"
    )


def test_the_most_complete_observation_is_not_always_the_one_that_saw_the_removal() -> None:
    """The interaction between this row's rule and `_completeness`'s ordering,
    on a chain rather than on a single listing start.

    `_completeness` deliberately ranks field completeness BELOW the removal
    components, so that a rich still-active observation cannot beat one that saw
    the unit leave the market. That ordering protects the collapse of two
    observations of ONE listing start. This row's selector runs over a whole
    CHAIN, where the two concerns are independent: the chain's end is already
    fixed by `_chain_end`, so taking `sqft` from a still-open spell costs
    nothing at all.

    Asserted so a fix does not import `_completeness`'s ordering wholesale into
    a place where its reason does not apply, and thereby refuse the only
    reported square footage in the chain because it came from an open spell.
    """
    comp = one_comp(
        record("closed", listed="2025-03-01", removed="2025-04-01", sqft=None, baths=None),
        record("open", listed="2025-04-10", removed=None, sqft=880.0, baths=2.0),
    )
    assert comp.censored is True, (
        "harness precondition: the chain ends on an open spell, so the comp is censored"
    )
    assert comp.sqft == 880.0, (
        f"got sqft={comp.sqft}. The only observation that reported a square footage was the "
        f"still-open one. Censoring is decided by dates and is already correct above; refusing "
        f"its sqft buys nothing and discards the chain's only measurement."
    )


def test_a_chain_of_one_open_spell_reports_what_it_observed() -> None:
    """The degenerate case, stated separately from
    `test_a_single_spell_chain_is_untouched` because censoring is the branch a
    selector is most likely to special-case by accident. 39 of the committed
    pull's 567 comps are censored."""
    comp = one_comp(record("solo", listed="2026-06-01", removed=None, sqft=615.0, baths=1.0))
    assert comp.censored is True
    assert comp.relist_count == 0
    assert (comp.sqft, comp.baths) == (615.0, 1.0)


# ===========================================================================
# determinism / reproducibility
# ===========================================================================


def test_selection_does_not_depend_on_the_order_records_arrive_in() -> None:
    """The standing reproducibility requirement, applied to this change.

    `extract_spells` already promises that "the winner is chosen from the
    spells' own values, never from their input order, so shaping cannot depend
    on which response a copy arrived in". A position-derived identity selector
    is the same defect one function later, and pagination order is not
    something the owner controls.
    """
    records = [
        record("a", listed="2025-02-01", removed="2025-03-01", sqft=900.0, unit="Unit 5"),
        record("b", listed="2025-03-10", removed="2025-04-01", sqft=None, unit="# 5"),
        record("c", listed="2025-04-05", removed="2025-05-01", sqft=None, unit="Unit 5"),
    ]
    results = set()
    for order in permutations(records):
        comp = one_comp(*order)
        results.add(tuple(repr(getattr(comp, field)) for field in IDENTITY_FIELDS))
    assert len(results) == 1, (
        f"the same three observations shaped into {len(results)} different comps depending on "
        f"the order they were listed in: {sorted(results)}"
    )


def test_a_split_active_inactive_pair_shapes_the_same_from_either_response() -> None:
    """The real arrival shape of the defect. On the committed pull the two
    records behind `2350 S Leavitt St 1R` are two listing ids for one physical
    unit; in general the same id also appears in BOTH responses when its status
    flips mid-pagination (`_dedupe_by_id`). Which half a record was in must
    reach no number."""
    active = record("flip", listed="2025-05-01", removed=None, sqft=None)
    inactive = record("flip", listed="2025-05-01", removed="2025-06-01", sqft=810.0)
    forward = shape_raw_pull([active], [inactive], Config(), AS_OF, *FULL_YEAR)
    reverse = shape_raw_pull([inactive], [active], Config(), AS_OF, *FULL_YEAR)
    assert [c.model_dump_json() for c in forward] == [c.model_dump_json() for c in reverse], (
        "shaping depends on which response the record arrived in"
    )
    assert forward[0].sqft == 810.0, (
        f"one of the two copies of this listing reported squareFootage=810; got "
        f"{forward[0].sqft}"
    )


# ===========================================================================
# is `chain[-1]` even well defined? — the equal-listed-dates probe
# ===========================================================================


def test_two_observations_sharing_a_listed_date_collapse_to_one_spell() -> None:
    """⚠ THE PROBE THE PM EXPECTED TO FIND A NON-DETERMINISM, AND THE ANSWER.

    If a chain could hold two spells with the same `listed` date, then "the last
    spell by listed date" would be decided by a tie in a sort — a positional
    accident promoted to a non-reproducible one. **It cannot.**
    `extract_spells` keys its winners `dict[date, Spell]`, so two observations
    of one listing start collapse to one row *before* stitching, and every
    chain therefore has strictly increasing listed dates.

    So `chain[-1]` today is deterministic-but-arbitrary, not unstable — which is
    a materially different (and better) starting point than the row assumed.
    This test and the next make that guarantee explicit instead of incidental,
    because the whole of row 9b's determinism rests on it and nothing currently
    states it. 495 of the committed pull's records restate their own newest
    history event on the same date, so this collapse fires constantly.
    """
    records = (
        record("a", listed="2025-03-01", removed="2025-04-01", sqft=None),
        record("b", listed="2025-03-01", removed="2025-04-01", sqft=760.0),
    )
    spells = extract_spells(list(records))
    assert len(spells) == 1, (
        f"two observations of the listing that started 2025-03-01 produced {len(spells)} "
        f"spells. They are one vacancy, not two, and if they survive as two rows then "
        f"'the last spell by listed date' is decided by a sort tie."
    )
    assert spells[0].sqft == 760.0, (
        f"the collapse kept the observation with no square footage (sqft={spells[0].sqft}) "
        f"over the one that reported 760. Both agree on the removal, so `_completeness`'s "
        f"field-completeness component is what decides — F4-S3 added it for exactly this."
    )


def test_every_chain_has_strictly_increasing_listed_dates() -> None:
    """The guarantee the previous test rests on, stated over a chain rather than
    a spell list. If this ever fails, `chain[-1]` stops being well defined and
    row 9b's selector — whatever it is — inherits a tie it cannot break from
    values."""
    records = (
        record("a", listed="2025-02-01", removed="2025-03-01"),
        record("b", listed="2025-02-01", removed="2025-03-05"),
        record("c", listed="2025-03-10", removed="2025-04-01"),
    )
    for chain in chains_of(*records):
        listed = [spell.listed for spell in chain]
        assert listed == sorted(set(listed)), (
            f"a chain holds duplicate or unsorted listed dates: {listed}"
        )


def test_a_removal_bearing_observation_still_outranks_a_richer_active_one() -> None:
    """The ordering `_completeness` protects, re-asserted because this row is
    about to lean on that function's third component.

    An observation that saw the unit *removed* must beat one that reported more
    attributes but still had it active, or a comp that actually leased comes out
    censored — NORTH_STAR's "single most important distinction in the whole
    system". A fix that promotes field completeness above the removal
    components to get `sqft` right would buy `sqft` at the price of a censoring
    error, which is a far worse trade.
    """
    spells = extract_spells(
        [
            record("rich-active", listed="2025-03-01", removed=None, sqft=700.0, baths=2.0),
            record("thin-removed", listed="2025-03-01", removed="2025-05-01", sqft=None, baths=None),
        ]
    )
    assert len(spells) == 1
    assert spells[0].removed == date(2025, 5, 1), (
        "an observed removal lost to a still-active observation that reported more fields. "
        "That censors a comp the pull watched leave the market."
    )


# ===========================================================================
# what a fix must not cost — the curation key
# ===========================================================================


def test_changing_which_observation_supplies_the_unit_does_not_move_the_comp_key() -> None:
    """F13-S1 [INVARIANT]: curation is addressed by normalized address+unit,
    "never by listing id — ids churn between pulls and a curation state keyed
    on a churning id silently loses the user's work".

    `unit` is a *display* string whose formatting varies between observations
    (`Unit 1R` vs `# 1R`). Re-choosing which observation supplies it is
    therefore allowed to change what the row prints, and is **not** allowed to
    change the key the user's toggles are stored against. `comp_key` normalizes
    both spellings to `1r`, so this holds today — asserted so that a future
    normalization change cannot quietly invalidate every saved workspace.
    """
    records = (
        record("a", listed="2025-02-01", removed="2025-03-01", unit="Unit 1R", sqft=700.0),
        record("b", listed="2025-03-10", removed="2025-04-01", unit="# 1R", sqft=None),
    )
    comp = one_comp(*records)
    expected = comp_key(ADDRESS, "Unit 1R")
    assert comp_key(comp.address, comp.unit) == expected, (
        f"the comp's key is {comp_key(comp.address, comp.unit)!r} but the same unit keys to "
        f"{expected!r} under its other reported spelling. Whichever display string wins, the "
        f"key must not move — a workspace's weights are stored against it (F13-S1)."
    )


def test_a_reported_unit_designator_is_not_discarded_because_a_later_observation_omitted_it() -> None:
    """⚠ ADDED AT VERIFY (2026-07-30) — a SURVIVING MUTANT, measured not argued.

    Mutating the shipped selector's `unit` clause back to `chain[-1].unit` and
    running the whole suite produced **exactly the 3 documented Windows failures
    and the baseline pass count** — i.e. nothing in 1799 tests noticed. The
    field's rule was implemented and left entirely unpinned.

    It survived because every existing case has a last spell that reports a
    designator, so "most recent usable" and `chain[-1]` return the same string.
    This is the one shape that separates them: the last observation omitted the
    designator and an earlier one did not.

    The behaviour asserted is the row's own rule applied to `unit` — a reported
    value beats an absent one — so this pins what the implementation already
    does. It is a `[DEFAULT]` (the PM has explicitly reserved the `unit` rule),
    so a ruling that changes it changes THIS test, deliberately, instead of
    sliding through green.
    """
    # One listing id, observed twice: `_group_key` is computed per id, so two
    # DIFFERENT ids that disagree about the designator land in two groups and
    # never share a chain at all — there would be nothing to select between.
    comp = one_comp(
        record(SHARED_ID, listed="2025-03-01", removed="2025-04-01", unit="Unit 2"),
        record(SHARED_ID, listed="2025-04-10", removed="2025-05-01", unit=None),
    )
    assert comp.unit == "Unit 2", (
        f"the comp published unit={comp.unit!r}. One of its own observations named this unit "
        f"'Unit 2' and a later one simply did not mention a designator — which is not evidence "
        f"that the unit has none."
    )


def test_the_unit_selector_is_not_purely_presentational_after_all() -> None:
    """⚠⚠ A CORRECTION TO THIS ROW'S OWN FRAMING, for the PM's `unit` ruling.

    Row 9b and `_select_unit`'s docstring both justify leaving the `unit` rule
    open on the grounds that "`comp_key` normalises all spellings, so F13-S1 is
    safe either way". **That is true for spelling variants and false for the
    only case the selector actually changes anything in.**

    `Unit 1R` / `# 1R` / `1r` all normalise to the same token, so choosing
    between two *reported* spellings really is display-only — that is what
    `test_changing_which_observation_supplies_the_unit_does_not_move_the_comp_key`
    above covers, and it still holds. But `_select_unit` only ever departs from
    `chain[-1]` when the last spell reported **no** designator, and `comp_key`
    treats an absent designator as *distinct* from a present one ("the whole
    building" is not "the building's apartment 2"). So in exactly the case the
    rule fires, the key moves:

        chain[-1] rule  ->  `10 probe st|`      (no designator)
        shipped rule    ->  `10 probe st|2`

    Both keys are defensible and the shipped one is arguably better — it is the
    more specific address, and it is what the chain actually observed. The point
    is that the choice is **not free**: it reaches F13-S1's curation key, so it
    is a ruling with a consequence rather than a formatting preference.

    Unreachable on the committed pull — measured at verify, `unit` is identical
    on all 567 comps before and after this row — so nothing an owner has saved
    can move today.
    """
    # One listing id, observed twice: `_group_key` is computed per id, so two
    # DIFFERENT ids that disagree about the designator land in two groups and
    # never share a chain at all — there would be nothing to select between.
    comp = one_comp(
        record(SHARED_ID, listed="2025-03-01", removed="2025-04-01", unit="Unit 2"),
        record(SHARED_ID, listed="2025-04-10", removed="2025-05-01", unit=None),
    )
    assert comp_key(comp.address, comp.unit) == comp_key(ADDRESS, "Unit 2"), (
        f"key={comp_key(comp.address, comp.unit)!r}. Whichever way the PM rules on the `unit` "
        f"selector, record here that the ruling MOVES THE CURATION KEY in the absent-designator "
        f"case — it is not the display-only choice the row's text describes."
    )
    assert comp_key(comp.address, comp.unit) != comp_key(ADDRESS, None), (
        "the two candidate rules key this comp differently; if this ever stops being true, "
        "`comp_key`'s treatment of an absent designator has changed and F13-S1 needs re-reading"
    )


def test_re_selecting_identity_fields_changes_nothing_about_the_chains_dom() -> None:
    """The blast-radius guard. F4-S3 fixed `_chain_end` ("effectiveDOM = final
    removal − first listed") and this row must not disturb it: DOM, censoring,
    the removal class, `initial_ask`, `relist_count`, `gap_days` and
    `cohort_year` are all functions of dates and prices, and none of them is an
    identity field.

    The chain below is the real shape that made F4-S3's defect reachable — the
    last spell by listed date is NOT the last to end — so a fix that reaches
    for `chain[-1]`'s neighbours carelessly shows up right here.
    """
    comp = one_comp(
        record("a", listed="2025-03-01", removed="2025-05-30", sqft=700.0, price=2400.0),
        record("b", listed="2025-04-01", removed="2025-04-20", sqft=None, price=2200.0),
    )
    assert comp.first_listed == date(2025, 3, 1)
    assert comp.effective_dom == (date(2025, 5, 30) - date(2025, 3, 1)).days, (
        f"effective_dom={comp.effective_dom}: the chain must still run to its LATEST observed "
        f"removal (2025-05-30), not to its last spell by listed date (2025-04-20)"
    )
    assert comp.censored is False
    assert comp.initial_ask == 2400.0, "initialAsk is the FIRST spell's price (F4-S3)"
    assert comp.relist_count == 1
    assert comp.gap_days == 0, "overlapping spells were never off market"
    assert comp.sqft == 700.0, "and the identity fix still applies to this chain"
