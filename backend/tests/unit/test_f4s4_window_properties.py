"""F4-S4 [INVARIANT] — window/cohort property tests (`hypothesis`).
QA-authored, written before the developer starts (AGENT_QA.md protocol).

Layer 1 throughout, for the same reason as `test_f4s4_window_cohort.py`:
`shape_raw_pull` takes the window bounds and `as_of` as plain arguments and
returns the comps, so every claim below is "call a function, look at the
result". CLAUDE.md names `hypothesis` property tests as the tool for exactly
this kind of pipeline invariant, and the dispatch asks for two of them by
name — "a record's cohort is always the calendar year of its stitched start"
and "window membership is invariant under the record's raw listed date".

WHAT A PROPERTY BUYS HERE THAT A PARAMETRIZED CASE DOES NOT
-----------------------------------------------------------
The parametrized file next door proves the rule at chosen days. These
properties state it as a **biconditional over generated inputs**, so a filter
cannot pass by being right on the four days someone thought to check. The two
that matter most are the invariance properties: they hold the stitched start
fixed and vary everything a wrong implementation might read instead — the
re-list dates, the number of re-lists, the calendar year — and assert that
neither the keep/drop decision nor the cohort moves.

Each generated chain uses gaps strictly under 42 days so it stitches into one
chain, and a single address so it is one group. That the gaps merge is F4-S3's
rule, asserted in F4-S3's own file; here it is a precondition, and every test
that relies on it checks the precondition explicitly rather than assuming it.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

#: Comfortably under `Config().stitch_gap_days` (42), so every generated chain
#: is one chain. Not an assertion about the threshold — see the module
#: docstring — just a precondition this file keeps well clear of.
_MAX_GAP = 30

_ADDRESS = "700 Property Ave"


def _record(id_: str, listed: date, removed: date | None) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{_ADDRESS}, Chicago, IL 60609",
        "addressLine1": _ADDRESS,
        "addressLine2": "Unit 1",
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
        "price": 2000.0,
        "listingType": "Standard",
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "removedDate": f"{removed.isoformat()}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed.isoformat()}T00:00:00.000Z",
        "lastSeenDate": f"{(removed or listed).isoformat()}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }


def _shape_chain(segments: list[tuple[date, date]], window: tuple[str, str]):
    """One unit's spells -> the comps that survive `window`. `as_of` is set well
    past the chain so nothing here depends on the removal ladder (F4-S8)."""
    records = [_record(f"p{i}", listed, removed) for i, (listed, removed) in enumerate(segments)]
    as_of = max(removed for _, removed in segments) + timedelta(days=200)
    return shape_raw_pull([], records, Config(), as_of, *window)


def _mmdd(day: date) -> str:
    return f"{day.month:02d}-{day.day:02d}"


def _inside(day: date, start_mmdd: str, end_mmdd: str) -> bool:
    """The oracle, written once. A closed month-day interval that wraps when
    its end sorts before its start — the two readings pinned (and justified)
    in `test_f4s4_window_cohort.py`'s module docstring."""
    key = _mmdd(day)
    if start_mmdd <= end_mmdd:
        return start_mmdd <= key <= end_mmdd
    return key >= start_mmdd or key <= end_mmdd


#: Any real calendar day over a span wide enough to include leap years and to
#: exercise both a wrapping window's arms.
_days = st.dates(min_value=date(2020, 1, 1), max_value=date(2026, 12, 31))
_mmdds = st.dates(min_value=date(2024, 1, 1), max_value=date(2024, 12, 31)).map(_mmdd)


