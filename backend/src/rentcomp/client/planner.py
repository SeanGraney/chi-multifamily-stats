"""The query plan (F4-S1) — which windows a pull asks for, and how many calls it costs.

Spec §3.2 is the whole of this module, and it is **[INVARIANT]**: the date-window
algorithm *is* the definition of which comps get pulled, so there is no
"equivalent computation" to substitute for it.

    windowStart_y = (windowStartMonthDay in year: currentYear - y)
    windowEnd_y   = (windowEndMonthDay   in year: currentYear - y)
    daysOldMin    = today - windowEnd_y   - PAD   (floor at 1)
    daysOldMax    = today - windowStart_y + PAD

`PAD = 90` on both sides is not slack for rounding — RentCast **resets
`listedDate` on re-list**, so a stitched listing whose true start falls inside
the window can present a latest-re-list date well outside it. The pad buys those
records back; F4-S4 then filters on the *stitched* start month-day, which is the
only place the window is enforced for real. That division of labour is why a
one-day boundary shift here (see the leap-day clamp below) cannot change which
comps end up in a cohort.

WHAT THIS MODULE OWNS, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------------
Owns: resolving month-day window bounds into real dates for each cohort year,
the `daysOld` arithmetic, and the two-queries-per-year (Active + Inactive)
emission.

Does NOT own, on purpose:

* **Pagination.** `RentCastClient.fetch_listings` (F0-S4) already owns
  `offset` / `X-Total-Count` / short-page detection. This module never sets
  `offset`, so every planned query hashes to the same fixture signature its
  page 0 would.
* **Wire syntax.** `client/query.py` is the single source of the colon/pipe
  rules that eight paid-for live calls bought; `params` here is built by
  calling it, never by formatting a string. Hand-rolling would re-key
  `fixture_signature` and orphan every committed fixture.
* **The clock.** `today` is passed in, always. Time is data here, not ambient
  state (the F0-S2 "as_of is data, never the wall clock" precedent) — a planner
  that read `date.today()` would be untestable at exactly the boundaries the AC
  cares about, and would make a cached plan depend on when it was replayed.

TWO READINGS THE SPEC TEXT LEAVES OPEN (both confirmed by the PM at dispatch)
-----------------------------------------------------------------------------
1. **A window that spans New Year wraps.** Read completely literally, the
   formula puts *both* bounds in `currentYear - y`, which makes a "12-15" ->
   "01-15" window end chronologically before it starts — not an interval at
   all, and the story's own AC demands a Dec-Jan case. So when the end
   month-day sorts before the start month-day, the end resolves into
   `(currentYear - y) + 1`. The cohort `year` label always follows the window's
   **START**, matching F4-S4's "cohort = calendar year of the stitched start".
2. **Feb 29 in a non-leap cohort year clamps to Feb 28**, never raises. A pull
   spanning five years back must not blow up because one of them is not a leap
   year, and per the PAD reasoning above the one-day shift is immaterial to
   which records come back.

STRUCTURALLY EMPTY WINDOWS — THE ONE THING THAT COSTS REAL MONEY
-----------------------------------------------------------------
The formula floors `daysOldMin` at 1 but never floors `daysOldMax`, so a cohort
whose window has not opened yet produces `daysOldMax < daysOldMin`
(e.g. `daysOld=1:-107` for cohort 2027 on 2027-06-01 with a 12-15 -> 01-15
window). `rentcast_range` performs no min<=max validation, so that string would
go on the wire — and a listing cannot have been listed in the future, so the
query returns zero *by construction*, not merely "probably sparse".

Such a query is therefore marked `is_structurally_empty`, and
`fetchable_queries` is the one filter both consumers use: the pull must not send
it (1 of 50 monthly calls spent to guarantee nothing) and F2-S3's "est. N API
calls" must not count it (a wrong number shown to the user before they consent
to spend). The cohort is still *emitted* — F2-S3 shows the user which cohorts
were planned, and "silently absent" and "planned but empty" are different
things to a user deciding whether their date window is what they meant.
"""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from rentcomp.client.query import build_listing_params, rentcast_range

__all__ = [
    "PAD_DAYS",
    "STATUSES",
    "PlannedQuery",
    "fetchable_queries",
    "plan_pull_queries",
]

#: Spec §3.2. Both sides, because RentCast resets `listedDate` on re-list.
PAD_DAYS = 90

#: `status` is single-valued on `/listings/rental/long-term`, so one cohort year
#: is two calls. Order is the story prose's own: Active, then Inactive.
STATUSES = ("Active", "Inactive")

#: `daysOld` is "days since listed"; 0 or less is not a real value on the wire.
MIN_DAYS_OLD = 1


