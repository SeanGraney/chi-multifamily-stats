"""F4-S1 [BE] Query planner. QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

STORY, VERBATIM
----------------
"[BE] Query planner. [INVARIANT] per spec §3.2 — the date-window algorithm
itself is the definition of which comps get pulled; there's no alternative
computation that preserves meaning. For each year y in 0..N-1 compute the
daysOld window with PAD=90 on both sides, daysOldMin floored at 1; emit two
queries per year (status=Active, status=Inactive), limit=500, offset
pagination until short page."

AC: "unit tests with a frozen 'today' covering: window in current year
partially in the future (daysOldMin floor), year boundaries (Dec-Jan windows
spanning year end), leap day. Planner output is a pure function of
(params, today)."

Spec formula (functional spec §3.2, lines 80-93), reproduced because this
IS the invariant under test, not a paraphrase of it:

    windowStart_y = (windowStartMonthDay in year: currentYear - y)
    windowEnd_y   = (windowEndMonthDay   in year: currentYear - y)
    daysOldMin    = today - windowEnd_y   - PAD   (floor at 1)
    daysOldMax    = today - windowStart_y + PAD

WHY LAYER 1 ONLY
----------------
Every clause is "given these plain values (today, the window bounds, N),
what list does the pure function return" -- AGENT_QA.md's decision
procedure step 1, no request body, no assembled state, no route exists for
this yet (F4-S1 is a planning step inside a not-yet-built F4 pull flow; see
the epics doc and ARCHITECTURE.md D21/§8, which names "query planner"
directly as a Layer-1 example). Pagination ("offset pagination until short
page") is explicitly OUT of this file's scope: `RentCastClient.fetch_listings`
(F0-S4, already built -- see `rentcomp/client/rentcast.py`) already owns
paging via `offset`/`X-Total-Count`/short-page detection; this story only
plans the (year, status, daysOld-window) tuples that get handed to it. No L2
or L3 coverage is owed here for the same reason F0-S3/F0-S5 had none.

CONTRACT THESE TESTS PIN (QA-PROPOSED -- story names no module, function, or
return shape; PM/dev may counter-propose, QA then edits)
----------------------------------------------------------------------------
``rentcomp.client.planner``

    ``PlannedQuery`` (frozen dataclass):
        year: int                cohort year label = windowStart_y's
                                  calendar year (matches F4-S4's "cohort =
                                  calendar year of stitched start" -- the
                                  label always follows the window's START,
                                  never its end)
        status: str               "Active" | "Inactive"
        window_start: date         resolved windowStart_y
        window_end: date           resolved windowEnd_y
        days_old_min: int          floored at 1
        days_old_max: int          never floored (story only floors MIN)
        params: dict                full wire params, built via
                                     ``rentcomp.client.query.build_listing_params``
                                     with ``days_old=rentcast_range(days_old_min,
                                     days_old_max)`` -- NOT a hand-rolled string,
                                     so a change to the wire-syntax rules in
                                     query.py cannot silently drift out of sync
                                     with what this module emits.

    ``plan_pull_queries(today, *, years_back, window_start_mmdd,
    window_end_mmdd, address, radius, bedrooms, bathrooms=None,
    property_types) -> list[PlannedQuery]``

        Returns exactly ``2 * years_back`` entries: for y in 0..years_back-1
        (in that order), one Active then one Inactive query (in that order)
        for cohort year ``today.year - y``. Pure: same arguments in, same
        list out, every time, no wall-clock read (``today`` is the only time
        input, passed explicitly -- consistent with the F0-S2 ADR's "as_of
        is data, never the wall clock" precedent, even though the AST guard
        itself is scoped to `pipeline/`+`stats/` only per that ADR's erratum;
        this module earns the same discipline by convention since it is
        equally a "meaning" input, not enforced by the guard).

AMBIGUITIES FLAGGED FOR THE PM (QA-decided so the story is testable at all;
not resolved by the story text as written -- confirm or override)
----------------------------------------------------------------------------
1. **Dec-Jan (or any) window that spans a calendar-year boundary.** The
   spec's formula literally assigns BOTH windowStart_y and windowEnd_y to
   the SAME "year: currentYear - y" -- taken completely literally, a window
   like "12-15" to "01-15" would put windowEnd (Jan 15) chronologically
   BEFORE windowStart (Dec 15) in the same labeled year, which cannot be a
   valid interval. QA's resolution (the only one consistent with F4-S4's
   already-stated "cohort = calendar year of stitched start", which only
   makes sense if the cohort label always follows the window's START):
   compare the month-day tuples; if end < start, the window WRAPS and
   windowEnd resolves to (currentYear - y) + 1 instead. This is asserted
   directly in `test_year_boundary_window_wraps_the_end_into_next_calendar_year`
   via the exposed `window_start`/`window_end` fields, not inferred
   indirectly through daysOldMin/Max (which would conflate this with
   flooring in the harder-to-read case below).
2. **Feb 29 as a window boundary in a year where `currentYear - y` is not
   itself a leap year.** Not addressed by the story text at all. QA's
   resolution: clamp to Feb 28 (never expand a window; the alternative,
   silently rolling to Mar 1, would be an equally defensible but different
   invariant -- flagged, not asserted as obviously correct). See
   `test_leap_day_boundary_clamps_to_feb_28_in_a_non_leap_cohort_year`.
3. **Deterministic emission order** (which of the ``2 * years_back`` entries
   comes first). Not literally an AC line item, but "pure function" implies
   SOME fixed order, and the story's own prose lists "y in 0..N-1" then
   "status=Active, status=Inactive" in that order -- QA pins that literal
   reading. If the developer has a reason to emit a different (still
   deterministic) order, that is a `[DEFAULT]`-class swap, not a violation,
   as long as every OTHER assertion here still holds against the reordered
   list (e.g. by sorting both sides before comparing) -- flag it in the
   handoff rather than silently breaking this file.

EXPECTED RED STATE TODAY: one import-gate test fails naming exactly what is
missing (`rentcomp.client.planner`); everything else SKIPS behind it.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.client.query import build_listing_params, rentcast_range

try:
    from rentcomp.client.planner import PlannedQuery, plan_pull_queries

    _PLANNER_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - red state before F4-S1 lands
    _PLANNER_ERROR = exc

needs_planner = pytest.mark.skipif(
    _PLANNER_ERROR is not None,
    reason=f"rentcomp.client.planner missing (F4-S1 red): {_PLANNER_ERROR}",
)

# ---------------------------------------------------------------------------
# shared fixtures -- common params every planned query must carry unchanged
# ---------------------------------------------------------------------------

ADDRESS = "3651 S Wood St, Chicago, IL 60609"
RADIUS = 2.0
BEDROOMS = "3:4"
BATHROOMS = None  # omitted deliberately -- exercises F0-S4's "no bathrooms key at all"
PROPERTY_TYPES = ["Multi-Family", "Apartment", "Townhouse"]

PAD_DAYS = 90  # the story's own constant, restated -- not imported, this IS what's pinned


def _expected_params(*, status: str, days_old_min: int, days_old_max: int) -> dict:
    """The wire params THIS query should carry, built the same way any other
    caller of `client.query` would -- i.e. what "reuse, don't reinvent" means
    concretely for this test file."""
    return build_listing_params(
        address=ADDRESS,
        radius=RADIUS,
        bedrooms=BEDROOMS,
        bathrooms=BATHROOMS,
        property_types=PROPERTY_TYPES,
        status=status,
        days_old=rentcast_range(days_old_min, days_old_max),
    )


def _plan(today: date, *, years_back: int, window_start_mmdd: str, window_end_mmdd: str):
    return plan_pull_queries(
        today,
        years_back=years_back,
        window_start_mmdd=window_start_mmdd,
        window_end_mmdd=window_end_mmdd,
        address=ADDRESS,
        radius=RADIUS,
        bedrooms=BEDROOMS,
        bathrooms=BATHROOMS,
        property_types=PROPERTY_TYPES,
    )


def _find(queries: list[PlannedQuery], *, year: int, status: str) -> PlannedQuery:
    matches = [q for q in queries if q.year == year and q.status == status]
    assert len(matches) == 1, (
        f"expected exactly one {status} query for cohort year {year}, "
        f"found {len(matches)} (all years present: {sorted({q.year for q in queries})})"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# existence -- legible red
# ---------------------------------------------------------------------------


def test_planner_module_imports_its_contract() -> None:
    assert _PLANNER_ERROR is None, (
        "Cannot import PlannedQuery/plan_pull_queries from rentcomp.client.planner "
        f"(QA-proposed contract, see this module's docstring): {_PLANNER_ERROR}"
    )


# ---------------------------------------------------------------------------
# baseline -- a fully-past, non-edge-case window: sanity + wire-syntax reuse
# ---------------------------------------------------------------------------


@needs_planner
def test_baseline_window_computes_the_stated_formula_exactly() -> None:
    """No floor, no year wrap, no leap day -- just the formula, so a failure
    here means the arithmetic itself is wrong, not an edge case."""
    today = date(2026, 12, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")

    q = _find(queries, year=2026, status="Active")
    assert q.window_start == date(2026, 6, 1)
    assert q.window_end == date(2026, 6, 30)
    assert q.days_old_min == 64, "today(Dec 1) - windowEnd(Jun 30) - PAD(90) = 64"
    assert q.days_old_max == 273, "today(Dec 1) - windowStart(Jun 1) + PAD(90) = 273"


@needs_planner
def test_planner_reuses_client_query_wire_syntax_not_a_hand_rolled_string() -> None:
    """The `params` on every emitted query must be exactly what
    `build_listing_params` + `rentcast_range` would produce for that
    query's own (status, daysOldMin, daysOldMax) -- proves the planner
    delegates wire-syntax formatting rather than reinventing it (F0-S4's
    colon/pipe rules stay the single source of truth)."""
    today = date(2026, 12, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")
    assert len(queries) == 2
    for q in queries:
        assert q.params == _expected_params(
            status=q.status, days_old_min=q.days_old_min, days_old_max=q.days_old_max
        )
        assert q.params["limit"] == 500


# ---------------------------------------------------------------------------
# two queries per year, Active + Inactive, across years_back = 1, 2, 5
# ---------------------------------------------------------------------------


@needs_planner
@pytest.mark.parametrize("years_back", [1, 2, 5])
def test_emits_exactly_two_queries_per_cohort_year(years_back: int) -> None:
    today = date(2026, 12, 1)
    queries = _plan(
        today, years_back=years_back, window_start_mmdd="06-01", window_end_mmdd="06-30"
    )
    assert len(queries) == 2 * years_back

    expected_years = [today.year - y for y in range(years_back)]
    assert sorted({q.year for q in queries}) == sorted(expected_years)
    for year in expected_years:
        statuses = sorted(q.status for q in queries if q.year == year)
        assert statuses == ["Active", "Inactive"], (
            f"cohort year {year} must have exactly one Active and one Inactive query, "
            f"got {statuses}"
        )
    for q in queries:
        assert q.params["limit"] == 500


@needs_planner
def test_emission_order_is_year_then_active_then_inactive() -> None:
    """QA-proposed determinism (ambiguity #3 above): y=0..N-1 in order, each
    year's Active before its Inactive, matching the story prose's own
    literal listing order. A developer with a good reason for a different
    deterministic order should flag it via the PM rather than silently
    breaking this test."""
    today = date(2026, 12, 1)
    queries = _plan(today, years_back=3, window_start_mmdd="06-01", window_end_mmdd="06-30")
    assert [(q.year, q.status) for q in queries] == [
        (2026, "Active"),
        (2026, "Inactive"),
        (2025, "Active"),
        (2025, "Inactive"),
        (2024, "Active"),
        (2024, "Inactive"),
    ]


# ---------------------------------------------------------------------------
# AC case 1 -- current-year window PARTIALLY in the future: daysOldMin floor
# ---------------------------------------------------------------------------


@needs_planner
def test_current_year_window_partially_in_the_future_floors_days_old_min_at_one() -> None:
    """`today` sits INSIDE the window (Jun 20, between Jun 1 and Jun 30):
    the raw daysOldMin (today - windowEnd - PAD) is deeply negative because
    windowEnd hasn't happened yet. Must floor at 1, never go negative or
    zero -- a negative/zero daysOld is not a real RentCast range value."""
    today = date(2026, 6, 20)
    queries = _plan(today, years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")

    q = _find(queries, year=2026, status="Active")
    assert q.window_start == date(2026, 6, 1)
    assert q.window_end == date(2026, 6, 30)
    assert q.days_old_min == 1, (
        "raw daysOldMin = (Jun20 - Jun30) - 90 = -100; must floor at 1, not go negative"
    )
    assert q.days_old_max == 109, "today(Jun 20) - windowStart(Jun 1) + PAD(90) = 109"
    # the floor must be reflected in the emitted wire param too, not just the field
    assert q.params["daysOld"] == rentcast_range(1, 109)


@needs_planner
def test_days_old_min_never_drops_below_one_even_in_wholly_future_windows() -> None:
    """A window that has not started at all yet (today well before
    windowStart) is an even more extreme version of the floor case --
    confirms the floor is a hard clamp, not merely "less negative"."""
    today = date(2026, 1, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")
    q = _find(queries, year=2026, status="Inactive")
    assert q.days_old_min == 1


# ---------------------------------------------------------------------------
# AC case 2 -- year boundary: a Dec-Jan window spanning year end
# ---------------------------------------------------------------------------


@needs_planner
def test_year_boundary_window_wraps_the_end_into_next_calendar_year() -> None:
    """Window "12-15" -> "01-15": for cohort year Y, windowEnd must resolve
    to Jan 15 of Y+1, never Y (which would make windowEnd chronologically
    BEFORE windowStart -- see ambiguity #1). Checked directly via the
    resolved `window_start`/`window_end` fields so this test is not
    entangled with the separate flooring behavior."""
    today = date(2027, 6, 1)
    queries = _plan(today, years_back=2, window_start_mmdd="12-15", window_end_mmdd="01-15")

    # y=1 -> cohort year 2026: fully elapsed by today (Jun 2027), clean numbers
    older = _find(queries, year=2026, status="Active")
    assert older.window_start == date(2026, 12, 15)
    assert older.window_end == date(2027, 1, 15), (
        "the window's END must fall in the year AFTER its START's year "
        "(2027, not 2026) -- the wrap is the whole point of this test"
    )
    assert older.days_old_min == 47
    assert older.days_old_max == 258

    # y=0 -> cohort year 2027: this instance of the window hasn't happened
    # yet as of today (Jun 2027 is before Dec 2027) -- still wraps the same
    # way, and daysOldMin still floors at 1 even though daysOldMax goes
    # negative for this genuinely-not-yet-open window (an accurate, if
    # degenerate, consequence of the literal formula -- not a bug).
    newer = _find(queries, year=2027, status="Active")
    assert newer.window_start == date(2027, 12, 15)
    assert newer.window_end == date(2028, 1, 15)
    assert newer.days_old_min == 1


@needs_planner
def test_non_wrapping_window_does_not_spuriously_roll_the_end_into_next_year() -> None:
    """Guards the other side of ambiguity #1: an ordinary same-year window
    (start <= end) must NOT roll over just because the wrap logic exists."""
    today = date(2026, 12, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")
    q = _find(queries, year=2026, status="Active")
    assert q.window_start.year == 2026
    assert q.window_end.year == 2026, "Jun 1 -> Jun 30 does not span a year boundary"


# ---------------------------------------------------------------------------
# AC case 3 -- leap day (Feb 29) as a window boundary
# ---------------------------------------------------------------------------


@needs_planner
def test_leap_day_boundary_resolves_normally_in_an_actual_leap_year() -> None:
    """2024 IS a leap year -- Feb 29 exists and must be used as-is, no
    clamping, no shift."""
    today = date(2024, 12, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="02-29", window_end_mmdd="03-15")
    q = _find(queries, year=2024, status="Active")
    assert q.window_start == date(2024, 2, 29)
    assert q.window_end == date(2024, 3, 15)
    assert q.days_old_min == 171
    assert q.days_old_max == 366


@needs_planner
def test_leap_day_boundary_clamps_to_feb_28_in_a_non_leap_cohort_year() -> None:
    """2025 is NOT a leap year -- Feb 29, 2025 does not exist. QA's
    resolution (ambiguity #2 above): clamp to Feb 28 rather than raising or
    silently rolling to Mar 1. Must not raise ValueError/date-construction
    error for any cohort year the years-back range touches."""
    today = date(2025, 12, 1)
    queries = _plan(today, years_back=1, window_start_mmdd="02-29", window_end_mmdd="03-15")
    q = _find(queries, year=2025, status="Active")
    assert q.window_start == date(2025, 2, 28), "Feb 29 in a non-leap year must clamp to Feb 28"
    assert q.window_end == date(2025, 3, 15)
    assert q.days_old_min == 171
    assert q.days_old_max == 366


@needs_planner
def test_leap_day_boundary_across_a_multi_year_span_never_raises() -> None:
    """A 5-year-back pull straddling both leap and non-leap cohort years
    (2024 leap; 2025/2026/2027/2028 -- note 2028 IS also a leap year) must
    plan every year without raising, mixing the clamp only where needed."""
    today = date(2028, 12, 1)
    queries = _plan(today, years_back=5, window_start_mmdd="02-29", window_end_mmdd="03-15")
    assert len(queries) == 10
    starts_by_year = {q.year: q.window_start for q in queries}
    assert starts_by_year[2028] == date(2028, 2, 29)  # leap
    assert starts_by_year[2027] == date(2027, 2, 28)  # clamp
    assert starts_by_year[2026] == date(2026, 2, 28)  # clamp
    assert starts_by_year[2025] == date(2025, 2, 28)  # clamp
    assert starts_by_year[2024] == date(2024, 2, 29)  # leap


# ---------------------------------------------------------------------------
# "planner output is a pure function of (params, today)"
# ---------------------------------------------------------------------------


@needs_planner
def test_identical_arguments_produce_identical_output_every_time() -> None:
    today = date(2026, 12, 1)
    kwargs = dict(years_back=2, window_start_mmdd="06-01", window_end_mmdd="06-30")

    first = _plan(today, **kwargs)
    second = _plan(today, **kwargs)
    assert first == second

    # an intervening call with DIFFERENT arguments must not leak into a
    # later call with the ORIGINAL arguments (rules out shared mutable
    # state, e.g. a mutable default argument or module-level accumulator)
    _plan(date(2019, 1, 1), years_back=5, window_start_mmdd="01-01", window_end_mmdd="12-31")
    third = _plan(today, **kwargs)
    assert third == first


@needs_planner
def test_a_different_today_produces_a_different_result() -> None:
    """Sanity check paired with the determinism test above: the function
    must actually be a function OF today, not a constant that happens to
    pass the equality check by ignoring its input."""
    kwargs = dict(years_back=1, window_start_mmdd="06-01", window_end_mmdd="06-30")
    a = _plan(date(2026, 12, 1), **kwargs)
    b = _plan(date(2020, 12, 1), **kwargs)
    assert a != b


# ---------------------------------------------------------------------------
# property test -- general invariants across many random configurations
# (Feb 29 excluded here on purpose: it is covered exhaustively above, and
# mixing it into a hypothesis-generated mmdd would make failures illegible)
# ---------------------------------------------------------------------------

_NON_LEAP_YEAR_DATES = st.dates(min_value=date(2001, 1, 1), max_value=date(2001, 12, 31))


@needs_planner
@settings(max_examples=40, deadline=None)
@given(
    today=st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31)),
    years_back=st.integers(min_value=1, max_value=5),
    start_mmdd_source=_NON_LEAP_YEAR_DATES,
    end_mmdd_source=_NON_LEAP_YEAR_DATES,
)
def test_property_output_shape_and_floor_hold_for_any_valid_configuration(
    today: date, years_back: int, start_mmdd_source: date, end_mmdd_source: date
) -> None:
    start_mmdd = start_mmdd_source.strftime("%m-%d")
    end_mmdd = end_mmdd_source.strftime("%m-%d")

    queries = _plan(today, years_back=years_back, window_start_mmdd=start_mmdd, window_end_mmdd=end_mmdd)

    assert len(queries) == 2 * years_back
    assert {q.year for q in queries} == {today.year - y for y in range(years_back)}
    for q in queries:
        assert q.days_old_min >= 1, "daysOldMin must never be < 1, for any config"
        assert q.status in ("Active", "Inactive")
        assert q.params["limit"] == 500

    # determinism holds for every randomly generated configuration too, not
    # just the hand-picked scenarios above
    replay = _plan(
        today, years_back=years_back, window_start_mmdd=start_mmdd, window_end_mmdd=end_mmdd
    )
    assert replay == queries
