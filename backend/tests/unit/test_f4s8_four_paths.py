"""F4-S8 [INVARIANT] — the AC's four named paths, one test each.

QA-authored, written test-first (AGENT_QA.md protocol). Layer 1: the whole
story is a pure function of (spells, today, config), so every claim here is a
direct call with plain values — no HTTP, no browser, no clock.

The acceptance criterion names four paths and this file refuses to blur them
into one loosely-asserted fixture::

    1. pending -> provisional -> confirmed
    2. provisional -> re-list -> stitched
    3. confirmed -> suspect
    4. clean confirmed

Paths 1-3 are *transitions*, and a transition needs two moments. The story
supplies them: "**Refresh re-classifies**" — a refresh is a later pull, i.e.
the same unit shaped again with a later ``as_of`` and (for paths 2 and 3) the
extra records the later pull observed. So each path below is written as a
sequence of ``shape_raw_pull`` calls over a growing record set, which is what
a refresh literally is, rather than as one snapshot asserted three ways.

``as_of`` is the pull's own fetch date, never the wall clock (owner ruling 1,
`storage/pulls.py`) — so a refresh moving the classification forward is the
pull date moving, and these tests are stable forever.
"""

from __future__ import annotations

from datetime import date, timedelta

from f4s8_records import FULL_YEAR, closed_record, open_record
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config


def _shape(records: list[dict], as_of: date, config: Config | None = None):
    return shape_raw_pull(records, [], config or Config(), as_of, *FULL_YEAR)


def _only(comps):
    assert len(comps) == 1, f"expected exactly one comp, got {len(comps)}: {[c.address for c in comps]}"
    return comps[0]


# ---------------------------------------------------------------------------
# PATH 1 — pending -> provisional -> confirmed
# ---------------------------------------------------------------------------


def test_path_1_pending_then_provisional_then_confirmed_across_three_refreshes() -> None:
    """One unchanged removal, re-classified by three successive pulls.

    Nothing about the record changes between the three calls — only the pull
    date. That is the whole ladder: it is a statement about how much time has
    passed since the removal was observed, and about nothing else. The three
    calls also pin that a refresh *re-classifies* rather than freezing whatever
    class the first pull assigned."""
    listed = date(2026, 1, 5)
    removed = date(2026, 2, 10)
    rec = closed_record(id_="ladder", address="400 W Ladder St", listed=listed, removed=removed)

    day3 = _only(_shape([rec], removed + timedelta(days=3)))
    assert day3.removal_class == "pending", "3 days after removal: too recent to trust"
    assert day3.censored is False, "an observed removal is not censoring"

    day20 = _only(_shape([rec], removed + timedelta(days=20)))
    assert day20.removal_class == "provisional", "20 days after removal: counted, but marked"

    day60 = _only(_shape([rec], removed + timedelta(days=60)))
    assert day60.removal_class == "confirmed", "60 days with no re-list: the marker drops"

    # The evidence itself never moved, so nothing derived from the evidence may.
    assert day3.effective_dom == day20.effective_dom == day60.effective_dom == 36, (
        "the ladder must move without touching effective_dom — the unit's time on market is "
        f"a fact of the record, not of when it was looked at: got {day3.effective_dom}, "
        f"{day20.effective_dom}, {day60.effective_dom}"
    )
    assert day3.initial_ask == day60.initial_ask
    assert (day3.withdrawal_suspect, day60.withdrawal_suspect) == (False, False)


# ---------------------------------------------------------------------------
# PATH 2 — provisional -> re-list -> stitched
# ---------------------------------------------------------------------------


