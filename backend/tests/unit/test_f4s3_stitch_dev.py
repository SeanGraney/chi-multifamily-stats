"""F4-S3 — developer-authored supplement to QA's
`test_f4s3_stitch_properties.py` / `test_f4s3_stitcher_golden.py` /
`test_f4s3_real_pull_chain_boundary.py` / `test_f4s3_shape_pickups.py`.

QA's plan pins every acceptance criterion. This file covers what satisfying
them changed but was nobody's AC (AGENT_DEVELOPER.md protocol item 4):

* **the withdrawal-suspect window** (PM ruling E4). `_withdrawal_suspect_flags`
  shared BUG-2's `chains[i][-1].removed` expression, so it measured the
  6-week-to-6-month re-list window from a *truncated* chain end. It is
  unreachable on the committed pull — QA's files correctly assert nothing
  about it — but F4-S8 is the next story to lean on it, and the fix rode in
  on this pass. Nothing else in the suite would notice if it regressed.
* **`gap_days` is summed with the same rule `stitch` merged on.** QA's
  property test pins `gap_days == sum(gaps)` only over *non-negative* gaps,
  which is the case where the old per-pair rule and the running-latest-
  removal rule agree. The case that distinguishes them — a chain whose
  overlap makes an earlier spell the last to end — is asserted here.
* **`_completeness`'s new component sorts above `price`.** QA's guard
  (`test_a_completeness_tie_break_does_not_change_which_removal_wins`) pins
  it *below* both removal components, which is the dangerous direction. The
  other edge matters too: `price` is a tie-break with no semantic
  justification that nonetheless biases `initial_ask` upward, so it must not
  outrank a component that has a reason.

Zero live API calls (D17) — every input here is a literal.
"""

from __future__ import annotations

from datetime import date, timedelta

from rentcomp.models.domain import Spell
from rentcomp.pipeline.shape import extract_spells, shape_raw_pull, stitch
from rentcomp.storage.config import Config

AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")
DEFAULT_THRESHOLD = 42


