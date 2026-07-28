"""F4-S2 [INVARIANT] — spell extraction and dedupe-on-id. QA-authored,
written RED before the developer starts (AGENT_QA.md protocol).

Layer 1 (pytest unit) throughout. Decision procedure Q1: every assertion
below is a pure function called with plain dicts and dates — no request body,
no curation state, no browser.

CONTRACT ITEM REQUESTED FROM THE DEVELOPER (one new export, nothing more)
------------------------------------------------------------------------
    rentcomp.pipeline.shape.extract_spells(
        records: Sequence[dict],
    ) -> Sequence[Spell]

    rentcomp.pipeline.shape.Spell          # public, with at least:
        .listed:  datetime.date
        .removed: datetime.date | None     # None ⇒ still active
        .price:   float

Why QA is asking for this seam rather than testing through
`shape_raw_pull` alone: F4-S2's AC is stated in **spell rows** ("a record
whose history holds two prior spells yields three spell rows"), and
`shape_raw_pull` does not expose spells — only post-stitch `StitchedComp`s.
WS-1's own version of this AC had to reach the count indirectly, by spacing
the spells more than F4-S3's 42-day threshold apart so that three spells
happened to produce three chains. That test passes for a reason F4-S2 does
not own (the stitcher) and would go quietly vacuous the moment F4-S3's
merge rules change. Architecture checkpoint 2 / ADR-002 note 6 already
recorded that `shape.py` has no Layer-1 seam and that this is where one is
owed; F4-S3's queue row asks for its own (`stitch`) separately.

Everything *below* this seam stays a [DEFAULT] — nothing here asserts how
spells are stored, ordered internally, or what other fields `Spell` carries.

REAL-DATA SHAPE THIS FILE RELIES ON (measured over the committed 539-record
pull, zero live calls — D17):
  * `history` is a dict keyed by event date; each event carries `price`,
    `listedDate`, `removedDate` (absent while active), `daysOnMarket`.
  * 495 of 495 records that have a `history` have a top-level
    (listedDate, removedDate) pair **identical to their newest history
    event** — the top level restates the current spell, it is never a spell
    the history does not already contain.
  * 44 records carry no `history` at all; all 539 carry `listedDate`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.keys import comp_key
from rentcomp.storage.config import Config

try:  # the seam this story is asked to add
    from rentcomp.pipeline.shape import Spell, extract_spells

    _SEAM_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - the red state before F4-S2 lands
    _SEAM_ERROR = exc

from rentcomp.pipeline.shape import shape_raw_pull  # already real (WS-1)

needs_seam = pytest.mark.skipif(
    _SEAM_ERROR is not None,
    reason=f"rentcomp.pipeline.shape.extract_spells/Spell not importable yet: {_SEAM_ERROR}",
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

FULL_YEAR = ("01-01", "12-31")
AS_OF = date(2026, 7, 27)


def _real_records() -> list[dict]:
    return json.loads(ACTIVE_FIXTURE.read_text()) + json.loads(INACTIVE_FIXTURE.read_text())


def _real(formatted_address: str) -> dict:
    """One record from the committed pull, by its `formattedAddress`."""
    matches = [r for r in _real_records() if r["formattedAddress"] == formatted_address]
    assert len(matches) == 1, (
        f"expected exactly one committed record at {formatted_address!r}, found {len(matches)} — "
        "this file's oracle has drifted from the fixtures"
    )
    return matches[0]


def _iso(value: str) -> date:
    return date.fromisoformat(value)


def _spell_rows(spells) -> set[tuple[date, date | None, float]]:
    return {(s.listed, s.removed, float(s.price)) for s in spells}


def _synthetic(
    *,
    id_: str,
    address: str,
    unit: str | None,
    listed: str,
    removed: str | None,
    price: float = 2000.0,
    history: dict | None = None,
) -> dict:
    """A raw record in RentCast's real wire shape (field set verified against
    both committed fixtures)."""
    return {
        "id": id_,
        "formattedAddress": f"{address}, {unit}, Chicago, IL 60609" if unit else f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": unit,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 1000,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": history if history is not None else {},
    }


def _event(listed: str, removed: str | None, price: float) -> dict:
    return {
        "event": "Rental Listing",
        "price": price,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "daysOnMarket": 30,
    }


def test_the_extract_spells_seam_exists() -> None:
    """The one loud, ungated failure that names what is missing — every other
    seam test below skips legibly behind it (AGENT_QA.md: a red state must be
    readable)."""
    assert _SEAM_ERROR is None, (
        "F4-S2 needs `rentcomp.pipeline.shape.extract_spells` + `Spell` exported so its AC "
        "('three spell rows') can be asserted directly instead of inferred from the "
        f"stitcher's output — see this module's docstring: {_SEAM_ERROR}"
    )


def test_the_committed_fixtures_are_the_gate_sample() -> None:
    assert len(json.loads(ACTIVE_FIXTURE.read_text())) == 39
    assert len(json.loads(INACTIVE_FIXTURE.read_text())) == 500


# ---------------------------------------------------------------------------
# AC1 — "a record whose history holds two prior spells yields three spell rows"
# ---------------------------------------------------------------------------


@needs_seam
def test_two_prior_history_spells_plus_the_current_one_yield_three_spell_rows() -> None:
    """F4-S2 AC1, verbatim and at its own layer — no stitch threshold, no
    cohort spacing, nothing borrowed from F4-S3."""
    record = _synthetic(
        id_="triple",
        address="200 W History Ave",
        unit="Unit 1",
        listed="2024-03-01",
        removed=None,
        price=2000.0,
        history={
            "2022-03-01": _event("2022-03-01", "2022-04-01", 1800),
            "2023-03-01": _event("2023-03-01", "2023-04-01", 1850),
            "2024-03-01": _event("2024-03-01", None, 2000),
        },
    )
    spells = extract_spells([record])
    assert len(spells) == 3, f"expected 3 spell rows (2 prior + 1 current), got {len(spells)}"
    assert _spell_rows(spells) == {
        (date(2022, 3, 1), date(2022, 4, 1), 1800.0),
        (date(2023, 3, 1), date(2023, 4, 1), 1850.0),
        (date(2024, 3, 1), None, 2000.0),
    }


@needs_seam
def test_the_top_level_spell_is_not_double_counted_when_history_restates_it() -> None:
    """The real pull's dominant shape: 495/495 records with a history have a
    top-level (listed, removed) identical to their newest history event.
    Counting both would inflate every re-list badge and fabricate a
    zero-length gap on every record in the pull."""
    record = _synthetic(
        id_="restated",
        address="201 W History Ave",
        unit="Unit 1",
        listed="2024-03-01",
        removed="2024-04-01",
        price=2000.0,
        history={"2024-03-01": _event("2024-03-01", "2024-04-01", 2000)},
    )
    spells = extract_spells([record])
    assert len(spells) == 1, (
        f"the top level restates the newest history event; expected 1 spell row, got {len(spells)}"
    )


@needs_seam
def test_a_record_with_no_history_still_yields_one_spell_from_its_top_level_fields() -> None:
    """F4-S2: "Extract spells from both top-level record fields and `history`
    events". 44 of the 539 real records carry no `history` object at all; all
    of them carry `listedDate` + `removedDate`. Without the top-level path
    those 44 comps vanish from the evidence base silently."""
    record = _real("1623 W 32nd St, Unit 2, Chicago, IL 60608")
    assert not record.get("history"), "oracle drift: this record is supposed to have no history"

    spells = extract_spells([record])
    assert _spell_rows(spells) == {(date(2024, 8, 29), date(2024, 9, 14), 1700.0)}


@needs_seam
@pytest.mark.parametrize("empty", [None, {}])
def test_a_missing_or_empty_history_key_is_not_an_error(empty) -> None:
    """`history` absent, null, or `{}` are all the same case — a single
    top-level spell. Wire truth is Optional (D5)."""
    record = _synthetic(
        id_="bare", address="202 W History Ave", unit=None, listed="2024-03-01", removed="2024-04-01"
    )
    record["history"] = empty
    assert len(extract_spells([record])) == 1


# ---------------------------------------------------------------------------
# AC1 on REAL records
# ---------------------------------------------------------------------------


@needs_seam
def test_a_real_re_listed_unit_yields_both_of_its_spells_with_their_own_prices() -> None:
    """`2652 W 23rd Pl, Unit 3` — the F4-S7 report's re-list example #1:
    listed 2025-03-17 at $1,790, removed 2025-05-21, re-listed 2026-03-25 at
    $2,400 (gap 308 days). If the prior spell's *price* is lost, F4-S5's
    premium is computed from the wrong initial ask for this comp."""
    spells = extract_spells([_real("2652 W 23rd Pl, Unit 3, Chicago, IL 60608")])
    assert _spell_rows(spells) == {
        (date(2025, 3, 17), date(2025, 5, 21), 1790.0),
        (date(2026, 3, 25), None, 2400.0),
    }


@needs_seam
def test_a_real_contiguous_price_change_chain_keeps_all_three_spells() -> None:
    """`2411 W 34th Pl, Unit 2` — the F4-S7 report's price-change chain:
    2026-05-29 $2,250 -> 2026-06-24 $2,650 -> 2026-07-09 $2,250, each
    segment's `removedDate` equal to the next segment's `listedDate`.

    F4-S2 must hand F4-S3 **all three** segments with their prices. Whether
    they then merge into one spell (they must — 23 real records are chains
    like this and treating them as re-lists would fabricate re-lists out of
    price cuts) is F4-S3's rule, deliberately not asserted here. Collapsing
    them at extraction time would destroy the $2,650 -> $2,250 cut that
    `cut_history` is built from."""
    spells = extract_spells([_real("2411 W 34th Pl, Unit 2, Chicago, IL 60608")])
    assert _spell_rows(spells) == {
        (date(2026, 5, 29), date(2026, 6, 24), 2250.0),
        (date(2026, 6, 24), date(2026, 7, 9), 2650.0),
        (date(2026, 7, 9), None, 2250.0),
    }


@needs_seam
def test_the_real_five_event_churn_record_yields_five_spells() -> None:
    """`2453 W 46th Pl, Unit 1` — the pull's churn extreme (5 history events
    between 2025-10-27 and 2026-04-10), flagged in the F4-S7 report as a
    stress case. Its top level restates the newest event, so 5 events => 5
    rows, not 6."""
    spells = extract_spells([_real("2453 W 46th Pl, Unit 1, Chicago, IL 60632")])
    assert len(spells) == 5, f"expected 5 spell rows for the 5-event churn record, got {len(spells)}"
    assert min(s.listed for s in spells) == date(2025, 10, 27)
    assert {float(s.price) for s in spells} == {1300.0, 1400.0}


@needs_seam
def test_every_dated_history_event_in_the_real_pull_becomes_a_spell_row() -> None:
    """The whole-pull version: extraction may collapse the top-level
    restatement, but it may never drop a *dated history event*. 580 events
    across 539 records; a record's spell count must be at least its distinct
    (listedDate, removedDate) event count."""
    dropped: list[tuple[str, int, int]] = []
    for record in _real_records():
        events = {
            (e["listedDate"][:10], (e.get("removedDate") or "")[:10])
            for e in (record.get("history") or {}).values()
            if e.get("listedDate")
        }
        got = len(extract_spells([record]))
        if got < max(len(events), 1):
            dropped.append((record["id"], max(len(events), 1), got))
    assert not dropped, f"spell extraction dropped events on {len(dropped)} real record(s): {dropped[:5]}"


# ---------------------------------------------------------------------------
# dedupe on listing id
# ---------------------------------------------------------------------------


def test_the_same_listing_id_in_both_pulls_is_one_comp() -> None:
    """F4-S2: "Dedupe on listing `id`". A status flip between the Active and
    Inactive calls puts one listing in both responses. Asserted through the
    composed seam, which already exists (WS-1) — no new contract needed."""
    record = _synthetic(
        id_="same-id", address="100 W Test St", unit="Unit 1", listed="2026-05-01", removed=None
    )
    comps = shape_raw_pull([record], [dict(record)], Config(), AS_OF, *FULL_YEAR)
    assert len(comps) == 1, f"one listing id present twice must dedupe to one comp, got {len(comps)}"


def test_a_listing_seen_active_and_then_removed_is_not_reported_as_still_active() -> None:
    """AMBIGUITY QA HAD TO RESOLVE — FLAGGED TO THE PM, not silently decided.

    The AC says "dedupe on listing `id`" but not *which* copy wins when the
    two copies disagree. The same id can appear in the Active response (no
    `removedDate`) and in the Inactive response (removed), because the two
    calls are made at different moments and pagination takes time.

    QA asserts the honest reading: a removal observed anywhere in the pull is
    an observation, and dropping it would report a leased unit as still on
    the market. NORTH_STAR calls censored-vs-leased "the single most
    important distinction in the whole system", and censoring a comp that
    actually leased biases every KM curve toward pessimism. Today's
    first-wins dedupe keeps whichever copy the caller passed first.

    Note the deliberately identical `listedDate`: this is one spell seen
    twice, NOT a re-list. If the Active copy's `listedDate` were later than
    the Inactive copy's `removedDate`, that would be a genuine re-list and
    both spells must survive — F4-S3's territory, not asserted here."""
    active_view = _synthetic(
        id_="flipped", address="101 W Test St", unit="Unit 1", listed="2026-05-01", removed=None
    )
    inactive_view = _synthetic(
        id_="flipped", address="101 W Test St", unit="Unit 1", listed="2026-05-01", removed="2026-06-10"
    )

    comps = shape_raw_pull([active_view], [inactive_view], Config(), AS_OF, *FULL_YEAR)
    assert len(comps) == 1, f"expected one comp for one listing id, got {len(comps)}"
    assert comps[0].censored is False, (
        "the pull observed this listing's removal (2026-06-10) in the Inactive response, but the "
        "comp is reported as still active — a leased unit counted as censored evidence"
    )