def test_path_2_a_provisional_that_relists_is_stitched_back_into_its_spell() -> None:
    """The story's own sentence: "a provisional that re-lists is stitched back
    into its spell."

    Pull 1 sees a unit removed 9 days ago and calls it a provisional lease.
    Pull 2, six weeks later, sees it back on the market after 28 days off — so
    the "lease" never happened, the two spells are one continuous vacancy, and
    the provisional must vanish as a separate leased outcome rather than linger
    beside the re-list.

    This is the path the WS-1 slice explicitly deferred ("not expressible
    through one ``shape_raw_pull`` call") — it is expressible, as two calls."""
    listed = date(2026, 1, 5)
    removed = date(2026, 2, 10)
    relisted = removed + timedelta(days=28)  # under the 42-day stitch threshold
    first = closed_record(id_="p2-a", address="500 W Relist St", listed=listed, removed=removed)

    before = _only(_shape([first], removed + timedelta(days=9)))
    assert before.removal_class == "provisional", "pull 1 sees a 9-day-old removal"
    assert before.relist_count == 0
    assert before.effective_dom == 36

    second = open_record(id_="p2-b", address="500 W Relist St", listed=relisted)
    after = _only(_shape([first, second], relisted + timedelta(days=14)))

    assert after.relist_count == 1, (
        "the re-list must be stitched into the same chain, not left as a second comp"
    )
    assert after.first_listed == listed, "the stitched chain still starts at the original listing"
    assert after.gap_days == 28, f"the 28 days off market are retained, got {after.gap_days}"
    assert after.censored is True, (
        "the unit is on the market again as of pull 2, so the chain is censored — its DOM is a "
        "floor, not an outcome"
    )
    assert after.removal_class is None, (
        "a re-listed unit has no removal to classify; leaving 'provisional' standing here is "
        "the removal-approximately-leased blind spot this story exists to close"
    )
    assert after.effective_dom == 78, (
        "effective DOM runs from the original listing to the pull date and the gap days count "
        f"(F4-S3), got {after.effective_dom}"
    )


def test_path_2b_a_provisional_that_relists_and_then_leaves_again_is_one_chain() -> None:
    """The same path where the re-list has itself ended by pull 2.

    The chain is closed again, so it re-enters the ladder — from the *new*
    removal date, not the old one. A classifier that kept measuring from the
    first removal would call this confirmed on the day it re-listed."""
    listed = date(2026, 1, 5)
    removed = date(2026, 2, 10)
    relisted = removed + timedelta(days=28)
    relist_removed = relisted + timedelta(days=10)
    records = [
        closed_record(id_="p2b-a", address="510 W Relist St", listed=listed, removed=removed),
        closed_record(
            id_="p2b-b", address="510 W Relist St", listed=relisted, removed=relist_removed
        ),
    ]

    comp = _only(_shape(records, relist_removed + timedelta(days=3)))
    assert comp.relist_count == 1
    assert comp.removal_class == "pending", (
        "the chain's own removal is 3 days old, so the chain is pending — the ladder measures "
        "from the chain's latest removal, never from the removal it stitched over"
    )

    later = _only(_shape(records, relist_removed + timedelta(days=50)))
    assert later.removal_class == "confirmed"
    assert later.withdrawal_suspect is False, (
        "a re-list the stitcher absorbed is not a withdrawal — it is one vacancy"
    )


# ---------------------------------------------------------------------------
# PATH 3 — confirmed -> suspect
# ---------------------------------------------------------------------------


def test_path_3_a_confirmed_lease_becomes_a_withdrawal_suspect_when_the_unit_reappears() -> None:
    """A spell that confirmed cleanly, then re-appeared 90 days later.

    Pull 1 has every reason to believe the unit leased: removed 47 days, no
    re-list. Pull 2 sees the unit back on the market inside the six-month
    window, which is not proof the lease was fake — hence *suspect*, a flag, on
    a comp that is still confirmed and still counted."""
    listed = date(2026, 1, 5)
    removed = date(2026, 2, 10)
    first = closed_record(id_="p3-a", address="600 W Doubt St", listed=listed, removed=removed)

    before = _only(_shape([first], removed + timedelta(days=47)))
    assert before.removal_class == "confirmed"
    assert before.withdrawal_suspect is False, "pull 1 has no re-list to be suspicious of"

    relisted = removed + timedelta(days=90)
    second = closed_record(
        id_="p3-b",
        address="600 W Doubt St",
        listed=relisted,
        removed=relisted + timedelta(days=25),
    )
    comps = sorted(_shape([first, second], relisted + timedelta(days=60)), key=lambda c: c.first_listed)

    assert len(comps) == 2, (
        f"a 90-day gap is past the stitch threshold and must stay two comps, got {len(comps)}"
    )
    after = comps[0]
    assert after.first_listed == listed
    assert after.removal_class == "confirmed", (
        "the flag is additional doubt, not a demotion — the removal is still >= 42 days old "
        "and still confirmed"
    )
    assert after.withdrawal_suspect is True, "the re-list inside six months raises the flag"