@st.composite
def _chains(draw, *, start: date | None = None, min_spells: int = 1, max_spells: int = 5):
    """A stitchable run of spells for one unit, described by its start.

    Returns `(segments, start)`. Every gap is in `[0, 30]` and every spell has a
    real removal, so the run is one chain and its stitched start is `start`.
    """
    first = start if start is not None else draw(_days)
    n = draw(st.integers(min_value=min_spells, max_value=max_spells))
    doms = draw(st.lists(st.integers(min_value=0, max_value=120), min_size=n, max_size=n))
    gaps = draw(st.lists(st.integers(min_value=0, max_value=_MAX_GAP), min_size=max(n - 1, 0), max_size=max(n - 1, 0)))

    segments: list[tuple[date, date]] = []
    listed = first
    for i in range(n):
        if i:
            listed = segments[-1][1] + timedelta(days=gaps[i - 1])
            # two spells sharing a listed date are one spell (F4-S2), which
            # would change the chain's shape rather than its start
            assume(listed > segments[-1][0])
        segments.append((listed, listed + timedelta(days=doms[i])))
    return segments, first


# ---------------------------------------------------------------------------
# the two properties the dispatch names
# ---------------------------------------------------------------------------


@given(spec=_chains(), start_mmdd=_mmdds, end_mmdd=_mmdds)
@settings(deadline=None, max_examples=400)
def test_a_kept_records_cohort_is_always_the_calendar_year_of_its_stitched_start(
    spec, start_mmdd, end_mmdd
) -> None:
    """The story's second clause, as a property over every window and every
    chain shape.

    `cohort_year` is not a label: F8-S1 raises `(1 + drift)` to the power
    `currentYear - cohortYear`, so a cohort off by one silently applies a full
    extra year of assumed market movement to that comp's $/sqft, and F4-S5
    measures its premium against a different year's median. Taking the year
    from the re-list — the raw record's own `listedDate` — is the mistake this
    property exists to make impossible.
    """
    segments, first = spec
    comps = _shape_chain(segments, (start_mmdd, end_mmdd))
    assume(len(comps) == 1)  # precondition: the run stitched into one chain

    comp = comps[0]
    assert comp.first_listed == first, (
        f"stitched start is {comp.first_listed}, expected {first} — precondition broken, this "
        "chain did not stitch as intended"
    )
    assert comp.cohort_year == first.year, (
        f"cohort_year={comp.cohort_year} for a chain whose stitched start is {first}; "
        f"{segments[-1][0].year} would be the year of its last re-list ({segments[-1][0]})"
    )


@given(
    start=_days,
    dom=st.integers(min_value=0, max_value=120),
    tail=st.lists(st.tuples(st.integers(min_value=0, max_value=_MAX_GAP), st.integers(min_value=0, max_value=120)), min_size=1, max_size=4),
    start_mmdd=_mmdds,
    end_mmdd=_mmdds,
)
@settings(deadline=None, max_examples=400)
def test_window_membership_does_not_move_when_the_raw_listed_date_does(
    start, dom, tail, start_mmdd, end_mmdd
) -> None:
    """The AC's "iff", as an invariance property — the dispatch's second named
    property, and the one that kills a filter reading the raw `listedDate`.

    The same unit is shaped twice: once as a single spell starting on `start`,
    and once as that spell plus one to four re-lists appended to it. RentCast
    resets `listedDate` on re-list, so the second record's raw date is the last
    re-list's, arbitrarily far from `start`. The chain's stitched start is
    `start` in both cases, so the keep/drop decision and the cohort must be
    identical in both cases.
    """
    window = (start_mmdd, end_mmdd)
    plain = _shape_chain([(start, start + timedelta(days=dom))], window)

    segments = [(start, start + timedelta(days=dom))]
    for gap, next_dom in tail:
        listed = segments[-1][1] + timedelta(days=gap)
        assume(listed > segments[-1][0])
        segments.append((listed, listed + timedelta(days=next_dom)))
    relisted = _shape_chain(segments, window)

    assume(len(relisted) <= 1)  # precondition: it stitched into one chain

    assert len(plain) == len(relisted), (
        f"a unit starting {start} was {'kept' if plain else 'dropped'} as a single spell but "
        f"{'kept' if relisted else 'dropped'} once re-listed through {segments[-1][0]}, under "
        f"window {start_mmdd}..{end_mmdd}. Membership must depend on the stitched start alone."
    )
    if plain and relisted:
        assert plain[0].cohort_year == relisted[0].cohort_year == start.year, (
            f"cohort moved from {plain[0].cohort_year} to {relisted[0].cohort_year} when re-lists "
            f"were appended; the stitched start is {start} in both"
        )


