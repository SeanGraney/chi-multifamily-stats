"""F4-S1 Layer 1 — the query planner's cost surface and input contract.

Developer-authored companion to QA's `test_query_planner.py` (which owns the
spec §3.2 formula itself: the floor, the year-boundary wrap, the leap day, the
2-per-year emission, wire-syntax reuse, and purity). Nothing here re-asserts
those; this file owns what QA's plan does not yet hold.

THE PART THAT SPENDS MONEY
--------------------------
The PM's added AC: **a structurally-empty window must never cost an API call.**
QA's `test_year_boundary_window_wraps_the_end_into_next_calendar_year` reads the
negative `days_old_max` of a not-yet-open cohort as "an accurate, if degenerate,
consequence of the literal formula", and as arithmetic that is exactly right.
The problem is what happens to it downstream: `rentcast_range` does no min<=max
validation, so `daysOld=1:-107` goes on the wire, and a listing cannot have been
listed in the future — the call is guaranteed to return nothing. That is one of
50 monthly calls (WORKFLOW.md §6) spent to buy zero records, and one inflated
unit in F2-S3's "est. N API calls", which is a wrong number shown to a user
being asked to consent to a spend.

So the tests below pin three things about it, in the order they matter:

1. the impossible query is **detectable** (`is_structurally_empty`), including
   at the exact min == max boundary, where it is *not* empty;
2. `fetchable_queries` — the single filter both the pull and the estimate use —
   excludes exactly those and nothing else, for any configuration; and
3. the cohort is **still described** by the plan. F2-S3 has to tell the user
   which cohorts were planned and why one of them is empty; a silently absent
   cohort and a planned-but-empty one mean different things to someone deciding
   whether their date window is what they meant.

Plus the input contract (a plan built from a typo'd month-day is worse than no
plan) and a structural no-clock guard, since the F0-S2 ADR's AST guard is scoped
to `pipeline/` + `stats/` and does not reach `client/`.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.client import planner as planner_module
from rentcomp.client.planner import (
    PAD_DAYS,
    PlannedQuery,
    fetchable_queries,
    plan_pull_queries,
)

ADDRESS = "3651 S Wood St, Chicago, IL 60609"
RADIUS = 2.0
BEDROOMS = "3:4"
PROPERTY_TYPES = ["Multi-Family", "Apartment", "Townhouse"]


def _plan(today: date, *, years_back: int, start: str, end: str) -> list[PlannedQuery]:
    return plan_pull_queries(
        today,
        years_back=years_back,
        window_start_mmdd=start,
        window_end_mmdd=end,
        address=ADDRESS,
        radius=RADIUS,
        bedrooms=BEDROOMS,
        property_types=PROPERTY_TYPES,
    )


# ---------------------------------------------------------------------------
# the impossible query is detectable
# ---------------------------------------------------------------------------


def test_the_pms_reproduction_is_flagged_structurally_empty() -> None:
    """The exact case reproduced at spot-check: today 2027-06-01 with a
    12-15 -> 01-15 window. Cohort 2027's window opens in December, six months
    from now; padded, it is still wholly in the future, so `daysOld=1:-107`
    would be sent and would match nothing that exists."""
    queries = _plan(date(2027, 6, 1), years_back=2, start="12-15", end="01-15")

    empty = [q for q in queries if q.year == 2027]
    assert len(empty) == 2
    for q in empty:
        assert q.days_old_min == 1
        assert q.days_old_max == -107
        assert q.is_structurally_empty is True
        assert q.params["daysOld"] == "1:-107", (
            "the malformed range is still built (the plan describes the cohort "
            "honestly); the marker is what stops it reaching the wire"
        )

    # the elapsed cohort in the same plan is a perfectly ordinary, sendable query
    for q in (q for q in queries if q.year == 2026):
        assert q.is_structurally_empty is False


def test_an_ordinary_fully_elapsed_window_is_never_flagged() -> None:
    """The other side of the guard: marking must not creep onto real queries."""
    for q in _plan(date(2026, 12, 1), years_back=5, start="06-01", end="06-30"):
        assert q.is_structurally_empty is False
        assert q.days_old_min <= q.days_old_max


def test_a_single_day_range_where_min_equals_max_is_not_empty() -> None:
    """The boundary the condition turns on. `min == max` is a legitimate
    one-day range (`rentcast_range` exists precisely so a degenerate range is
    still written `5:5` rather than bare), and off-by-one here would silently
    drop a real cohort from a pull instead of merely mis-costing it."""
    # A one-day window opening 89 days from now: days_old_max = -89 + PAD = 1,
    # and days_old_min floors to 1. Exactly on the line, and still sendable.
    today = date(2026, 6, 20)
    boundary = today + timedelta(days=PAD_DAYS - 1)
    q = _plan(
        today,
        years_back=1,
        start=boundary.strftime("%m-%d"),
        end=boundary.strftime("%m-%d"),
    )[0]
    assert (q.days_old_min, q.days_old_max) == (1, 1)
    assert q.is_structurally_empty is False


# ---------------------------------------------------------------------------
# no call is spent, and no estimate counts one
# ---------------------------------------------------------------------------


def test_fetchable_queries_drops_exactly_the_empty_ones_and_keeps_order() -> None:
    queries = _plan(date(2027, 6, 1), years_back=3, start="12-15", end="01-15")
    fetchable = fetchable_queries(queries)

    assert [(q.year, q.status) for q in fetchable] == [
        (2026, "Active"),
        (2026, "Inactive"),
        (2025, "Active"),
        (2025, "Inactive"),
    ], "cohort 2027 has not opened yet; the two elapsed cohorts survive in order"
    assert all(not q.is_structurally_empty for q in fetchable)


def test_the_empty_cohort_is_still_present_in_the_plan_itself() -> None:
    """Marked, not omitted. F2-S3 renders "Dec 15-Jan 15 · 2027, 2026, 2025
    (3 cohorts) · est. N API calls", so the planned-but-empty cohort has to be
    describable — with its resolved window — even though it costs nothing."""
    queries = _plan(date(2027, 6, 1), years_back=3, start="12-15", end="01-15")

    assert len(queries) == 6, "every cohort is still planned"
    assert {q.year for q in queries} == {2027, 2026, 2025}
    empty = next(q for q in queries if q.year == 2027)
    assert (empty.window_start, empty.window_end) == (date(2027, 12, 15), date(2028, 1, 15))


def test_the_call_estimate_excludes_the_impossible_call() -> None:
    """The number F2-S3 shows the user is `len(fetchable_queries(...))`, not
    `2 * years_back`. On a 50-calls-a-month budget the difference is not
    cosmetic — it is a call the user consents to spend on a guaranteed zero."""
    queries = _plan(date(2027, 6, 1), years_back=2, start="12-15", end="01-15")
    assert len(queries) == 4
    assert len(fetchable_queries(queries)) == 2


def test_a_wholly_future_window_costs_nothing_at_all() -> None:
    """Every cohort in the plan is in the future (a 2020 pull for a window that
    opens in December of each year, asked in January). The right answer is zero
    calls planned — not a handful of queries that each return `[]` at a cost."""
    queries = _plan(date(2020, 1, 5), years_back=1, start="06-01", end="06-30")
    assert len(queries) == 2
    assert fetchable_queries(queries) == []


# ---------------------------------------------------------------------------
# the property that has to hold for every configuration, not just these
# ---------------------------------------------------------------------------

_MMDD_SOURCE = st.dates(min_value=date(2001, 1, 1), max_value=date(2001, 12, 31))


@settings(max_examples=60, deadline=None)
@given(
    today=st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31)),
    years_back=st.integers(min_value=1, max_value=5),
    start_source=_MMDD_SOURCE,
    end_source=_MMDD_SOURCE,
)
def test_property_every_fetchable_query_is_a_range_a_listing_could_satisfy(
    today: date, years_back: int, start_source: date, end_source: date
) -> None:
    queries = _plan(
        today,
        years_back=years_back,
        start=start_source.strftime("%m-%d"),
        end=end_source.strftime("%m-%d"),
    )
    fetchable = fetchable_queries(queries)

    for q in fetchable:
        assert 1 <= q.days_old_min <= q.days_old_max, (
            f"a query that will be SENT must have a satisfiable daysOld range, got "
            f"{q.params['daysOld']} for cohort {q.year}"
        )
    for q in queries:
        if q not in fetchable:
            assert q.days_old_max < q.days_old_min, "only impossible ranges are excluded"
            assert q.days_old_max < 1, (
                "an excluded query's window is in the future, since days_old_min "
                "floors at 1 and the exclusion means max < min"
            )
    assert len(fetchable) % 2 == 0, (
        "a cohort's Active and Inactive queries share one window, so they are "
        "always both sendable or both impossible"
    )


@settings(max_examples=40, deadline=None)
@given(
    today=st.dates(min_value=date(2000, 1, 1), max_value=date(2060, 12, 31)),
    start_source=_MMDD_SOURCE,
    end_source=_MMDD_SOURCE,
)
def test_property_a_window_that_has_fully_elapsed_is_always_sendable(
    today: date, start_source: date, end_source: date
) -> None:
    """The converse guard: the marker must not swallow real cohorts. Any cohort
    whose (padded) window start is already in the past yields max >= 1, and with
    min floored at 1 that is always a satisfiable range."""
    queries = _plan(
        today,
        years_back=5,
        start=start_source.strftime("%m-%d"),
        end=end_source.strftime("%m-%d"),
    )
    for q in queries:
        window_has_opened = q.window_start <= today
        if window_has_opened:
            assert not q.is_structurally_empty, (
                f"cohort {q.year} opened on {q.window_start}, before today {today} — "
                "it can contain real listings and must be fetched"
            )


# ---------------------------------------------------------------------------
# input contract — refuse a plan built from a typo
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["06/01", "6-1-2026", "02-30", "13-01", "", "june", "06-01 ", "-06-01"],
)
def test_a_month_day_that_is_not_a_month_day_is_refused(bad: str) -> None:
    """`02-30` and `13-01` are not a date in any year, so clamping them would
    move a window the user never asked to move, and returning a plan anyway
    would spend real calls on it. `02-29` is deliberately NOT in this list — it
    is a valid boundary that clamps per cohort year (QA owns that case)."""
    with pytest.raises(ValueError):
        _plan(date(2026, 12, 1), years_back=1, start=bad, end="06-30")
    with pytest.raises(ValueError):
        _plan(date(2026, 12, 1), years_back=1, start="06-01", end=bad)


@pytest.mark.parametrize("years_back", [0, -1])
def test_a_non_positive_years_back_is_refused_rather_than_planning_nothing(
    years_back: int,
) -> None:
    """An empty plan reads downstream as "0 API calls, nothing to pull", which
    is indistinguishable from a valid free pull. A caller that computed
    `years_back` wrongly should hear about it here."""
    with pytest.raises(ValueError):
        _plan(date(2026, 12, 1), years_back=years_back, start="06-01", end="06-30")


# ---------------------------------------------------------------------------
# structural: the planner cannot read the clock
# ---------------------------------------------------------------------------

CLOCK_ATTRIBUTES = {"today", "now", "utcnow", "fromtimestamp", "monotonic", "perf_counter"}
CLOCK_MODULES = {"time"}


def test_the_planner_module_cannot_read_the_wall_clock() -> None:
    """`today` is data, passed explicitly (the F0-S2 ADR's ruling). That ADR's
    AST guard covers `pipeline/` and `stats/` only, so `client/planner.py` would
    otherwise hold this by convention alone — and it is the module where a
    stray `date.today()` would be least visible and most expensive: the plan
    would silently re-window (and re-cost) itself depending on when it ran,
    breaking cache-key stability along with every frozen-today test above."""
    source = inspect.getsource(planner_module)
    tree = ast.parse(source)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                f"imports {a.name}"
                for a in node.names
                if a.name.split(".")[0] in CLOCK_MODULES
            ]
        elif isinstance(node, ast.ImportFrom):
            # `from datetime import date` is fine (a type). `from time import
            # monotonic` / `from datetime import ...` of a clock reader is not.
            if node.module in CLOCK_MODULES:
                offenders.append(f"imports from {node.module}")
            offenders += [
                f"imports {a.name}" for a in node.names if a.name in CLOCK_ATTRIBUTES
            ]
        elif isinstance(node, ast.Attribute) and node.attr in CLOCK_ATTRIBUTES:
            # `date.today()` / `datetime.now()`. The bare NAME `today` is not
            # checked here (unlike the pipeline guard) because it is this
            # module's explicit parameter — which is the whole point.
            offenders.append(f".{node.attr}()")
    assert not offenders, (
        "the query planner can reach the wall clock: "
        + "; ".join(sorted(set(offenders)))
        + ". `today` is an explicit argument — see this module's docstring."
    )
