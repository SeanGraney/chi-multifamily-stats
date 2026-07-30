"""Row 9b — the selection rules `_select_identity` actually implements.

Developer-authored (AGENT_DEVELOPER.md step 4: "the supporting unit tests QA's
plan calls for but didn't write itself"). QA's two unit files pin what is
decidable *without* a ruling — a reported value never loses to an absent one,
nothing is invented, selection is deterministic — and deliberately stop short of
the two places where a choice had to be made. This file pins those choices, so
that changing one is a deliberate edit to a named test rather than a silent
drift back to a coin flip.

WHAT THIS FILE CLOSES THAT QA'S DELIBERATELY LEFT OPEN
------------------------------------------------------
1. **PER-FIELD vs. WHOLE-OBSERVATION selection.** QA's
   `test_complementary_observations_do_not_lose_evidence_to_each_other` asserts
   only what *both* readings agree on, because picking between them was the PM's
   call and not QA's. The PM ruled **per-field** (row 9b, 2026-07-30), on the
   grounds that whole-observation selection contradicts this row's own
   justification — "strictly more evidence, and it cannot invent a number" — in
   exactly the tie case the row was written for.
   `test_complementary_observations_both_survive_intact` is the assertion that
   ruling needs: it **fails** if selection ever reverts to whole-observation.

2. **`lat`/`lng` are an ATOMIC PAIR**, the one exception to (1). QA's
   `test_a_comps_coordinate_is_a_place_some_observation_actually_reported`
   covers two fully-reported points. It does not cover the case that actually
   discriminates a per-field implementation from an atomic one — two
   *half*-reported observations — which is
   `test_a_coordinate_is_never_composed_from_two_half_reported_observations`
   below.

3. **The tie-break direction** when two observations both report a usable value
   and disagree. QA asserts only that the answer is one of the observed values
   and is stable. It is a `[DEFAULT]`; the direction chosen is pinned here.

Layer 1 throughout, driving `shape_raw_pull` — the same seam QA used, and for
the same reason: `_select_identity` is private, so nothing here constrains how
the selection is structured, only what it decides.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.pipeline.shape import extract_spells, shape_raw_pull, stitch
from rentcomp.storage.config import Config

AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")
ADDRESS = "77 W Selector St"

#: The seven fields row 9b moved off `chain[-1]`.
IDENTITY_FIELDS = ("address", "unit", "lat", "lng", "beds", "baths", "sqft")


def record(
    listing_id: str,
    *,
    listed: date,
    removed: date | None,
    price: float = 2000.0,
    address: str = ADDRESS,
    unit: str | None = "Unit 1",
    lat: float | None = 41.85,
    lng: float | None = -87.68,
    beds: float | None = 3.0,
    baths: float | None = 2.0,
    sqft: float | None = 1000.0,
) -> dict:
    """One raw RentCast-shaped record; `None` means the response OMITTED it."""
    return {
        "id": listing_id,
        "addressLine1": address,
        "addressLine2": unit,
        "latitude": lat,
        "longitude": lng,
        "bedrooms": beds,
        "bathrooms": baths,
        "squareFootage": sqft,
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "removedDate": None if removed is None else f"{removed.isoformat()}T00:00:00.000Z",
        "price": price,
        "history": {},
    }


#: Several observations of ONE listing id — `_dedupe_by_id`'s documented case
#: (a listing whose status flipped mid-pagination, or whose `history` restates
#: it). Used wherever a chain's observations must disagree about `unit`:
#: `_group_key` is computed per listing id, so two *different* ids that spell
#: their unit differently land in two groups and never share a chain at all.
SHARED_ID = "one-listing"


def one_comp(*records: dict):
    comps = shape_raw_pull([], list(records), Config(), AS_OF, *FULL_YEAR)
    assert len(comps) == 1, (
        f"harness precondition: these {len(records)} record(s) must stitch into ONE comp, got "
        f"{len(comps)}"
    )
    return comps[0]


def one_chain(*records: dict):
    chains = stitch(extract_spells(list(records)), Config().stitch_gap_days)
    assert len(chains) == 1, f"harness precondition: one chain, got {len(chains)}"
    return chains[0]


# ===========================================================================
# 1. THE DESIGN FORK, CLOSED — per-field, not whole-observation
# ===========================================================================


def test_complementary_observations_both_survive_intact() -> None:
    """⭐ THE TEST THAT PINS THE PM's RULING. If someone reverts `_select_identity`
    to "pick the most-complete observation and take all seven fields from it",
    THIS is the test that fails.

    Two observations of one unit, each reporting exactly what the other omits:

        A: sqft 950, baths absent
        B: sqft absent, baths 2.5

    They **tie** on any whole-observation completeness score — one reported
    attribute each — so whole-observation selection must discard one of the two
    genuinely reported values whichever way the tie breaks, publishing either
    `(950, None)` or `(None, 2.5)`. Both of those satisfy every assertion in
    QA's sibling test, which is why that test could not close this and this one
    can: it demands **both**.

    The argument is the row's own, applied consistently. "Preferring a reported
    value over an absent one is strictly more evidence and cannot invent a
    number" is the entire justification for row 9b; a rule that discards a
    reported value in the tie case contradicts its own stated reason. Per-field
    selection keeps 950 *and* 2.5, and invents nothing — each value is read off
    the observation that reported it.

    Unreachable on the committed pull (exactly one chain disagrees about any
    identity field at all), so this costs nothing on real data today. It exists
    so that a coin flip is not left in the code.
    """
    comp = one_comp(
        record("a", listed=date(2025, 3, 1), removed=date(2025, 4, 1), sqft=950.0, baths=None),
        record("b", listed=date(2025, 4, 10), removed=date(2025, 5, 1), sqft=None, baths=2.5),
    )
    assert comp.sqft == 950.0, (
        f"sqft={comp.sqft}. Observation A reported 950 square feet and the comp published "
        f"something else — whole-observation selection discarded it to keep B's bathroom count. "
        f"Per-field selection (PM ruling, row 9b) keeps both."
    )
    assert comp.baths == 2.5, (
        f"baths={comp.baths}. Observation B reported 2.5 bathrooms and the comp published "
        f"something else — whole-observation selection discarded it to keep A's square footage."
    )


def test_three_way_complementary_observations_all_survive() -> None:
    """The same rule under more pressure: three observations, each the only one
    to report a different field. A whole-observation selector can keep at most
    one of the three; per-field keeps all three.

    `unit` is included here as a third *field*, not as an endorsement of any
    particular spelling rule for it (that is an open PM question) — what is
    asserted is only that a reported designator is not thrown away in favour of
    an observation that had none.
    """
    comp = one_comp(
        record(
            SHARED_ID, listed=date(2025, 3, 1), removed=date(2025, 4, 1),
            sqft=880.0, baths=None, unit=None,
        ),
        record(
            SHARED_ID, listed=date(2025, 4, 10), removed=date(2025, 5, 1),
            sqft=None, baths=1.5, unit=None,
        ),
        record(
            SHARED_ID, listed=date(2025, 5, 20), removed=date(2025, 6, 1),
            sqft=None, baths=None, unit="Apt 3C",
        ),
    )
    assert (comp.sqft, comp.baths, comp.unit) == (880.0, 1.5, "Apt 3C"), (
        f"got {(comp.sqft, comp.baths, comp.unit)}; each of the three observations was the only "
        f"one to report one of these fields, so all three must survive"
    )


# ===========================================================================
# 2. THE EXCEPTION — lat/lng move together, always
# ===========================================================================


def test_a_coordinate_is_never_composed_from_two_half_reported_observations() -> None:
    """⭐ THE TEST THAT DISCRIMINATES ATOMIC FROM PER-FIELD. QA's sibling covers
    two *fully* reported points, which a naive per-field selector also passes.
    This is the case that separates them.

    Observation A reports a latitude and no longitude; B reports a longitude and
    no latitude. Selecting each column independently — "prefer the reported one"
    — composes `(41.85, -87.70)`, a point **neither observation reported and
    nothing ever observed**. That is not more evidence, it is fabricated
    evidence, and it is the exact NORTH_STAR failure mode ("every number this
    tool produces must trace back to real, observed comps") that the rest of
    this selector exists to serve. It is also a pin the map will draw.

    So latitude and longitude are one datum in two columns, and they move
    together from a single observation. Here neither observation reported a
    complete point, so the comp falls back to the last spell's pair verbatim —
    which is still something one observation reported, and still not composed.
    """
    reported_points = {(41.85, 0.0), (0.0, -87.70)}
    comp = one_comp(
        record("a", listed=date(2025, 3, 1), removed=date(2025, 4, 1), lat=41.85, lng=None),
        record("b", listed=date(2025, 4, 10), removed=date(2025, 5, 1), lat=None, lng=-87.70),
    )
    assert (comp.lat, comp.lng) != (41.85, -87.70), (
        "the comp's coordinate was COMPOSED — A's latitude with B's longitude — producing a "
        "location neither observation reported. `lat` and `lng` are one datum (PM ruling, row "
        "9b) and must be taken from the same observation."
    )
    assert (comp.lat, comp.lng) in reported_points, (
        f"the comp is at {(comp.lat, comp.lng)}, which is neither of the two (partial) points "
        f"the records carried: {sorted(reported_points)}"
    )


def test_a_complete_coordinate_beats_a_half_reported_one() -> None:
    """The pair is preferred *as a pair*: an observation that reported both
    columns outranks a later one that reported only half. The half-reported
    observation's surviving column is not salvaged into the answer, because
    doing so is the composition the test above forbids."""
    comp = one_comp(
        record("full", listed=date(2025, 3, 1), removed=date(2025, 4, 1), lat=41.85, lng=-87.68),
        record("half", listed=date(2025, 4, 10), removed=date(2025, 5, 1), lat=None, lng=-87.99),
    )
    assert (comp.lat, comp.lng) == (41.85, -87.68), (
        f"got {(comp.lat, comp.lng)}; the only observation that reported a whole point was the "
        f"earlier one, and half a point is not a place"
    )


# ===========================================================================
# 3. THE TIE-BREAK DIRECTION — a [DEFAULT], pinned so a change is deliberate
# ===========================================================================


def test_the_most_recent_usable_observation_wins_when_two_conflict() -> None:
    """⚠ A `[DEFAULT]`, not an invariant — pinned so that changing it is an edit
    to this test rather than a silent drift.

    When two observations both report a usable value and disagree (700 vs 900
    square feet), no honesty argument decides between them: neither is more
    evidence than the other, and QA's
    `test_two_conflicting_reported_sqfts_resolve_deterministically` correctly
    asserts only that the answer is observed and stable.

    The rule chosen is **the most recent usable observation** — the chain's
    latest spell that reported the field. Two reasons, in order of weight:

    * it is the **smallest possible deviation** from the pre-row behaviour. It
      returns exactly `chain[-1]`'s value whenever `chain[-1]` reported the
      field, so the change is provably confined to fields the last spell said
      nothing about (see `test_the_last_spells_value_survives_whenever_it_
      reported_one`). Measured on the committed pull: exactly one field on
      exactly one comp moves;
    * it has an argument of its own — the freshest description of a unit that
      may have been re-measured or re-configured between listings.
    """
    comp = one_comp(
        record("earlier", listed=date(2025, 3, 1), removed=date(2025, 4, 1), sqft=700.0),
        record("later", listed=date(2025, 4, 10), removed=date(2025, 5, 1), sqft=900.0),
    )
    assert comp.sqft == 900.0, (
        f"sqft={comp.sqft}. Both observations reported a usable area, so the tie-break decides: "
        f"the most recent one (900). If this is being changed deliberately, change it here and "
        f"say so — it is a [DEFAULT]."
    )


def test_an_unusable_recent_value_does_not_win_the_tie_break() -> None:
    """"Most recent" ranks below "usable", never above it — the two rules
    compose in one order only.

    A reported `0` is not a smaller measurement than 700, it is the absence of
    one wearing a number (`StitchedComp.psf` already refuses to divide by it).
    So recency only ever chooses *among* usable observations; it never promotes
    an unusable one. Asserted at all three unusable shapes at once.
    """
    for bad in (0.0, -50.0, None):
        comp = one_comp(
            record("real", listed=date(2025, 3, 1), removed=date(2025, 4, 1), sqft=640.0),
            record("bad", listed=date(2025, 4, 10), removed=date(2025, 5, 1), sqft=bad),
        )
        assert comp.sqft == 640.0, f"a later sqft={bad!r} displaced a real 640; got {comp.sqft}"


# ===========================================================================
# 4. NO USABLE OBSERVATION ANYWHERE — the structural guarantee
# ===========================================================================


@pytest.mark.parametrize("bad", [0.0, -50.0])
def test_a_chain_whose_only_area_is_unusable_publishes_no_area(bad: float) -> None:
    """A DELIBERATE CHOICE, flagged in the handoff: a chain in which *no*
    observation reported a usable area publishes `sqft=None`, not the unusable
    number.

    QA's `test_no_comp_publishes_a_non_positive_square_footage` asserts this
    over the real pull, where it holds only because zero records happen to carry
    `squareFootage <= 0` — a fixture coincidence, and F4-S8's mutant-M8 lesson
    is that inherited coincidence is worth nothing. This makes it structural:
    the same predicate that stops an unusable value winning a tie also stops it
    being published when it is the only thing on offer.

    Nothing is discarded by this. A non-positive area is not a measurement, so
    there is no evidence to lose — and `psf` was already `None` for such a comp,
    meaning it was already excluded from every median. What changes is only that
    the row prints "unknown" rather than `0` or `-50`, which is what the pipeline
    actually knows.
    """
    comp = one_comp(record("solo", listed=date(2025, 6, 1), removed=date(2025, 7, 1), sqft=bad))
    assert comp.sqft is None, (
        f"the comp published sqft={comp.sqft}. No observation reported a usable area, so the "
        f"honest answer is 'unknown' — publishing an impossible size puts it on the wire and in "
        f"front of the owner."
    )
    assert comp.psf is None


def test_a_chain_that_reported_no_unit_still_reports_none() -> None:
    """The absent-designator case: no observation named a unit, so the comp does
    not invent one. `comp_key` treats a missing unit as distinct from any present
    unit ("the whole building" is not "the building's apartment 2"), so inventing
    one here would silently re-key the comp."""
    comp = one_comp(
        record("a", listed=date(2025, 3, 1), removed=date(2025, 4, 1), unit=None),
        record("b", listed=date(2025, 4, 10), removed=date(2025, 5, 1), unit=None),
    )
    assert comp.unit is None


# ===========================================================================
# 5. `beds` — the field this row could NOT fix, pinned as such
# ===========================================================================


def test_a_studio_is_never_promoted_out_of_being_a_studio() -> None:
    """`beds` is deliberately still taken from the chain's last spell, and this
    is the assertion that says why the obvious "prefer a non-zero" repair is
    wrong.

    `extract_spells` coerces a missing `bedrooms` to `0.0`, so a `Spell` cannot
    distinguish "not reported" from "reported zero" — and `0` is a perfectly
    real bedroom count. A selector that preferred a non-zero `beds` would
    silently convert every studio in a multi-spell chain into a one-bedroom,
    which is a fabricated attribute: strictly worse than the positional accident
    this row removes, and in a field the committed pull never exercises
    (measured: 539 records, `bedrooms` is never absent and never 0 — only 3 and
    4 occur).
    """
    comp = one_comp(
        record("a", listed=date(2025, 3, 1), removed=date(2025, 4, 1), beds=0.0),
        record("b", listed=date(2025, 4, 10), removed=date(2025, 5, 1), beds=0.0),
    )
    assert comp.beds == 0.0, f"a studio was published as a {comp.beds}-bedroom"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN AND DISCLOSED (row 9b handoff): `beds` cannot be selected honestly while "
        "`extract_spells` coerces a missing `bedrooms` to 0.0 — the domain type has already "
        "thrown away the difference between 'reported 0' (a studio) and 'not reported'. Fixing "
        "it means making `Spell.beds` optional, which reaches `models/domain.py` and the wire "
        "type, and is therefore not this row's change. Unreachable on the committed pull: "
        "`bedrooms` is never absent and never 0 across all 539 records. When the domain "
        "boundary is repaired, this test XPASSes and strict mode FAILS IT, demanding the marker "
        "be removed — which is the intent."
    ),
)
def test_a_reported_bedroom_count_is_not_discarded_because_a_later_record_omitted_it() -> None:
    """The `beds` half of this row's own acceptance criterion, which this
    implementation does NOT satisfy — recorded as an executable disclosure
    rather than a sentence in a handoff nobody will re-read.

    The first observation reports three bedrooms; the second omits `bedrooms`
    entirely. The comp publishes `0.0` — a studio — while its own chain holds a
    reported 3. That is the identical defect row 9b exists to remove, hiding in
    the one field whose coercion makes it invisible to any predicate.
    """
    comp = one_comp(
        record("early", listed=date(2025, 3, 1), removed=date(2025, 4, 1), beds=3.0),
        record("late", listed=date(2025, 4, 10), removed=date(2025, 5, 1), beds=None),
    )
    assert comp.beds == 3.0


# ===========================================================================
# 6. PROPERTIES — over generated chains
# ===========================================================================

_USABLE_AREAS = st.sampled_from([600.0, 725.0, 900.0, 1100.0])
_UNUSABLE_AREAS = st.sampled_from([None, 0.0, -50.0])
_AREAS = st.one_of(_USABLE_AREAS, _UNUSABLE_AREAS)
_BATHS = st.sampled_from([None, 1.0, 1.5, 2.0, 2.5])
_UNITS = st.sampled_from([None, "Unit 1", "# 1", "Apt 1"])
_POINTS = st.sampled_from([(41.85, -87.68), (41.87, -87.70), (None, None), (41.9, None)])


@st.composite
def _chain_records(draw: st.DrawFn) -> list[dict]:
    """2-4 observations of one listing, spaced so they always stitch into ONE
    chain (each gap is 10 days, well under the 42-day threshold), disagreeing
    freely about every optional attribute.

    They share a listing id (see `SHARED_ID`) so that the `unit` designator can
    vary — including between "reported" and "absent" — without the records
    landing in different `comp_key` groups and never meeting in a chain.
    """
    n = draw(st.integers(min_value=2, max_value=4))
    base = date(2025, 2, 1)
    records = []
    for i in range(n):
        lat, lng = draw(_POINTS)
        listed = base + timedelta(days=40 * i)
        records.append(
            record(
                SHARED_ID,
                listed=listed,
                removed=listed + timedelta(days=30),
                unit=draw(_UNITS),
                lat=lat,
                lng=lng,
                baths=draw(_BATHS),
                sqft=draw(_AREAS),
            )
        )
    return records


@given(_chain_records())
@settings(max_examples=200, deadline=None)
def test_no_identity_value_is_ever_invented(records: list[dict]) -> None:
    """The property that survives every rule choice above: each identity value a
    comp publishes was reported by one of its own spells.

    QA asserts this on one hand-built ragged chain and on the real pull; this
    asserts it over the generated input space, where `sqft` is drawn from usable
    *and* unusable values and coordinates from complete *and* half-reported
    ones. It forbids blending, averaging, defaulting and interpolating in one
    statement — and, together with the coordinate clause, forbids composing.
    """
    comp = one_comp(*records)
    chain = one_chain(*records)
    for field in IDENTITY_FIELDS:
        observed = {getattr(spell, field) for spell in chain}
        if field == "sqft" and comp.sqft is None:
            continue  # "no usable area in this chain" — an absence, not a value
        if field == "unit" and comp.unit is None:
            continue
        if field == "baths" and comp.baths is None:
            continue
        assert getattr(comp, field) in observed, (
            f"comp.{field}={getattr(comp, field)!r} was reported by no spell in its own chain "
            f"(observed {sorted(observed, key=repr)})"
        )
    assert (comp.lat, comp.lng) in {(spell.lat, spell.lng) for spell in chain}, (
        f"the comp's coordinate {(comp.lat, comp.lng)} was composed from two observations; it "
        f"must be a point one spell reported whole"
    )


@given(_chain_records())
@settings(max_examples=200, deadline=None)
def test_the_last_spells_value_survives_whenever_it_reported_one(records: list[dict]) -> None:
    """THE BLAST-RADIUS BOUND, as a property.

    The tie-break is "most recent usable", so wherever the last spell reported a
    usable value, the comp publishes exactly that value — i.e. this row's change
    is *provably confined* to fields about which `chain[-1]` said nothing. That
    is what makes the measured real-pull effect (one field, one comp) a
    consequence of the rule rather than a lucky fixture.

    It is also the property that would break first if someone replaced the rule
    with "the earliest observation" or "the most complete observation", so it
    pins the direction as well as the bound.
    """
    comp = one_comp(*records)
    last = one_chain(*records)[-1]

    if last.sqft is not None and last.sqft > 0.0:
        assert comp.sqft == last.sqft
    if last.baths is not None:
        assert comp.baths == last.baths
    if last.unit is not None and last.unit.strip():
        assert comp.unit == last.unit
    if last.lat != 0.0 and last.lng != 0.0:
        assert (comp.lat, comp.lng) == (last.lat, last.lng)
    assert comp.address == last.address
    assert comp.beds == last.beds


@given(_chain_records())
@settings(max_examples=200, deadline=None)
def test_a_published_area_is_always_a_usable_measurement(records: list[dict]) -> None:
    """`sqft` is either absent or a positive number — never `0`, never negative.

    The same predicate `StitchedComp.psf` already applies, enforced one stage
    earlier so it is a property of what the pipeline *publishes* and not only of
    what it divides by.
    """
    comp = one_comp(*records)
    assert comp.sqft is None or comp.sqft > 0.0, f"published sqft={comp.sqft}"
    assert (comp.psf is None) == (comp.sqft is None), (
        "a comp has a $/sqft exactly when it has a usable area"
    )