# ---------------------------------------------------------------------------
# the filter as a biconditional, and year-agnosticism
# ---------------------------------------------------------------------------


@given(spec=_chains(), start_mmdd=_mmdds, end_mmdd=_mmdds)
@settings(deadline=None, max_examples=600)
def test_a_record_is_kept_exactly_when_its_stitched_starts_month_day_is_in_the_window(
    spec, start_mmdd, end_mmdd
) -> None:
    """The first clause of the story, whole: keep **iff** the stitched start's
    month-day is inside the year-agnostic window.

    Stated as an `is` against an independently-written oracle over generated
    windows and dates, so neither edge can be off by one and neither arm of a
    wrapping window can be dropped without a counterexample appearing.
    """
    segments, first = spec
    comps = _shape_chain(segments, (start_mmdd, end_mmdd))
    assume(len(comps) <= 1)  # precondition: the run stitched into one chain

    kept = len(comps) == 1
    expected = _inside(first, start_mmdd, end_mmdd)
    assert kept is expected, (
        f"stitched start {first} ({_mmdd(first)}) with window {start_mmdd}..{end_mmdd}: "
        f"{'kept' if kept else 'dropped'}, expected {'kept' if expected else 'dropped'}"
        + ("  [this window wraps the year end]" if start_mmdd > end_mmdd else "")
    )


@given(
    month_day=st.dates(min_value=date(2024, 1, 1), max_value=date(2024, 12, 31)),
    years=st.lists(st.integers(min_value=2016, max_value=2026), min_size=2, max_size=6, unique=True),
    start_mmdd=_mmdds,
    end_mmdd=_mmdds,
)
@settings(deadline=None, max_examples=300)
def test_the_window_is_year_agnostic(month_day, years, start_mmdd, end_mmdd) -> None:
    """"Year-agnostic" restated: one month-day, placed in several different
    calendar years, is kept in all of them or none of them.

    The failure mode is an implementation that compares day-of-year ordinals,
    or that resolves the window into concrete dates in one reference year:
    both make membership depend on whether the record's year is a leap year,
    which would put a year-shaped hole in a cohort for no visible reason.
    """
    window = (start_mmdd, end_mmdd)
    outcomes = {}
    for year in years:
        try:
            day = date(year, month_day.month, month_day.day)
        except ValueError:  # Feb 29 in a non-leap year is not a day at all
            continue
        outcomes[year] = bool(_shape_chain([(day, day + timedelta(days=5))], window))

    assume(len(outcomes) >= 2)
    assert len(set(outcomes.values())) == 1, (
        f"month-day {_mmdd(month_day)} against window {start_mmdd}..{end_mmdd} is kept in some "
        f"years and not others: {outcomes}"
    )


@given(spec=_chains(), start_mmdd=_mmdds, end_mmdd=_mmdds)
@settings(deadline=None, max_examples=200)
def test_shaping_the_same_pull_twice_gives_the_same_window_and_cohort_decisions(
    spec, start_mmdd, end_mmdd
) -> None:
    """Group A is memoized once per (pull, config, as_of) — a decision that is
    not a pure function of those would be frozen by the memo at whatever value
    it happened to take first. Cheap to state, and it is the precondition every
    other property in this file leans on."""
    segments, _ = spec
    window = (start_mmdd, end_mmdd)
    first = _shape_chain(segments, window)
    second = _shape_chain(segments, window)
    assert [(c.first_listed, c.cohort_year) for c in first] == [(c.first_listed, c.cohort_year) for c in second]