def test_dedupe_does_not_depend_on_which_response_a_copy_arrived_in() -> None:
    """The order-independence half of the same invariant, stated so it cannot
    be satisfied by luck: the shaped result must be identical whichever list
    the two copies of one id arrive in. Pagination order is not evidence."""
    active_view = _synthetic(
        id_="flipped", address="101 W Test St", unit="Unit 1", listed="2026-05-01", removed=None
    )
    inactive_view = _synthetic(
        id_="flipped", address="101 W Test St", unit="Unit 1", listed="2026-05-01", removed="2026-06-10"
    )

    forward = shape_raw_pull([active_view], [inactive_view], Config(), AS_OF, *FULL_YEAR)
    reversed_ = shape_raw_pull([inactive_view], [active_view], Config(), AS_OF, *FULL_YEAR)
    assert forward == reversed_, (
        "shaping depends on which response a duplicated listing id arrived in:\n"
        f"  active-first : {forward}\n"
        f"  inactive-first: {reversed_}"
    )


@needs_seam
@pytest.mark.xfail(
    strict=True,
    reason=(
        "KNOWN GAP, raised at QA verify and referred to the PM (F4-S2 review item M1). "
        "`_completeness` orders on (removed is not None, removed, price) only, so two "
        "observations of one spell that agree on removal and price but differ on any OTHER "
        "field are resolved first-seen-wins — i.e. by arrival order. Not an AC violation and "
        "unreachable on the committed pull, so it did not fail the story; pinned strict so it "
        "converts to a green test the moment it is fixed, instead of being forgotten."
    ),
)
def test_two_observations_of_one_spell_resolve_the_same_way_in_either_order() -> None:
    """The full form of the invariant `_dedupe_by_id`'s own docstring claims —
    "identical input shaped to [X] or [Y] purely by call order" was the defect
    it set out to remove — and that `_completeness` states outright: "Value-
    derived only — never a position in the input."

    That claim holds for the two fields that decide `censored` and
    `effective_dom`, which is the part NORTH_STAR actually protects. It does
    not hold for the ride-along fields, and `sqft` is the one that matters:
    it feeds F4-S5's `premium`, and a missing-sqft comp is *default-excluded*
    from every median. So the field most likely to differ between two reports
    of one unit is the one that decides whether the comp counts at all.

    Reachable precisely because of this story: before F4-S2 these two listing
    ids were separate comps and were never compared. Six real groups now take
    this path."""
    reported_without_sqft = _synthetic(
        id_="obs-a", address="300 W Order St", unit="Unit 1", listed="2026-05-01", removed="2026-06-10"
    )
    reported_with_sqft = _synthetic(
        id_="obs-b", address="300 W Order St", unit="Apt 1", listed="2026-05-01", removed="2026-06-10"
    )
    reported_without_sqft["squareFootage"] = None
    reported_with_sqft["squareFootage"] = 1000

    forward = extract_spells([reported_without_sqft, reported_with_sqft])
    reversed_ = extract_spells([reported_with_sqft, reported_without_sqft])
    assert forward == reversed_, (
        "the surviving spell's sqft depends on which response the copy arrived in:\n"
        f"  no-sqft first : {[s.sqft for s in forward]}\n"
        f"  with-sqft first: {[s.sqft for s in reversed_]}"
    )