@dataclass(frozen=True)
class PlannedQuery:
    """One `/listings/rental/long-term` query: one (cohort year x status) pair.

    `days_old_min` is floored at 1; `days_old_max` is not floored, so it can be
    zero or negative for a window that has not opened yet. That is the honest
    output of the formula, and `is_structurally_empty` is how a caller notices
    rather than paying to find out.
    """

    year: int
    status: str
    window_start: date
    window_end: date
    days_old_min: int
    days_old_max: int
    params: dict

    @property
    def is_structurally_empty(self) -> bool:
        """True when this query cannot match a record, whatever the market holds.

        `days_old_max < days_old_min` (post-floor) means the window lies wholly
        in the future once the pad is applied — and nothing can have been listed
        in the future. Not a heuristic about thin data: a guaranteed empty
        result, and therefore a call that must never be sent or estimated.
        """
        return self.days_old_max < self.days_old_min


def fetchable_queries(queries: Iterable[PlannedQuery]) -> list[PlannedQuery]:
    """The subset worth spending a call on — structurally empty windows dropped.

    Deliberately the *only* place that filter is expressed, so that the pull and
    F2-S3's call estimate cannot disagree about what a pull costs (F2-S3 is
    [INVARIANT] on exactly that: one function, two consumers, no drift).
    """
    return [q for q in queries if not q.is_structurally_empty]


def plan_pull_queries(
    today: date,
    *,
    years_back: int,
    window_start_mmdd: str,
    window_end_mmdd: str,
    address: str,
    radius: float,
    bedrooms: str,
    bathrooms: str | None = None,
    property_types: Iterable[str],
) -> list[PlannedQuery]:
    """Spec §3.2's plan for one pull: `2 * years_back` queries.

    Pure in `(params, today)` — no clock read, no I/O, no module state. Cohort
    years run `today.year - y` for y in 0..years_back-1, each contributing an
    Active query then an Inactive one.

    Args:
        today: the "now" the windows are measured against. Data, not the clock.
        years_back: how many cohort years to plan (F2's 1-5).
        window_start_mmdd / window_end_mmdd: `"MM-DD"`. An end that sorts before
            the start means the window spans New Year and wraps into the next
            calendar year; the cohort label follows the start.
        address, radius, bedrooms, bathrooms, property_types: passed straight
            through to `build_listing_params`. `bathrooms=None` means no
            `bathrooms` key at all on the wire — an empty filter would make the
            server exclude records with a missing bathroom count.
    """
    if years_back < 1:
        raise ValueError(f"years_back must be at least 1, got {years_back}")

    start_month_day = _parse_mmdd(window_start_mmdd, field="window_start_mmdd")
    end_month_day = _parse_mmdd(window_end_mmdd, field="window_end_mmdd")
    # Compared as (month, day) rather than as resolved dates: whether a window
    # wraps is a property of the window itself, identical for every cohort year.
    wraps = end_month_day < start_month_day
    property_types = list(property_types)

    queries: list[PlannedQuery] = []
    for y in range(years_back):
        year = today.year - y
        window_start = _resolve(start_month_day, year)
        window_end = _resolve(end_month_day, year + 1 if wraps else year)

        days_old_min = max(MIN_DAYS_OLD, (today - window_end).days - PAD_DAYS)
        days_old_max = (today - window_start).days + PAD_DAYS
        days_old = rentcast_range(days_old_min, days_old_max)

        for status in STATUSES:
            queries.append(
                PlannedQuery(
                    year=year,
                    status=status,
                    window_start=window_start,
                    window_end=window_end,
                    days_old_min=days_old_min,
                    days_old_max=days_old_max,
                    params=build_listing_params(
                        address=address,
                        radius=radius,
                        bedrooms=bedrooms,
                        bathrooms=bathrooms,
                        property_types=property_types,
                        status=status,
                        days_old=days_old,
                    ),
                )
            )
    return queries


# ---------------------------------------------------------------------------
# month-day resolution
# ---------------------------------------------------------------------------


def _parse_mmdd(value: str, *, field: str) -> tuple[int, int]:
    """`"MM-DD"` -> `(month, day)`, validated against a LEAP year.

    Leap year on purpose: `02-29` is a legitimate window boundary (the AC names
    it), so it must survive parsing and be clamped later per cohort year, not
    rejected here. `02-30` and `04-31` are still refused — they are not a date
    in any year, and quietly clamping a typo would move a window the user never
    asked to move.
    """
    parts = str(value).split("-")
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError(f"{field} must be 'MM-DD', got {value!r}")
    month, day = int(parts[0]), int(parts[1])
    try:
        date(2000, month, day)  # 2000 is a leap year
    except ValueError as exc:
        raise ValueError(f"{field}={value!r} is not a valid month-day: {exc}") from exc
    return month, day


def _resolve(month_day: tuple[int, int], year: int) -> date:
    """A `(month, day)` placed in `year`, clamping Feb 29 to Feb 28 where the
    year is not a leap year.

    Clamp rather than raise (a 5-year pull must not fail on the one non-leap
    year in it) and rather than roll forward to Mar 1 (never widen a window the
    user specified). Immaterial to results either way: this module only builds a
    +/-90-day-padded range, and F4-S4 does the real month-day filtering against
    the stitched start.
    """
    month, day = month_day
    return date(year, month, min(day, monthrange(year, month)[1]))