def test_path_3b_the_suspect_flag_never_demotes_the_ladder_or_removes_the_comp() -> None:
    """NORTH_STAR: withdrawal-suspect is "**not** grounds for automatic
    exclusion. Display-only flag; the human makes the call."

    The negative half of the invariant, pinned at Layer 1 so a future refactor
    cannot quietly turn a badge into a filter. The suspect comp must be
    indistinguishable from the clean one in every field except the flag."""
    listed = date(2026, 1, 5)
    removed = date(2026, 2, 10)
    relisted = removed + timedelta(days=90)
    suspect_records = [
        closed_record(id_="p3b-a", address="610 W Doubt St", listed=listed, removed=removed),
        closed_record(
            id_="p3b-b",
            address="610 W Doubt St",
            listed=relisted,
            removed=relisted + timedelta(days=25),
        ),
    ]
    clean_record = closed_record(
        id_="p3b-c", address="611 W Clean St", listed=listed, removed=removed
    )
    as_of = relisted + timedelta(days=60)

    comps = _shape([*suspect_records, clean_record], as_of)
    suspect = next(c for c in comps if c.address == "610 W Doubt St" and c.first_listed == listed)
    clean = next(c for c in comps if c.address == "611 W Clean St")

    assert suspect.withdrawal_suspect is True and clean.withdrawal_suspect is False
    assert (suspect.removal_class, suspect.censored, suspect.effective_dom) == (
        clean.removal_class,
        clean.censored,
        clean.effective_dom,
    ), (
        "the suspect and the clean comp differ ONLY in the flag: same rung, same censoring, "
        "same DOM. Anything else means the flag has started acting like a filter"
    )
    assert suspect.initial_ask == clean.initial_ask


# ---------------------------------------------------------------------------
# PATH 4 — clean confirmed (the control)
# ---------------------------------------------------------------------------


def test_path_4_a_clean_confirmed_removal_carries_no_marker_at_all() -> None:
    """The control case, and the one that makes the other three mean something:
    a single spell, long removed, never re-listed. Confirmed, not suspect, not
    censored, no re-lists, no gap days.

    Without this, an implementation that flagged everything would pass paths
    1-3."""
    listed = date(2025, 3, 1)
    removed = date(2025, 4, 20)
    rec = closed_record(id_="p4", address="700 W Clean St", listed=listed, removed=removed)

    comp = _only(_shape([rec], date(2026, 6, 1)))
    assert comp.removal_class == "confirmed"
    assert comp.withdrawal_suspect is False
    assert comp.censored is False
    assert comp.relist_count == 0
    assert comp.gap_days == 0
    assert comp.effective_dom == 50
    assert comp.cut_history == ()


def test_path_4b_a_still_active_listing_is_censored_and_has_no_rung_at_all() -> None:
    """The other control: `removal_class is None` means *still active*, which is
    not a rung of the ladder and never becomes one by waiting.

    `None` is not a fourth state on the ladder — NORTH_STAR's most important
    distinction is that a censored comp has not been removed, let alone leased.
    Two pull dates, 200 days apart, both `None`."""
    listed = date(2025, 3, 1)
    rec = open_record(id_="p4b", address="710 W Active St", listed=listed)

    early = _only(_shape([rec], date(2025, 5, 1)))
    late = _only(_shape([rec], date(2025, 11, 17)))

    assert early.removal_class is None and late.removal_class is None, (
        "a listing that never left the market has no removal to classify, however long the "
        "pull waits"
    )
    assert early.censored is True and late.censored is True
    assert late.effective_dom > early.effective_dom, (
        "a censored comp's DOM floor grows with the pull date — that is the floor moving, not "
        "a classification"
    )