def test_real_records_dedupe_to_the_pull_they_came_from() -> None:
    """Guard, not a fix: the real pull has 539 distinct listing ids, so
    dedupe-on-id is a no-op on it (F4-S7 §4). This pins that a future
    dedupe rule does not start *losing* records — the failure mode that
    would look like a smaller, cleaner pull."""
    records = _real_records()
    assert len({r["id"] for r in records}) == 539
    comps = shape_raw_pull(
        json.loads(ACTIVE_FIXTURE.read_text()),
        json.loads(INACTIVE_FIXTURE.read_text()),
        Config(),
        AS_OF,
        *FULL_YEAR,
    )
    assert len(comps) >= 500, (
        f"the 539-record real pull shaped into only {len(comps)} comps — dedupe or grouping is "
        "swallowing records"
    )


# ---------------------------------------------------------------------------
# the load-bearing preconditions of `listed`-only spell identity
#
# Added at QA VERIFY (not at plan time): the implementation identifies a spell
# by its `listed` date alone, and resolves two observations of one start by
# keeping the "more complete" one. That is sound ONLY while two *genuinely
# different* vacancy episodes can never share a start date inside one group —
# if they can, the loser is dropped with no exception, no log and no flag, and
# `effective_dom`/`censored` change with zero signal.
#
# Two separate conditions underwrite it. Neither was committed as a test, and
# they are not the same claim — the first is structural (cannot be violated by
# any RentCast response), the second is merely empirical (true of this pull,
# not guaranteed of the next). Pinning them here is what makes a future
# violation loud instead of silent.
# ---------------------------------------------------------------------------