def _record(id_: str, listed: date, removed: date | None, price: float = 2000.0, **over) -> dict:
    record = {
        "id": id_,
        "formattedAddress": "500 W Dev St, Unit 1, Chicago, IL 60609",
        "addressLine1": "500 W Dev St",
        "addressLine2": "Unit 1",
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
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "removedDate": f"{removed.isoformat()}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed.isoformat()}T00:00:00.000Z",
        "lastSeenDate": f"{(removed or listed).isoformat()}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }
    record.update(over)
    return record


def _spells(segments):
    records = [_record(f"d{i}", *seg) for i, seg in enumerate(segments)]
    spells = extract_spells(records)
    assert len(spells) == len(segments), "test-input defect: two segments share a listedDate"
    return spells


def _comps(segments, as_of: date = AS_OF):
    records = [_record(f"d{i}", *seg) for i, seg in enumerate(segments)]
    active = [r for r in records if r["removedDate"] is None]
    inactive = [r for r in records if r["removedDate"] is not None]
    return shape_raw_pull(active, inactive, Config(), as_of, *FULL_YEAR)


# ---------------------------------------------------------------------------
# E4 — the withdrawal-suspect window measures from the chain's LATEST removal
# ---------------------------------------------------------------------------


def test_the_withdrawal_suspect_window_is_measured_from_the_chains_latest_removal() -> None:
    """PM ruling E4: `_withdrawal_suspect_flags` shared BUG-2's expression.

    The chain here overlaps, so its last spell *by listed date* ends well
    before the chain actually did:

        A  2025-01-01 -> 2025-06-01   (the latest removal the chain observed)
        B  2025-02-01 -> 2025-03-01   (last by listed date, first to end)
        ---- chain ends 2025-06-01 ----
        C  2025-07-20                 (49 days after the real end)

    49 days is inside the 42d-to-6mo suspicion window, so C's re-list casts
    doubt on whether A/B's "lease" happened. Measured from B's 2025-03-01
    instead, the gap reads 141 days — still inside the window here, so the
    flag alone cannot distinguish the two rules; what distinguishes them is
    that the *chain boundary* moves with it. The assertion that bites is the
    lower one: from the truncated end, C would have merged rather than being
    a separate chain at all.
    """
    segments = [
        (date(2025, 1, 1), date(2025, 6, 1)),
        (date(2025, 2, 1), date(2025, 3, 1)),
        (date(2025, 7, 20), date(2025, 8, 20)),
    ]
    chains = stitch(_spells(segments), DEFAULT_THRESHOLD)
    assert [len(c) for c in chains] == [2, 1], (
        "the 2025-07-20 re-list is 49 days after the chain's latest observed removal "
        "(2025-06-01), which is >= the 42d threshold, so it must start a new chain. "
        f"Measured from the last spell by listed date it is 141 days. Got {[len(c) for c in chains]}"
    )

    comps = _comps(segments)
    assert len(comps) == 2
    first = min(comps, key=lambda c: c.first_listed)
    assert first.withdrawal_suspect is True, (
        "the first chain ended 2025-06-01 and the unit re-listed 49 days later — inside the "
        "6-week-to-6-month withdrawal-suspect window"
    )


def test_a_relist_beyond_six_months_of_the_chains_real_end_is_not_suspect() -> None:
    """The complement, and the direction the old expression got wrong.

    Same overlapping chain ending 2025-06-01, re-listed 2026-01-15 — 228
    days later, past the 6-month ceiling, so not suspicious. From the
    truncated end (2025-03-01) that gap reads 320 days; both are outside the
    window, but only one of them is the number the flag claims to be about.
    """
    comps = _comps(
        [
            (date(2025, 1, 1), date(2025, 6, 1)),
            (date(2025, 2, 1), date(2025, 3, 1)),
            (date(2026, 1, 15), date(2026, 2, 15)),
        ]
    )
    assert len(comps) == 2
    first = min(comps, key=lambda c: c.first_listed)
    assert first.withdrawal_suspect is False


def test_a_censored_chain_is_never_withdrawal_suspect() -> None:
    """A chain that never came off market has no "lease" to doubt. Pinned
    because `_chain_end` returning `None` is what encodes this now, where it
    used to be a `chains[i][-1].removed is None` check."""
    comps = _comps(
        [
            (date(2025, 1, 1), None),
            (date(2025, 4, 1), date(2025, 5, 1)),
        ]
    )
    assert len(comps) == 2
    first = min(comps, key=lambda c: c.first_listed)
    assert first.censored is True
    assert first.withdrawal_suspect is False


# ---------------------------------------------------------------------------
# `gap_days` uses the same days-off-market rule the merge did
# ---------------------------------------------------------------------------


def test_gap_days_counts_off_market_time_from_the_latest_removal_not_the_last_spell() -> None:
    """`gap_days` is what the row badge shows as time off market, so it must
    be the quantity `stitch` merged on.

        A  2025-01-01 -> 2025-06-01
        B  2025-02-01 -> 2025-03-01   (overlaps A entirely)
        C  2025-06-21 -> 2025-07-01   (20 days after the chain's real end)

    The unit was off market for 20 days, once. Summing per adjacent pair from
    the *previous* spell's removal instead counts 0 (A->B, an overlap) plus
    112 (B's 2025-03-01 -> C), reporting 112 days off market for a unit that
    was listed for 109 of them.
    """
    comps = _comps(
        [
            (date(2025, 1, 1), date(2025, 6, 1)),
            (date(2025, 2, 1), date(2025, 3, 1)),
            (date(2025, 6, 21), date(2025, 7, 1)),
        ]
    )
    assert len(comps) == 1, "all three spells are within 42 days off market of each other"
    comp = comps[0]
    assert comp.gap_days == 20, (
        f"gap_days={comp.gap_days}; the unit was off market from 2025-06-01 (the chain's latest "
        "observed removal) to 2025-06-21, which is 20 days"
    )
    assert comp.effective_dom == (date(2025, 7, 1) - date(2025, 1, 1)).days
    assert comp.relist_count == 2


def test_an_overlap_contributes_no_off_market_days() -> None:
    """A unit whose next listing began before the previous one ended was
    never off market — the reading of the merge rule this story turns on."""
    comps = _comps(
        [
            (date(2025, 1, 1), date(2025, 3, 1)),
            (date(2025, 2, 1), date(2025, 4, 1)),
        ]
    )
    assert len(comps) == 1
    assert comps[0].gap_days == 0
    assert comps[0].effective_dom == (date(2025, 4, 1) - date(2025, 1, 1)).days


# ---------------------------------------------------------------------------
# the seam's own shape
# ---------------------------------------------------------------------------


def test_stitch_takes_a_plain_int_so_the_config_bounds_cannot_constrain_it() -> None:
    """`Config.stitch_gap_days` is `ge=7, le=60`. The seam must accept
    thresholds outside that range or AC1 is unassertable — this is the whole
    reason the signature is not `(spells, config)`."""
    spells = _spells([(date(2025, 1, 1), date(2025, 2, 1)), (date(2025, 2, 2), date(2025, 3, 1))])
    assert len(stitch(spells, 0)) == 2, "threshold 0 must merge nothing"
    assert len(stitch(spells, 500)) == 1, "a threshold far above Config's ceiling must still merge"


def test_stitch_is_the_path_shape_raw_pull_actually_takes() -> None:
    """The seam is only worth having if it is the real code path and not a
    second implementation that can drift from it (QA's requirement 3).

    Asserted behaviourally rather than by patching: a threshold the `Config`
    would reject cannot be pushed through `shape_raw_pull`, but the two must
    agree at every threshold the `Config` allows.
    """
    segments = [
        (date(2025, 1, 1), date(2025, 2, 1)),
        (date(2025, 3, 1), date(2025, 4, 1)),
        (date(2025, 4, 10), date(2025, 5, 1)),
    ]
    spells = _spells(segments)
    for threshold in (7, 28, 42, 60):
        config = Config(stitch_gap_days=threshold)
        records = [_record(f"d{i}", *seg) for i, seg in enumerate(segments)]
        comps = shape_raw_pull([], records, config, AS_OF, *FULL_YEAR)
        assert len(comps) == len(stitch(spells, threshold)), (
            f"at threshold {threshold} `shape_raw_pull` produced {len(comps)} comps but `stitch` "
            f"produced {len(stitch(spells, threshold))} chains — the seam is not the real path"
        )


def test_stitch_returns_chains_of_the_spells_it_was_given() -> None:
    """Identity, not copies: the chains carry the same `Spell` objects, so a
    caller can fold them without re-deriving anything."""
    spells = _spells([(date(2025, 1, 1), date(2025, 2, 1)), (date(2025, 6, 1), date(2025, 7, 1))])
    chains = stitch(spells, DEFAULT_THRESHOLD)
    assert [list(chain) for chain in chains] == [[spells[0]], [spells[1]]]
    assert all(isinstance(spell, Spell) for chain in chains for spell in chain)


# ---------------------------------------------------------------------------
# `_completeness`'s ordering — the edge QA's guard does not cover
# ---------------------------------------------------------------------------


def test_field_completeness_outranks_the_price_tie_break() -> None:
    """PM ruling P1a/P1b: `price` is a deterministic tie-break with no
    semantic justification, but a real semantic consequence — it prefers the
    higher price and so biases `initial_ask`, the premium numerator, upward.
    A component that has a reason must outrank one that does not.

    Both observations here report the same listing start and the same
    removal. One reports `squareFootage` at a lower price; the other reports
    no size at a higher one. Preferring the higher price would take a comp
    that the pull sized and make it unsizeable — default-excluded from every
    cohort median — in exchange for a tie-break with no meaning behind it.
    """
    sized = _record("sized", date(2026, 5, 1), date(2026, 6, 10), price=1800.0)
    unsized = _record("unsized", date(2026, 5, 1), date(2026, 6, 10), price=2400.0)
    unsized["squareFootage"] = None
    unsized["addressLine2"] = "Apt 1"

    for order in ([sized, unsized], [unsized, sized]):
        spells = extract_spells(order)
        assert len(spells) == 1, "these are two observations of one spell"
        assert spells[0].sqft == 1000.0, (
            "the observation reporting a squareFootage lost to a higher-priced one that did "
            f"not (order: {[r['id'] for r in order]}); price outranked field completeness"
        )
        assert spells[0].price == 1800.0


def test_the_price_tie_break_still_decides_when_completeness_is_equal() -> None:
    """The complement: `price` is still the last resort, so two equally
    complete observations remain resolved deterministically rather than by
    arrival order."""
    cheap = _record("cheap", date(2026, 5, 1), date(2026, 6, 10), price=1800.0)
    dear = _record("dear", date(2026, 5, 1), date(2026, 6, 10), price=2400.0)
    dear["addressLine2"] = "Apt 1"

    for order in ([cheap, dear], [dear, cheap]):
        spells = extract_spells(order)
        assert len(spells) == 1
        assert spells[0].price == 2400.0


def test_completeness_never_invents_a_value() -> None:
    """The property that makes the tie-break safe: it can only ever prefer a
    *reported* field over an absent one, so the surviving spell's fields are
    always some single observation's fields — never a merge of two."""
    listed, removed = date(2026, 5, 1), date(2026, 6, 10)
    baths_only = _record("baths", listed, removed, price=2000.0, squareFootage=None, bathrooms=2)
    sqft_only = _record("sqft", listed, removed, price=2000.0, squareFootage=900, bathrooms=None)

    for order in ([baths_only, sqft_only], [sqft_only, baths_only]):
        spells = extract_spells(order)
        assert len(spells) == 1
        spell = spells[0]
        assert (spell.sqft, spell.baths) in {(None, 2.0), (900.0, None)}, (
            f"the surviving spell reports sqft={spell.sqft!r} baths={spell.baths!r}, which is "
            "neither observation — completeness fabricated a record by combining them"
        )


# ---------------------------------------------------------------------------
# censoring, stated at the seam rather than through a generated chain
# ---------------------------------------------------------------------------


def test_an_open_spell_ends_its_chain_so_censored_is_unambiguous() -> None:
    """PM ruling E6 left "an open spell not last by listed date" undefined —
    it occurs zero times in the pull. What makes that safe to leave undefined
    is this: an open spell always terminates its own chain, so "the chain
    ends open" and "the chain contains an open spell" can never disagree, and
    `censored` means the same thing under either reading.
    """
    spells = _spells(
        [
            (date(2025, 1, 1), date(2025, 2, 1)),
            (date(2025, 2, 2), None),
            (date(2025, 2, 20), date(2025, 3, 20)),
        ]
    )
    chains = stitch(spells, DEFAULT_THRESHOLD)
    for chain in chains:
        opens = [spell for spell in chain if spell.removed is None]
        assert not opens or chain[-1].removed is None, (
            "a chain contains an open spell that is not its last — `censored` is now ambiguous"
        )


def test_a_censored_chains_dom_runs_to_the_pull_date_not_the_wall_clock() -> None:
    """Owner ruling 1 (`models/domain.py`): "today" is the pull's `as_of`.
    Asserted with an `as_of` far in the past so a wall-clock implementation
    cannot coincidentally agree."""
    as_of = date(2025, 3, 1)
    comps = _comps([(date(2025, 1, 1), date(2025, 1, 20)), (date(2025, 2, 1), None)], as_of=as_of)
    assert len(comps) == 1
    assert comps[0].censored is True
    assert comps[0].removal_class is None
    assert comps[0].effective_dom == (as_of - date(2025, 1, 1)).days == 59


def test_stitching_a_single_spell_is_one_chain_at_every_threshold() -> None:
    spells = _spells([(date(2025, 1, 1), date(2025, 2, 1))])
    for threshold in (0, 1, 42, 1000):
        assert [list(c) for c in stitch(spells, threshold)] == [[spells[0]]]


def test_a_chain_ending_in_a_removal_beyond_the_pull_date_is_not_censored() -> None:
    """A removal the pull observed is an observed outcome even when the merge
    arithmetic puts the chain's end close to `as_of` — the `removal_class`
    ladder handles recency, `censored` does not."""
    comps = _comps([(date(2026, 6, 1), date(2026, 7, 25))], as_of=AS_OF)
    assert len(comps) == 1
    assert comps[0].censored is False
    assert comps[0].removal_class == "pending", "removed 2 days before the pull"


def test_relist_count_and_gap_days_agree_with_the_chain_the_seam_produced() -> None:
    """The two badge fields are read off the same chain the merge rule built,
    so a comp can never report more re-lists than the chain has spells."""
    segments = [
        (date(2025, 1, 5), date(2025, 2, 5)),
        (date(2025, 3, 1), date(2025, 3, 20)),
        (date(2025, 4, 1), date(2025, 5, 1)),
    ]
    spells = _spells(segments)
    chains = stitch(spells, DEFAULT_THRESHOLD)
    assert len(chains) == 1
    comp = _comps(segments)[0]
    assert comp.relist_count == len(chains[0]) - 1 == 2
    assert comp.gap_days == 24 + 12, "2025-02-05->03-01 is 24 days, 2025-03-20->04-01 is 12"


def test_gap_days_never_exceeds_the_chains_span() -> None:
    """A structural sanity check on the pair above: the time a unit spent off
    market cannot exceed the time between its first listing and its last
    removal."""
    for segments in (
        [(date(2025, 1, 1), date(2025, 2, 1)), (date(2025, 2, 20), date(2025, 3, 1))],
        [(date(2025, 1, 1), date(2025, 6, 1)), (date(2025, 2, 1), date(2025, 3, 1))],
        [(date(2025, 1, 1), date(2025, 1, 2)), (date(2025, 1, 2), date(2025, 1, 3))],
    ):
        comp = _comps(segments)[0]
        assert 0 <= comp.gap_days <= comp.effective_dom, (
            f"gap_days={comp.gap_days} effective_dom={comp.effective_dom} for {segments}"
        )


def test_spells_carry_a_timedelta_free_date_boundary() -> None:
    """CLAUDE.md: dates parse to `datetime.date` at the DTO boundary, no
    timezones internally. The raw records carry `...T00:00:00.000Z`."""
    spell = _spells([(date(2025, 1, 1), date(2025, 2, 1))])[0]
    assert isinstance(spell.listed, date) and not hasattr(spell.listed, "tzinfo")
    assert spell.removed == date(2025, 2, 1)
    assert (spell.removed - spell.listed) == timedelta(days=31)
