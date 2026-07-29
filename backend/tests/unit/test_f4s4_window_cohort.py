"""F4-S4 [INVARIANT] — window filter + cohort assignment. QA-authored,
written RED before the developer starts (AGENT_QA.md protocol).

THE STORY, VERBATIM
-------------------
    Keep records whose *stitched* start month-day falls inside the
    year-agnostic window; assign cohort = calendar year of stitched start.

    AC: a listing re-listed inside the window but originally listed before it
    is kept iff its stitched start is inside; padding-only records (pulled but
    stitched-start outside) are dropped and counted in a pipeline debug
    summary.

This file owns the first clause and the first half of the AC. The
"counted in a pipeline debug summary" half needs a seam that does not exist
yet and lives in `test_f4s4_padding_debug_summary.py`; the property-shaped
statements of the same invariants live in `test_f4s4_window_properties.py`.

WHY LAYER 1
-----------
Every assertion here is "call a function with plain values, look at what comes
back" — AGENT_QA.md's decision procedure stops at step 1. `shape_raw_pull`
already takes `window_start_mmdd` / `window_end_mmdd` and `as_of` as ordinary
arguments and already returns the comps, so no request body, no assembled
curation state and no browser is needed to state any of it. A window-boundary
assertion in Playwright would be an L2 test in a browser costume, and a loop
over 365 candidate start dates (which is exactly what `test_..._sweep` below
is) belongs nowhere near one.

NO NEW SEAM IS ASKED FOR HERE, deliberately. Unlike F4-S3 — whose "threshold
0 => no merging" criterion was literally unreachable through `shape_raw_pull`
because `Config.stitch_gap_days` is `ge=7` — this story's window bounds are
already plain `"MM-DD"` arguments to the shipped entry point. Pinning an
internal `_in_window` helper would pin structure the AC does not mention.

THE TWO READINGS THIS FILE PINS, AND WHY (flagged to the PM, not decided here)
-----------------------------------------------------------------------------
1. **Both ends are INCLUSIVE.** The story says "falls inside the ... window"
   without naming an edge convention, but every other statement of the window
   in the project spells it as a closed interval a user types into two
   month-day pickers: `docs/rentcomp_functional_spec.md` §1 defines a cohort as
   "all comps whose stitched start date falls in the same year's date window
   (e.g., **'June 15-30, 2024' cohort**)", §2.1 gives "Date window |
   month-day -> month-day | Year-agnostic, e.g. **Jun 15 - Jun 30**", and §6.3's
   preview line reads "**Jun 15-30** - 2026, 2025 (2 cohorts)". A half-open
   reading would silently exclude June 30 from the "June 15-30" cohort. If the
   owner rules otherwise, exactly the four `..._on_the_window_..._day_is_kept/
   dropped` tests below change, and nothing else does.

2. **A window whose end month-day sorts before its start WRAPS the year end**
   (Dec 20 -> Feb 15 is one 58-day season, not an empty set). This is not an
   invention of this file: `client/planner.py` already resolves exactly that
   case — "when the end month-day sorts before the start month-day, the end
   resolves into `(currentYear - y) + 1`", recorded there as PM-confirmed at
   F4-S1 dispatch — and F4-S1's own AC names "year boundaries (Dec-Jan windows
   spanning year end)". A filter that did not wrap would drop every record the
   planner just paid to fetch for such a window.

   Note the consequence the story's second clause forces, which this file
   asserts rather than smooths over: cohort is the **calendar year of the
   stitched start**, so a wrapping window's December records and its January
   records land in *different* cohorts. That is the invariant as written; see
   `test_a_wrapping_window_splits_one_season_across_two_calendar_year_cohorts`.

WHAT THIS FILE DOES NOT TOUCH
-----------------------------
`withdrawal_suspect` (F4-S8, running in parallel — PM instruction), premium and
cohort *medians* (F4-S5, Group B), and the stitcher's own merge rule (F4-S3,
shipped). Stitching is used here only as the thing that produces a "stitched
start"; no assertion below depends on where the 42-day threshold sits.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

# ---------------------------------------------------------------------------
# input construction — raw RentCast-shaped dicts, the same shape
# `pipeline/shape.py` documents as its input and the same one F4-S3's QA file
# uses. Nothing here constructs a `Spell` or a `StitchedComp` directly, so the
# domain models are free to move or gain fields without touching this file.
# ---------------------------------------------------------------------------


def _record(id_: str, address: str, listed: date, removed: date | None, price: float = 2000.0) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": None,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 2,
        "bathrooms": 1,
        "squareFootage": 900,
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


def _shape(units: dict[str, list[tuple[date, date | None]]], window: tuple[str, str], as_of: date):
    """`{address: [(listed, removed), ...]}` -> the comps that survive `window`.

    One address per unit, so every unit shapes independently; the segments
    within an address are its spells, and whether they stitch into one chain is
    F4-S3's rule, which this file never asserts on.
    """
    active: list[dict] = []
    inactive: list[dict] = []
    for address, segments in units.items():
        for i, (listed, removed) in enumerate(segments):
            record = _record(f"{address}-{i}", address, listed, removed)
            (active if removed is None else inactive).append(record)
    return shape_raw_pull(active, inactive, Config(), as_of, *window)


def _kept_starts(starts: list[date], window: tuple[str, str]) -> set[date]:
    """Shape one closed 10-day listing per date in `starts`; return the starts
    that survived `window`."""
    as_of = max(starts) + timedelta(days=400)
    units = {f"{i} Window Test St": [(day, day + timedelta(days=10))] for i, day in enumerate(starts)}
    return {comp.first_listed for comp in _shape(units, window, as_of)}


AS_OF = date(2027, 6, 1)


# ---------------------------------------------------------------------------
# The window's own boundaries — asserted deliberately and directly.
#
# F4-S3's verify pass found the 42-day withdrawal floor covered only
# *incidentally*, by a golden-file group that happened to have the right shape.
# The four edge tests below (plus the exhaustive sweep after them) exist so
# this story's boundary is never in that position: each one names the day it
# is about and fails with the day in the message.
# ---------------------------------------------------------------------------

#: (window, day-before, start-day, end-day, day-after). Four windows, chosen so
#: that a single hard-coded month cannot make them all pass: a mid-month window,
#: a month-spanning one, a single-day one, and one on a month's last day.
_EDGES = [
    pytest.param(("06-15", "06-30"), date(2025, 6, 14), date(2025, 6, 15), date(2025, 6, 30), date(2025, 7, 1), id="jun15-jun30"),
    pytest.param(("03-28", "04-04"), date(2025, 3, 27), date(2025, 3, 28), date(2025, 4, 4), date(2025, 4, 5), id="mar28-apr04"),
    pytest.param(("09-09", "09-09"), date(2025, 9, 8), date(2025, 9, 9), date(2025, 9, 9), date(2025, 9, 10), id="single-day"),
    pytest.param(("10-31", "11-30"), date(2025, 10, 30), date(2025, 10, 31), date(2025, 11, 30), date(2025, 12, 1), id="oct31-nov30"),
]


@pytest.mark.parametrize(("window", "before", "start_day", "end_day", "after"), _EDGES)
def test_a_record_starting_on_the_window_start_day_is_kept(window, before, start_day, end_day, after) -> None:
    """Reading 1 (see the module docstring): the start edge is INCLUSIVE.

    "Jun 15-30" is what the user typed and what the preview line echoed back;
    a comp listed on Jun 15 is in the June 15-30 cohort or the label is a lie.
    """
    assert start_day in _kept_starts([start_day], window), (
        f"a record whose stitched start is {start_day} (the window's own start day) was dropped "
        f"by window {window[0]}..{window[1]} — the start comparison is exclusive where the spec's "
        f"'Jun 15-30' cohort label requires it to be inclusive"
    )


@pytest.mark.parametrize(("window", "before", "start_day", "end_day", "after"), _EDGES)
def test_a_record_starting_on_the_window_end_day_is_kept(window, before, start_day, end_day, after) -> None:
    """Reading 1, the other edge — the one a `<` typo actually reaches, since
    `range`-shaped thinking makes the *end* the natural thing to leave open."""
    assert end_day in _kept_starts([end_day], window), (
        f"a record whose stitched start is {end_day} (the window's own end day) was dropped by "
        f"window {window[0]}..{window[1]} — the end comparison is exclusive, so the last day of "
        f"every cohort the user asked for is silently missing from it"
    )


@pytest.mark.parametrize(("window", "before", "start_day", "end_day", "after"), _EDGES)
def test_a_record_starting_the_day_before_the_window_is_dropped(window, before, start_day, end_day, after) -> None:
    """The complement of the start edge. A window that leaks one day early
    pulls in padding records the +/-90d pad fetched but the user never asked
    for — and padding records are what this story exists to remove."""
    assert _kept_starts([before], window) == set(), (
        f"a record whose stitched start is {before} — one day before window "
        f"{window[0]}..{window[1]} opens — survived the filter"
    )


@pytest.mark.parametrize(("window", "before", "start_day", "end_day", "after"), _EDGES)
def test_a_record_starting_the_day_after_the_window_is_dropped(window, before, start_day, end_day, after) -> None:
    assert _kept_starts([after], window) == set(), (
        f"a record whose stitched start is {after} — one day after window "
        f"{window[0]}..{window[1]} closes — survived the filter"
    )


@pytest.mark.parametrize("window", [("06-15", "06-30"), ("03-28", "04-04"), ("09-09", "09-09"), ("10-31", "11-30")])
def test_every_day_of_the_year_is_kept_exactly_when_it_is_inside_the_window(window) -> None:
    """The four edge tests above, as one biconditional over all 365 days.

    Stated as a sweep rather than as two hand-picked days on either side of the
    boundary so the boundary cannot be satisfied by luck: an off-by-one at
    either end, an inverted comparison, or a rule that accidentally admits a
    second month all show up here as a concrete set difference.
    """
    start_mmdd, end_mmdd = window
    year = 2025  # non-leap on purpose; the leap case is asserted separately
    days = [date(year, 1, 1) + timedelta(days=i) for i in range(365)]
    expected = {
        day
        for day in days
        if (start_mmdd <= f"{day.month:02d}-{day.day:02d}" <= end_mmdd)
    }
    kept = _kept_starts(days, window)

    assert kept == expected, (
        f"window {start_mmdd}..{end_mmdd} does not admit exactly the days inside it.\n"
        f"  wrongly kept   : {sorted(kept - expected)}\n"
        f"  wrongly dropped: {sorted(expected - kept)}"
    )


# ---------------------------------------------------------------------------
# The wrapping window (Dec -> Feb)
# ---------------------------------------------------------------------------

_WRAP = ("12-20", "02-15")


@pytest.mark.parametrize(
    ("day", "inside"),
    [
        (date(2025, 12, 19), False),
        (date(2025, 12, 20), True),   # start edge, inclusive
        (date(2025, 12, 31), True),
        (date(2026, 1, 1), True),     # the year boundary itself
        (date(2026, 2, 15), True),    # end edge, inclusive
        (date(2026, 2, 16), False),
        (date(2026, 6, 1), False),    # the middle of the excluded stretch
    ],
)
def test_a_window_that_spans_new_year_wraps(day, inside) -> None:
    """Reading 2. `client/planner.py` already resolves a `12-15 -> 01-15`
    window into a real interval that crosses New Year, and F4-S1's AC names
    "Dec-Jan windows spanning year end" explicitly. If this filter treated
    `end < start` as an empty (or inverted) interval, every record the planner
    just spent calls fetching for that window would be discarded here.
    """
    kept = _kept_starts([day], _WRAP)
    assert (day in kept) is inside, (
        f"{day} is {'inside' if inside else 'outside'} the wrapping window "
        f"{_WRAP[0]}..{_WRAP[1]}, but the filter {'dropped' if inside else 'kept'} it"
    )


def test_a_wrapping_window_admits_exactly_the_days_in_its_two_arms() -> None:
    """The wrap, swept. A naive `start <= key <= end` on a wrapping window
    admits *nothing*; an `and`/`or` slip admits *everything*. Both are one
    character away and both are caught here with the offending days named."""
    start_mmdd, end_mmdd = _WRAP
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(365)]
    expected = {
        day
        for day in days
        if (f"{day.month:02d}-{day.day:02d}" >= start_mmdd or f"{day.month:02d}-{day.day:02d}" <= end_mmdd)
    }
    kept = _kept_starts(days, _WRAP)

    assert kept, f"the wrapping window {start_mmdd}..{end_mmdd} kept nothing at all"
    assert kept != set(days), f"the wrapping window {start_mmdd}..{end_mmdd} kept every day of the year"
    assert kept == expected, (
        f"wrapping window {start_mmdd}..{end_mmdd} does not admit exactly its two arms.\n"
        f"  wrongly kept   : {sorted(kept - expected)}\n"
        f"  wrongly dropped: {sorted(expected - kept)}"
    )


def test_a_wrapping_window_splits_one_season_across_two_calendar_year_cohorts() -> None:
    """The story's second clause, applied to the case that makes it visible.

    A Dec 20 -> Feb 15 window is one continuous leasing season, but "cohort =
    calendar year of stitched start" puts its December records in one cohort
    and its January/February records in the next. That is the invariant as
    written, and it is pinned here rather than left implicit precisely because
    it *looks* like a bug: a well-meaning "keep the season together" fix would
    change what every cohort median (F4-S5) and every drift exponent (F8-S1,
    `(1+d)^(currentYear - cohortYear)`) is computed over.
    """
    december = date(2025, 12, 28)
    january = date(2026, 1, 9)
    units = {
        "10 Wrap Test St": [(december, december + timedelta(days=20))],
        "11 Wrap Test St": [(january, january + timedelta(days=20))],
    }
    comps = _shape(units, _WRAP, AS_OF)
    by_start = {comp.first_listed: comp.cohort_year for comp in comps}

    assert by_start == {december: 2025, january: 2026}, (
        f"expected the December record in cohort 2025 and the January one in cohort 2026, got "
        f"{by_start}"
    )


# ---------------------------------------------------------------------------
# Leap day, and why the comparison must be on (month, day)
# ---------------------------------------------------------------------------


def test_a_february_29_window_bound_does_not_raise_on_a_non_leap_cohort_year() -> None:
    """`client/planner.py` accepts `02-29` as a window bound on purpose (it
    validates month-days against a leap year and clamps per cohort year), so
    this filter must survive one too.

    The failure mode this guards is an implementation that resolves the window
    into concrete `date` objects per cohort year: `date(2025, 2, 29)` raises,
    and a five-year pull would blow up on whichever of its years is not a leap
    year — after the calls have been paid for.
    """
    starts = [date(2024, 2, 29), date(2025, 2, 28), date(2025, 3, 1), date(2024, 2, 28)]
    kept = _kept_starts(starts, ("02-28", "03-01"))
    assert kept == set(starts), f"a 02-28..03-01 window lost days across a leap boundary: {sorted(set(starts) - kept)}"


def test_the_same_month_day_is_inside_the_window_in_every_calendar_year() -> None:
    """"Year-agnostic" is the load-bearing adjective in this story's first
    clause: the window is a month-day range, and membership must not depend on
    which year the record is from.

    The failure mode is a filter written on the day-of-year ordinal: Mar 1 is
    day 61 in a leap year and day 60 otherwise, so a `03-01..03-15` window
    compared as ordinals against a fixed reference year admits Mar 1 in some
    years and not others — a silent, year-dependent cohort gap that no count
    assertion would explain.
    """
    window = ("03-01", "03-15")
    years = [2020, 2021, 2022, 2023, 2024, 2025, 2026]  # 2020 and 2024 are leap
    starts = [date(year, month, day) for year in years for month, day in ((3, 1), (3, 15), (2, 29 if year % 4 == 0 else 28), (3, 16))]
    kept = _kept_starts(starts, window)

    expected = {day for day in starts if (day.month, day.day) in ((3, 1), (3, 15))}
    assert kept == expected, (
        f"window {window[0]}..{window[1]} is not year-agnostic.\n"
        f"  wrongly kept   : {sorted(kept - expected)}\n"
        f"  wrongly dropped: {sorted(expected - kept)}"
    )


# ---------------------------------------------------------------------------
# AC1 — "a listing re-listed inside the window but originally listed before it
#        is kept **iff its stitched start is inside**"
#
# The headline case: the raw record and the stitched record disagree about when
# this listing started, and only the stitched start may decide.
# ---------------------------------------------------------------------------

_JUNE = ("06-15", "06-30")


def test_a_listing_relisted_inside_the_window_but_started_before_it_is_dropped() -> None:
    """AC1, the direction that costs evidence quality if it is wrong.

    RentCast **resets `listedDate` on re-list** (spec §3.2), so this unit's raw
    record says it was listed on Jun 20 when it has actually been sitting since
    May 12. Keeping it would put a ~50-day-old vacancy into the June cohort
    with a `listedDate` that hides its own history, and its `initial_ask` is
    May's price, not June's — the cohort median behind every `premium` would be
    computed over a comp that does not belong to the cohort at all.
    """
    started = date(2025, 5, 12)
    relisted = date(2025, 6, 20)  # inside the window; within the 42d stitch gap
    units = {"20 Relist Test St": [(started, date(2025, 6, 1)), (relisted, date(2025, 7, 10))]}

    comps = _shape(units, _JUNE, AS_OF)
    assert comps == (), (
        "a listing whose latest re-list (2025-06-20) falls inside the June window but whose "
        f"stitched start (2025-05-12) does not was kept: "
        f"{[(c.address, c.first_listed, c.cohort_year) for c in comps]}. The window must be "
        "applied to the stitched start, never to the raw `listedDate`."
    )


def test_a_listing_relisted_outside_the_window_but_started_inside_it_is_kept() -> None:
    """AC1, the other direction — and the reason the +/-90d pad is paid for.

    This unit started on Jun 22 (inside the window) and re-listed on Jul 25
    (outside it). Its raw `listedDate` is July's. Filtering on the raw date
    would discard a genuine June comp *that the pull deliberately spent a
    padded query to fetch* — spec §3.2: "a stitched listing's true start can
    fall inside the window even when its latest re-list doesn't".
    """
    started = date(2025, 6, 22)
    relisted = date(2025, 7, 25)
    units = {"21 Relist Test St": [(started, date(2025, 7, 4)), (relisted, date(2025, 8, 30))]}

    comps = _shape(units, _JUNE, AS_OF)
    assert len(comps) == 1, (
        "a listing whose stitched start (2025-06-22) is inside the June window was dropped because "
        f"its latest re-list (2025-07-25) is outside it: {comps}"
    )
    assert comps[0].first_listed == started
    assert comps[0].cohort_year == 2025


def test_the_relisted_records_cohort_is_the_year_of_its_stitched_start_not_its_relist() -> None:
    """The story's second clause on the case where the two answers differ.

    A unit listed 2024-12-27 and re-listed 2025-01-20 is a **2024** comp. Taking
    the cohort from the re-list would move it a year forward, which is not a
    cosmetic label: F8-S1 compounds drift as `(1 + d) ** (currentYear -
    cohortYear)`, so a mis-assigned cohort silently under-adjusts that comp's
    $/sqft by a full year of assumed market movement, and F4-S5 computes its
    premium against the wrong year's median.
    """
    started = date(2024, 12, 27)
    relisted = date(2025, 1, 20)
    units = {"22 Cohort Test St": [(started, date(2025, 1, 5)), (relisted, date(2025, 2, 14))]}

    comps = _shape(units, ("12-20", "12-31"), AS_OF)
    assert len(comps) == 1, f"expected the 2024-12-27 chain to survive a 12-20..12-31 window, got {comps}"
    assert comps[0].cohort_year == 2024, (
        f"cohort_year is {comps[0].cohort_year}; the stitched start is {comps[0].first_listed} "
        "(2024), and the 2025 answer is the re-list's year"
    )
    assert comps[0].first_listed == started


def test_two_units_in_the_same_window_but_different_years_get_different_cohorts() -> None:
    """The plain reading of "cohort = calendar year of stitched start", across
    the multiple years a year-agnostic window necessarily spans."""
    units = {
        f"3{i} Cohort Year St": [(date(year, 6, 18), date(year, 7, 20))]
        for i, year in enumerate((2023, 2024, 2025, 2026))
    }
    comps = _shape(units, _JUNE, AS_OF)
    assert sorted(comp.cohort_year for comp in comps) == [2023, 2024, 2025, 2026]
    assert all(comp.cohort_year == comp.first_listed.year for comp in comps)


# ---------------------------------------------------------------------------
# The real committed pull — the same claims on data nobody hand-made
#
# AGENT_QA.md / the dispatch: an invariant that only holds on synthetic data has
# caught this project out before. Every number below was measured against
# `fixtures/live-samples/` (the T-S3 gate's two committed raw responses, the
# same pair `storage/pulls.py::_load_ws1_real_pull` shapes) at the same
# `as_of`/config the loader uses.
# ---------------------------------------------------------------------------

_LIVE_SAMPLES = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
_REAL_ACTIVE = _LIVE_SAMPLES / "fe9de5158f036802.json"
_REAL_INACTIVE = _LIVE_SAMPLES / "6327600317b11d16.json"
#: `storage/pulls.py`'s `_WS1_AS_OF` — the fixtures' own `lastSeenDate` epoch.
_REAL_AS_OF = date(2026, 7, 27)


def _real_comps(window: tuple[str, str]):
    active = json.loads(_REAL_ACTIVE.read_text(encoding="utf-8"))
    inactive = json.loads(_REAL_INACTIVE.read_text(encoding="utf-8"))
    return shape_raw_pull(active, inactive, Config(), _REAL_AS_OF, *window)


def test_the_real_pull_shapes_to_the_known_comp_count_under_a_full_year_window() -> None:
    """The oracle every count below is measured against. A full-year window
    admits every month-day by construction, so this is the pull's whole chain
    count — 567, the figure F4-S3 established and QUEUE.md row 6 records."""
    assert len(_real_comps(("01-01", "12-31"))) == 567, (
        "the committed pull no longer shapes to 567 chains under a full-year window; the counts "
        "in this file are measured against that population and must be re-measured"
    )


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        # each row differs from the one above it by exactly one boundary day,
        # and each of those days really carries comps in this pull — so these
        # are boundary assertions on real data, not incidental totals.
        (("06-15", "06-30"), 28),
        (("06-16", "06-30"), 27),  # start edge moved in: loses the 1 comp listed Jun 15
        (("06-15", "06-29"), 23),  # end edge moved in: loses the 5 comps listed Jun 30
        (("06-14", "06-30"), 29),  # start edge moved out: gains the 1 comp listed Jun 14
    ],
)
def test_the_real_pulls_window_counts_move_by_exactly_the_comps_on_each_edge(window, expected) -> None:
    """Inclusivity, measured on the real pull rather than argued.

    If the start edge were exclusive, `06-15..06-30` would return 27 (the
    `06-16` row's answer); if the end edge were, it would return 23 (the
    `06-29` row's). The three neighbours are asserted alongside it so the
    headline number cannot be right for the wrong reason.
    """
    assert len(_real_comps(window)) == expected


def test_the_real_pulls_wrapping_window_counts_move_by_exactly_the_comps_on_each_edge() -> None:
    """The same measurement for a window that crosses New Year. 2 real comps
    have a stitched start of Dec 20 and 1 has Feb 15, so both edges of this
    window are populated in the real data."""
    assert len(_real_comps(("12-20", "02-15"))) == 72
    assert len(_real_comps(("12-21", "02-15"))) == 70, "the 2 real Dec-20 comps are not on the start edge"
    assert len(_real_comps(("12-20", "02-14"))) == 71, "the 1 real Feb-15 comp is not on the end edge"


def test_a_real_cross_year_stitched_chain_is_filtered_and_cohorted_by_its_own_start() -> None:
    """AC1 on the real pull, both directions, one unit.

    `2058 W 23rd St Unit 3F` is one of 13 chains in the committed pull whose
    first and last spells fall in different calendar years: it started
    2024-11-07 and re-listed 2025-01-14. So the raw record says January 2025
    and the stitched record says November 2024, and the two disagree about both
    questions this story answers.
    """
    november = [c for c in _real_comps(("11-01", "11-30")) if c.address.lower().startswith("2058 w 23rd")]
    assert len(november) == 1, (
        "2058 W 23rd St (stitched start 2024-11-07) is missing from a 11-01..11-30 window — the "
        f"filter is not using the stitched start. got: {november}"
    )
    assert november[0].first_listed == date(2024, 11, 7)
    assert november[0].cohort_year == 2024, (
        f"cohort_year is {november[0].cohort_year}; 2025 is the year of this chain's *re-list* "
        "(2025-01-14), not of its stitched start"
    )

    january = [c for c in _real_comps(("01-01", "01-31")) if c.address.lower().startswith("2058 w 23rd")]
    assert january == [], (
        "2058 W 23rd St was kept by a 01-01..01-31 window; only its re-listed spell (2025-01-14) "
        f"falls in January, and its stitched start (2024-11-07) does not. got: {january}"
    )


def test_a_second_real_cross_year_chain_behaves_the_same_way() -> None:
    """`3524 S Lowe Ave Apt 1`: started 2024-09-19, re-listed 2025-01-06.
    A second real witness, so the one above cannot be a single-record fluke."""
    september = [c for c in _real_comps(("09-01", "09-30")) if c.address.lower().startswith("3524 s lowe")]
    assert len(september) == 1 and september[0].first_listed == date(2024, 9, 19)
    assert september[0].cohort_year == 2024
    assert [c for c in _real_comps(("01-01", "01-31")) if c.address.lower().startswith("3524 s lowe")] == []


def test_every_comp_the_real_pull_keeps_is_inside_the_window_and_cohorted_by_its_start() -> None:
    """Both of the story's clauses, over every comp in the real pull, for
    several windows at once — the sanity net under the named cases above."""
    for start_mmdd, end_mmdd in [("06-15", "06-30"), ("12-20", "02-15"), ("01-01", "12-31"), ("02-01", "02-28")]:
        for comp in _real_comps((start_mmdd, end_mmdd)):
            key = f"{comp.first_listed.month:02d}-{comp.first_listed.day:02d}"
            inside = (start_mmdd <= key <= end_mmdd) if start_mmdd <= end_mmdd else (key >= start_mmdd or key <= end_mmdd)
            assert inside, (
                f"{comp.address} {comp.unit} was kept by window {start_mmdd}..{end_mmdd} but its "
                f"stitched start {comp.first_listed} is outside it"
            )
            assert comp.cohort_year == comp.first_listed.year, (
                f"{comp.address} {comp.unit}: cohort_year={comp.cohort_year}, stitched start "
                f"{comp.first_listed}"
            )