def test_a_history_events_dict_key_is_its_own_listed_date() -> None:
    """PRECONDITION 1 — structural, and the reason "0 duplicate listed dates
    within a record" is not evidence of anything risky.

    RentCast keys the `history` object *by* the event's own `listedDate`
    (580/580 events across the committed pull). JSON object keys are unique,
    so two events inside one record's history **cannot** share a `listed` date
    — it is impossible by the wire format, not lucky.

    This test exists so that if RentCast ever re-keys `history` by something
    else (removal date, an event id, a sequence number), the `listed`-only
    identity rule stops being safe *here*, loudly, instead of silently
    swallowing a spell inside `extract_spells`."""
    mismatches = [
        (record["id"], key, event.get("listedDate"))
        for record in _real_records()
        for key, event in (record.get("history") or {}).items()
        if (event.get("listedDate") or "")[:10] != key
    ]
    assert not mismatches, (
        "a `history` event is no longer keyed by its own listedDate, so two events in one "
        "record can now collide on `listed` and `extract_spells` would silently drop one: "
        f"{mismatches[:5]}"
    )


@needs_seam
def test_no_two_listing_ids_in_one_group_report_the_same_spell_start() -> None:
    """PRECONDITION 2 — empirical, and the one that actually carries risk.

    Precondition 1 only covers events *within one record*. AC2 newly puts two
    distinct listing ids into one group (six real groups do this), and nothing
    structural stops two ids from reporting a spell with the same `listed`
    date. When that happens `extract_spells` keeps one and drops the other in
    silence.

    Today that is 0 across all six real multi-record groups, so the merge is
    lossless on this pull — which is exactly what makes it worth pinning: the
    day a pull violates this, the number that moves is `effective_dom`, and
    nothing else in the suite would notice."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in _real_records():
        address = record.get("addressLine1") or record.get("formattedAddress") or ""
        groups[comp_key(address, record.get("addressLine2"))].append(record)

    lossy: dict[str, list] = {}
    for key, records in groups.items():
        if len(records) < 2:
            continue
        per_id = {record["id"]: {s.listed for s in extract_spells([record])} for record in records}
        merged = len(extract_spells(records))
        separate = len(set().union(*per_id.values()))
        if merged != separate:
            lossy[key] = sorted(per_id)

    assert not lossy, (
        "two listing ids in one normalized group report a spell with the same start date; "
        "`extract_spells` dropped one of them with no exception, log or flag — if they were "
        f"distinct vacancy episodes rather than duplicate reports, that is lost evidence: {lossy}"
    )
