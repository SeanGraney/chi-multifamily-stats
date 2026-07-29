"""F4-S8 [INVARIANT] — the four boundaries, each with its own deliberate test.

QA-authored, written RED before the developer starts (AGENT_QA.md protocol).

Layer 1 (decision procedure Q1): ``shape_raw_pull`` is importable and callable
with plain dicts and plain dates. No HTTP, no browser, no clock.

WHY THIS FILE EXISTS AT ALL
---------------------------
F4-S3's QA verify found that mutant **M8 — the stitch floor at exactly 42
days, ``<=`` vs ``<`` — died only *incidentally*, to golden-file group S05**.
A boundary that survives because some unrelated fixture happens to straddle it
is not covered; it is lucky. This story leans on FOUR boundaries at once (7d,
42d, 6 weeks, 6 months), so each one gets its own test here, asserted **below
it, exactly on it, and above it**, with the values written as literals rather
than derived from a fixture that could drift underneath them.

The real pull confirms the concern rather than dissolving it. Its inter-chain
gaps are::

    44, 59, 61, 63, 80, 120, 122, 123, 135, 143, 145, 148, 189, 193, 229, ...

so the nearest real evidence to the 42-day suspect floor is **44** (two days
clear) and the ceiling is bracketed only by **148 and 189** — any ceiling in
(148, 189] produces the identical 12 suspects. The committed pull therefore
cannot distinguish a correct implementation from one whose ceiling is off by a
month, and cannot see an off-by-one at the floor at all. These tests can.

WHAT IS DELIBERATELY *NOT* PINNED HERE
--------------------------------------
The exact days-per-month arithmetic behind "6 months"
(``withdrawal_suspect_months`` is a knob; converting it to days is nowhere
specified in the story). ``test_the_six_month_ceiling_*`` therefore asserts
that a single sharp crossing exists, that it lands where six months lands, and
that it *moves with the knob* — not that it equals 180. A test that pinned 180
would break on a legitimate switch to calendar months for no real reason
(AGENT_QA.md: never pin a `[DEFAULT]`'s technique in place of its
`[INVARIANT]`'s outcome).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from f4s8_records import FULL_YEAR, closed_record, open_record, spell_pair_with_gap
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config


def _classify_after(days_since_removal: int, *, dom: int = 20) -> str | None:
    """The removal class of one closed listing seen ``days_since_removal``
    days after it left the market. The only thing that varies is ``as_of``."""
    as_of = date(2026, 6, 1)
    removed = as_of - timedelta(days=days_since_removal)
    listed = removed - timedelta(days=dom)
    rec = closed_record(id_="b1", address="100 W Boundary St", listed=listed, removed=removed)
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1, f"one record must shape to one comp, got {len(comps)}"
    assert comps[0].censored is False, "an observed removal is never censored"
    return comps[0].removal_class


# ---------------------------------------------------------------------------
# BOUNDARY 1 — 7 days: "off market < 7d => pending; >= 7d => provisional"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("days", [0, 1, 3, 5, 6])
def test_boundary_7d_below_every_removal_under_seven_days_is_pending(days: int) -> None:
    """Strictly below the floor, on every day of it — including day 0.

    Day 0 (removed on the pull date itself) is in the sweep deliberately: it is
    the most-recent removal representable, it is the one an ``as_of``-vs-wall-
    clock confusion produces, and a `>` -for- `>=` slip at the bottom of the
    ladder would classify it as a lease on the day it was removed."""
    assert _classify_after(days) == "pending", (
        f"a removal {days}d before the pull date is under the 7-day floor and must be "
        f"'pending' (excluded from every leased statistic), got {_classify_after(days)!r}"
    )


def test_boundary_7d_exactly_seven_days_is_provisional_not_pending() -> None:
    """EXACTLY ON the boundary — the story's ">= 7d" is inclusive.

    This is the single assertion that separates ``< 7`` from ``<= 7``. Getting
    it wrong is invisible in aggregate: one comp moves between "excluded from
    leased stats" and "counted as a lease", which is precisely the direction
    NORTH_STAR says silently inflates apparent lease velocity."""
    assert _classify_after(7) == "provisional", (
        "a removal exactly 7 days before the pull date is AT the boundary, and the story's "
        "'>= 7d => provisional' makes it provisional, not pending"
    )


@pytest.mark.parametrize("days", [8, 12, 41])
def test_boundary_7d_above_stays_provisional_until_the_next_rung(days: int) -> None:
    assert _classify_after(days) == "provisional"


# ---------------------------------------------------------------------------
# BOUNDARY 2 — 42 days: ">= 42d with no re-list => confirmed"
# ---------------------------------------------------------------------------


def test_boundary_42d_below_forty_one_days_is_still_provisional() -> None:
    """One day below the confirmed rung. Paired with the test below, this is
    what makes the 42 a real boundary rather than a number in a docstring."""
    assert _classify_after(41) == "provisional", (
        "41 days is one day short of the confirmed rung and must still carry the "
        "provisional marker"
    )


def test_boundary_42d_exactly_forty_two_days_is_confirmed() -> None:
    """EXACTLY ON the boundary — ">= 42d" is inclusive, so 42 confirms."""
    assert _classify_after(42) == "confirmed", (
        "a removal exactly 42 days before the pull date has reached the confirmed rung "
        "('>= 42d'); the provisional marker must drop here, not at 43"
    )


@pytest.mark.parametrize("days", [43, 60, 400])
def test_boundary_42d_above_stays_confirmed(days: int) -> None:
    assert _classify_after(days) == "confirmed"


def test_the_confirmed_rung_is_reached_by_elapsed_time_not_by_dom() -> None:
    """A long-vacant listing removed yesterday is PENDING, not confirmed.

    The two 42s in this pipeline measure different things — days a chain sat on
    market (F4-S3's stitch gap is not this) versus days since it left. A
    classifier reading ``effective_dom`` where it should read
    ``as_of - removed`` passes every test above (all of which hold DOM fixed at
    20) and fails this one."""
    assert _classify_after(2, dom=300) == "pending", (
        "a listing that sat 300 days and was removed 2 days ago is a *pending* removal — "
        "its own time on market says nothing about whether it leased"
    )


# ---------------------------------------------------------------------------
# BOUNDARY 3 — 6 weeks (42d): the withdrawal-suspect window's FLOOR
# ---------------------------------------------------------------------------


def _suspect_pair(gap_days: int, *, config: Config | None = None, as_of: date | None = None):
    """Shape a unit that re-listed exactly ``gap_days`` after its removal."""
    first_listed = date(2025, 1, 6)
    records = spell_pair_with_gap(
        address="200 W Suspect St", first_listed=first_listed, first_dom=20, gap_days=gap_days
    )
    comps = shape_raw_pull(
        records, [], config or Config(), as_of or date(2026, 12, 1), *FULL_YEAR
    )
    return sorted(comps, key=lambda c: c.first_listed)


def test_boundary_6w_below_a_relist_at_41_days_is_stitched_not_suspected() -> None:
    """BELOW the floor: at the default knobs a 41-day gap is not a withdrawal
    at all — it is one continuous vacancy, and F4-S3 stitches it.

    This is the honest "below" side of the 6-week floor, and it is the reason
    the floor and the stitch threshold agree at the default: a gap the stitcher
    swallowed never becomes two spells to be suspicious about. What must NOT
    happen is a 41-day gap producing two comps of which the first is flagged."""
    comps = _suspect_pair(41)
    assert len(comps) == 1, (
        f"a 41-day gap is under the 42-day stitch threshold and must merge into ONE chain, "
        f"got {len(comps)} comps"
    )
    assert comps[0].relist_count == 1, "the merged chain records the re-list"
    assert comps[0].withdrawal_suspect is False, (
        "a stitched re-list is a continuous vacancy, not a withdrawal — flagging it would "
        "put a 'lease uncertain' badge on a comp whose lease was never claimed"
    )


def test_boundary_6w_exactly_forty_two_days_is_a_withdrawal_suspect() -> None:
    """EXACTLY ON the floor: 6 weeks == 42 days, and the window is inclusive
    at the bottom ("re-lists 6w-6mo later").

    This is the one assertion that separates ``gap > 42`` from ``gap >= 42``.
    The real pull's nearest evidence is a 44-day gap, so nothing in the
    committed data can tell those two implementations apart."""
    comps = _suspect_pair(42)
    assert len(comps) == 2, (
        f"a 42-day gap is NOT under the 42-day stitch threshold and must stay two separate "
        f"chains, got {len(comps)}"
    )
    assert comps[0].withdrawal_suspect is True, (
        "the earlier, completed spell re-listed exactly 6 weeks later — that is the floor of "
        "the withdrawal-suspect window, inclusive"
    )


@pytest.mark.parametrize("gap", [43, 44, 60, 120, 148])
def test_boundary_6w_above_stays_suspect_through_the_window(gap: int) -> None:
    """Inside the window, every gap flags. 44 and 148 are the real pull's own
    nearest-to-floor and nearest-to-ceiling gaps, restated here as literals so
    this file states the claim even if the fixture is ever re-pulled."""
    comps = _suspect_pair(gap)
    assert len(comps) == 2
    assert comps[0].withdrawal_suspect is True, f"a {gap}-day re-list is inside 6w-6mo"


def test_the_later_spell_of_a_suspect_pair_is_not_itself_suspect() -> None:
    """The flag belongs to the spell whose *lease is in doubt* — the one that
    completed and then re-appeared — not to the re-listing that raised the
    doubt. Flagging both doubles the suspect count in every bucket stat."""
    comps = _suspect_pair(90)
    assert comps[0].withdrawal_suspect is True
    assert comps[1].withdrawal_suspect is False, (
        "the re-list is the evidence, not the suspect; only the earlier completed spell "
        "carries the flag"
    )


def test_a_censored_spell_is_never_a_withdrawal_suspect() -> None:
    """Only a *complete* spell can be suspected of a fake lease. A still-active
    listing never claimed one, so there is nothing to doubt — and a censored
    comp carrying a 'lease uncertain' badge would contradict the single most
    important distinction in the system (NORTH_STAR)."""
    listed = date(2025, 3, 1)
    records = [
        open_record(id_="c-open", address="210 W Open St", listed=listed),
        closed_record(
            id_="c-later",
            address="210 W Open St",
            listed=listed + timedelta(days=90),
            removed=listed + timedelta(days=120),
        ),
    ]
    comps = shape_raw_pull(records, [], Config(), date(2026, 6, 1), *FULL_YEAR)
    censored = [c for c in comps if c.censored]
    assert censored, "the still-active spell must survive shaping as a censored comp"
    for comp in censored:
        assert comp.withdrawal_suspect is False, (
            "a censored (still-listed) spell has not completed and cannot be a "
            "withdrawal suspect"
        )


# ---------------------------------------------------------------------------
# BOUNDARY 4 — 6 months: the withdrawal-suspect window's CEILING
# ---------------------------------------------------------------------------


def _first_gap_not_suspect(config: Config, *, lo: int, hi: int) -> int:
    """The smallest gap in ``[lo, hi]`` that is NOT flagged, asserting on the
    way that the flag crosses exactly once (monotone in the gap).

    Stated as a crossing rather than as an equality because "6 months" has no
    day count in the story — see this module's docstring."""
    as_of = date(2028, 1, 1)  # far enough that both spells are confirmed at every gap
    seen: list[tuple[int, bool]] = []
    for gap in range(lo, hi + 1):
        comps = _suspect_pair(gap, config=config, as_of=as_of)
        assert len(comps) == 2, f"gap {gap} must stay two chains, got {len(comps)}"
        seen.append((gap, comps[0].withdrawal_suspect))

    flags = [flag for _, flag in seen]
    crossings = sum(1 for a, b in zip(flags, flags[1:], strict=False) if a != b)
    assert crossings == 1, (
        "the withdrawal-suspect flag must switch off exactly once as the re-list moves "
        f"further out — it is a window with one ceiling, not a pattern. Observed {crossings} "
        f"switches across gaps {lo}..{hi}: {seen}"
    )
    assert flags[0] is True and flags[-1] is False, (
        f"gaps {lo}..{hi} must start inside the window and end outside it; got {seen[:3]} ... "
        f"{seen[-3:]}"
    )
    return next(gap for gap, flag in seen if flag is False)


def test_the_six_month_ceiling_is_a_single_sharp_crossing_at_six_months() -> None:
    """EXACTLY ON / either side of the ceiling, without pinning month arithmetic.

    Six months from a removal is 180 days counted as 6x30 and 181-184 days
    counted on the calendar. Either is a defensible reading of the story, so
    this asserts the crossing lands in that band and nowhere else — which kills
    a ceiling that is missing, hardcoded to the wrong horizon, or accidentally
    reading weeks. See the module docstring for what is deliberately not
    pinned; the surviving +-1-day mutant at this boundary is reported to the PM
    rather than silently covered."""
    crossing = _first_gap_not_suspect(Config(), lo=150, hi=220)
    assert 180 <= crossing <= 186, (
        f"the first NON-suspect gap is {crossing} days after removal; six months is 180 days "
        "(6x30) to 184 days (calendar), so the ceiling is in the wrong place"
    )


def test_the_six_month_ceiling_moves_with_its_config_knob() -> None:
    """``withdrawal_suspect_months`` is a real knob (2-10, default 6), so the
    ceiling must be *derived* from it and not hardcoded.

    A ceiling frozen at six months passes the test above and fails here — and
    it fails silently in production for any user who tuned the knob, which is
    exactly the class of defect F0-S5's "a knob change re-derives like any
    other input" exists to prevent."""
    two_months = _first_gap_not_suspect(Config(withdrawal_suspect_months=2), lo=45, hi=90)
    assert 60 <= two_months <= 63, (
        f"with withdrawal_suspect_months=2 the window must close around 60 days, got "
        f"{two_months}"
    )
    assert two_months < _first_gap_not_suspect(Config(), lo=150, hi=220), (
        "a smaller withdrawal_suspect_months must close the window sooner"
    )


@pytest.mark.parametrize("gap", [189, 193, 250, 400])
def test_boundary_6mo_above_a_late_relist_is_a_separate_market_cycle(gap: int) -> None:
    """Past the ceiling a re-list is a new tenancy ending, not doubt about the
    old one. 189 and 193 are the real pull's two nearest-above gaps."""
    comps = _suspect_pair(gap, as_of=date(2028, 1, 1))
    assert comps[0].withdrawal_suspect is False, (
        f"a re-list {gap} days later is past six months and must not cast doubt on the "
        "earlier lease"
    )


# ---------------------------------------------------------------------------
# Stage ordering — classify runs BEFORE the window filter (PM, this dispatch)
# ---------------------------------------------------------------------------


def test_a_suspect_flag_survives_a_relist_that_the_window_filter_later_drops() -> None:
    """The epic flow orders the pipeline classify -> window-filter, and this is
    the only observable difference between the two orderings.

    A unit completes a spell inside the window and re-lists 153 days later,
    *outside* it. The re-list is dropped by F4-S4's filter and never reaches
    the response — but it is still the evidence that the first spell's lease is
    uncertain. If flagging ran after the filter, the surviving comp would lose
    its badge because its own evidence had already been discarded."""
    window = ("01-01", "06-30")
    first_listed = date(2025, 2, 1)
    records = spell_pair_with_gap(
        address="300 W Ordering St", first_listed=first_listed, first_dom=20, gap_days=153
    )
    relisted = date.fromisoformat(records[1]["listedDate"][:10])
    assert relisted.month > 6, "fixture must re-list outside the window for this test to bite"

    comps = shape_raw_pull(records, [], Config(), date(2026, 6, 1), *window)
    assert len(comps) == 1, (
        f"the re-listed spell starts {relisted} — outside the window — and must be filtered "
        f"out; got {len(comps)} comps"
    )
    assert comps[0].first_listed == first_listed
    assert comps[0].withdrawal_suspect is True, (
        "the surviving in-window spell must still be flagged: its re-list is evidence about "
        "it even though the re-list itself is out of window. Flagging after the window filter "
        "loses this."
    )
