"""F4-S3 [INVARIANT] — stitcher property tests (`hypothesis`). QA-authored,
written RED before the developer starts (AGENT_QA.md protocol).

Layer 1 throughout. ARCHITECTURE.md §8 and CLAUDE.md both name the stitcher
as *the* canonical property-test case in this codebase; the story's AC is
already phrased as four properties, so the layer decision is not close.

THE ONE SEAM THIS STORY MUST EXPOSE (pickup 3 in the PM's dispatch)
-------------------------------------------------------------------
    rentcomp.pipeline.shape.stitch(
        spells: Sequence[Spell],
        stitch_gap_days: int,
    ) -> Sequence[Sequence[Spell]]      # maximal chains, in listed order

Three requirements on it, and nothing else:

1. **The threshold is a plain `int`, not a `Config`.** `Config.stitch_gap_days`
   is `ge=7, le=60`, so the story's own first acceptance criterion —
   "threshold 0 => no merging" — is *unreachable* through `Config`. A seam
   that takes a `Config` cannot express AC1 at all.
2. **It returns the chains**, not `StitchedComp`s: AC1 and AC2 are claims
   about how spells group, and asserting them through `effective_dom` would
   make them depend on the DOM rule they are independent of.
3. **`shape_raw_pull` keeps calling it**, so the seam is the real code path
   and not a second implementation that can drift.

`shape.py`'s own docstring records that this seam was deferred: "F4-S3's own
`stitch` seam is its story's to add, not this one's." This is that story.

WHAT IS DELIBERATELY *NOT* PINNED
---------------------------------
Everything below the seam. This file never constructs a `Spell` directly —
it builds raw RentCast dicts and runs them through `extract_spells` (F4-S2's
shipped seam) — so `Spell` moving to `models/domain.py` (pickup 2), changing
from a dataclass to a `BaseModel`, or gaining fields breaks nothing here.
Chains are read positionally and by attribute, never by concrete type.

THE READING OF "GAP" THIS FILE PINS, AND WHY
--------------------------------------------
The story states the merge rule twice, and the two statements only agree
under one reading:

    "merge consecutive spells where gap = nextListed - prevRemoved < threshold"
    "Off market >= threshold => prior spell is complete"

Taken as raw subtraction, `gap < threshold` is **also true for every negative
gap**, so at `threshold = 0` two *overlapping* spells still merge — which
contradicts the story's own AC1 ("threshold 0 => no merging"). The second
sentence resolves it: what the threshold measures is **days off market**, and
a unit whose next listing began before the previous one ended was never off
market at all. So

    days_off_market = max(0, nextListed - latest removal observed so far)
    merge  <=>  days_off_market < threshold

and every clause lines up: threshold 0 merges nothing (0 is never < 0);
gap 41 merges and gap 42 does not at the default; contiguous price-change
segments (gap 0) merge, which the F4-S7 report says they must; and
overlapping observations of one unit merge, which is what stops F4-S2's four
de-duplicated units from being double-counted again.

Measured over the committed pull, the three candidate rules give:

    rule                                       threshold 0    threshold 42
    `gap < t`                (today)              618 chains     567 chains
    `0 <= gap < t`           (naive repair)       624 chains     573 chains
    `max(0, gap) < t`        (this file)          624 chains     567 chains

Today's rule merges 6 real spell pairs at threshold 0 — AC1 is violated on
real data, not just in principle. The naive repair fixes AC1 and breaks
something worse: it splits the 6 overlapping observations into extra comps,
re-introducing exactly the double-counting F4-S2 removed. Only the third
rule satisfies AC1 *and* leaves the evidence base at the 567 comps F4-S2
established.

This is a reading of the invariant, not a change to it — flagged to the PM in
the handoff rather than decided here.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.pipeline.shape import extract_spells

try:  # the seam this story is asked to add
    from rentcomp.pipeline.shape import stitch

    _SEAM_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - the red state before F4-S3 lands
    _SEAM_ERROR = exc

    def stitch(*_args, **_kwargs):  # type: ignore[misc]
        raise AssertionError("unreachable — every user is gated behind `needs_seam`")


needs_seam = pytest.mark.skipif(
    _SEAM_ERROR is not None,
    reason=f"rentcomp.pipeline.shape.stitch not importable yet: {_SEAM_ERROR}",
)

DEFAULT_THRESHOLD = 42


def test_the_stitch_seam_exists() -> None:
    """The one loud, ungated failure that names what is missing — every
    property below skips legibly behind it, so the red state stays readable
    (AGENT_QA.md's commit rule: a collection error hides everything, a named
    failure plus N skips does not)."""
    assert _SEAM_ERROR is None, (
        "F4-S3 needs `rentcomp.pipeline.shape.stitch(spells, stitch_gap_days: int)` exported so "
        "its acceptance criteria can be asserted at the gap boundary directly. `Config."
        "stitch_gap_days` is constrained to 7..60, so AC1 ('threshold 0 => no merging') cannot "
        f"be reached through `shape_raw_pull` at all — see this module's docstring: {_SEAM_ERROR}"
    )


# ---------------------------------------------------------------------------
# input construction — raw dicts through F4-S2's shipped `extract_spells`, so
# nothing here depends on `Spell`'s concrete shape or its module
# ---------------------------------------------------------------------------

_ADDRESS = "900 W Property Ave"
_UNIT = "Unit 1"


def _record(id_: str, listed: date, removed: date | None, price: float) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{_ADDRESS}, {_UNIT}, Chicago, IL 60609",
        "addressLine1": _ADDRESS,
        "addressLine2": _UNIT,
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


def _spells(segments: list[tuple[date, date | None, float]]):
    """`[(listed, removed, price), ...]` -> the `Spell` rows F4-S3 consumes.

    Routed through `extract_spells` rather than constructing `Spell`s, so this
    file states no opinion about `Spell`'s fields, type or module."""
    records = [_record(f"p{i}", listed, removed, price) for i, (listed, removed, price) in enumerate(segments)]
    spells = extract_spells(records)
    assert len(spells) == len(segments), (
        "test-input defect, not a product failure: two segments collapsed in `extract_spells` "
        "because they share a `listedDate`"
    )
    return spells


def _chains(spells, threshold: int) -> list[list]:
    return [list(chain) for chain in stitch(spells, threshold)]


_START = date(2024, 1, 1)
_price = st.integers(min_value=500, max_value=9000).map(float)


@st.composite
def _segment_specs(draw, *, min_spells: int, max_spells: int, min_gap: int, max_gap: int, open_last: bool):
    """A chronological run of spells, described as (dom, gap-before) pairs.

    `gap` is the literal `nextListed - prevRemoved` the story names, so a
    negative value is a genuine overlap. Clamped only enough to keep
    `listedDate` strictly increasing — two spells sharing a start date are
    one spell (F4-S2's rule), not this story's case."""
    n = draw(st.integers(min_value=min_spells, max_value=max_spells))
    doms = draw(st.lists(st.integers(min_value=0, max_value=400), min_size=n, max_size=n))
    raw_gaps = draw(
        st.lists(st.integers(min_value=min_gap, max_value=max_gap), min_size=n - 1, max_size=n - 1)
    )
    prices = draw(st.lists(_price, min_size=n, max_size=n))

    segments: list[tuple[date, date | None, float]] = []
    gaps: list[int] = []
    listed = _START
    for i in range(n):
        if i:
            gap = max(raw_gaps[i - 1], 1 - doms[i - 1])  # keep `listed` strictly increasing
            gaps.append(gap)
            listed = segments[-1][1] + timedelta(days=gap)  # type: ignore[operator]
        removed = None if (open_last and i == n - 1) else listed + timedelta(days=doms[i])
        segments.append((listed, removed, prices[i]))
    return segments, gaps


# ---------------------------------------------------------------------------
# structural invariants of `stitch` — the preconditions every AC below leans on
# ---------------------------------------------------------------------------


@needs_seam
def test_stitching_nothing_yields_nothing() -> None:
    """An empty group is not an error. `shape_raw_pull` guards this today; the
    seam must not make an empty sequence a crash for anyone else."""
    assert list(stitch((), DEFAULT_THRESHOLD)) == []


@needs_seam
@given(spec=_segment_specs(min_spells=1, max_spells=6, min_gap=-400, max_gap=400, open_last=False),
       threshold=st.integers(min_value=0, max_value=120))
@settings(deadline=None, max_examples=300)
def test_chains_partition_the_spells_in_order_losing_and_duplicating_nothing(spec, threshold) -> None:
    """Stitching re-groups evidence; it never creates or destroys any.

    Not in the AC, and the most important test in this file: every DOM
    assertion below is meaningless if a merge rule can quietly drop a spell.
    A stitcher that lost a spell would make DOM *shorter* and a re-list badge
    *absent* — both of which look like clean data."""
    segments, _ = spec
    spells = _spells(segments)
    chains = _chains(spells, threshold)

    flattened = [spell for chain in chains for spell in chain]
    assert flattened == list(spells), (
        f"stitch({threshold}) did not partition its input in order:\n"
        f"  in : {[(s.listed, s.removed) for s in spells]}\n"
        f"  out: {[[(s.listed, s.removed) for s in c] for c in chains]}"
    )
    assert all(chain for chain in chains), "stitch produced an empty chain"


@needs_seam
@given(spec=_segment_specs(min_spells=1, max_spells=5, min_gap=-400, max_gap=400, open_last=False),
       threshold=st.integers(min_value=0, max_value=120))
@settings(deadline=None)
def test_stitching_is_deterministic(spec, threshold) -> None:
    segments, _ = spec
    spells = _spells(segments)
    assert _chains(spells, threshold) == _chains(spells, threshold)


# ---------------------------------------------------------------------------
# AC1 — "threshold 0 => no merging"        (and known bug 1)
# ---------------------------------------------------------------------------


@needs_seam
@given(spec=_segment_specs(min_spells=1, max_spells=6, min_gap=-400, max_gap=400, open_last=False))
@settings(deadline=None, max_examples=300)
def test_at_threshold_zero_nothing_merges_however_the_spells_are_spaced(spec) -> None:
    """F4-S3 AC1, verbatim — and the pin for known bug 1.

    The generator deliberately produces **negative** gaps as well as positive
    ones. Today's condition is a raw `gap < threshold`, which is true for
    every negative gap at every threshold including 0, so overlapping spells
    merge even when the caller asked for no merging at all. On the committed
    pull that is 6 real spell pairs across 4 units.

    "No merging" is not a special case of the threshold rule — it *is* the
    rule, read as days-off-market: a chain continues only while the unit was
    off market for strictly fewer than `threshold` days, and no amount of
    off-market time is strictly less than zero."""
    segments, _ = spec
    spells = _spells(segments)
    chains = _chains(spells, 0)
    assert [len(chain) for chain in chains] == [1] * len(spells), (
        "threshold 0 merged spells that overlap:\n"
        f"  spells: {[(s.listed, s.removed) for s in spells]}\n"
        f"  chains: {[[(s.listed, s.removed) for s in c] for c in chains]}"
    )


@needs_seam
@pytest.mark.parametrize(
    ("first", "second", "gap"),
    [
        # every consecutive negative gap in the committed pull, verbatim
        ((date(2025, 9, 11), date(2025, 10, 22)), (date(2025, 10, 15), date(2026, 1, 17)), -7),
        ((date(2025, 10, 15), date(2026, 1, 17)), (date(2025, 10, 22), date(2025, 12, 11)), -87),
        ((date(2026, 2, 2), date(2026, 3, 19)), (date(2026, 2, 7), date(2026, 3, 1)), -40),
        ((date(2026, 1, 21), date(2026, 5, 20)), (date(2026, 1, 27), date(2026, 3, 15)), -113),
        ((date(2025, 6, 10), date(2025, 6, 25)), (date(2025, 6, 12), date(2025, 6, 20)), -13),
        ((date(2025, 12, 11), date(2026, 1, 26)), (date(2026, 1, 12), date(2026, 2, 1)), -14),
    ],
)
def test_the_real_overlapping_spell_pairs_do_not_merge_at_threshold_zero(first, second, gap) -> None:
    """Known bug 1, on the data it is actually reachable on.

    These six pairs are the real overlaps in the committed 539-record pull —
    the F4-S2 duplicate units (`2350 S Leavitt St 1R`, `2415 W 24th St 6`,
    `2453 W 46th Pl 1`, `2835 W 38th Pl 2`, `3643 S Hamilton Ave 3`), where
    two listing ids report intervals that overlap. The property above proves
    the rule; this proves the rule bites where the money is."""
    spells = _spells([(first[0], first[1], 2000.0), (second[0], second[1], 2000.0)])
    assert (second[0] - first[1]).days == gap, "oracle drift: this pair is no longer an overlap"
    assert len(_chains(spells, 0)) == 2, (
        f"an overlapping spell pair (gap {gap}d) merged at threshold 0 — the merge condition "
        "is comparing a raw signed gap instead of days off market"
    )


@needs_seam
@given(
    dom=st.integers(min_value=2, max_value=400),
    overlap=st.integers(min_value=1, max_value=399),
    second_dom=st.integers(min_value=0, max_value=400),
    threshold=st.integers(min_value=1, max_value=120),
)
@settings(deadline=None, max_examples=300)
def test_overlapping_spells_merge_at_every_threshold_above_zero(
    dom, overlap, second_dom, threshold
) -> None:
    """The other half of the same rule, and the reason the naive repair
    (`0 <= gap < threshold`) is wrong.

    A unit whose next listing began before the previous one ended was never
    off market, so its days-off-market is 0 — below every positive threshold.
    Splitting these apart would turn F4-S2's four de-duplicated units back
    into eight comps: the same vacancy counted twice in the cohort median
    behind `premium`, twice in every bucket, twice in the KM risk set. On the
    committed pull this is 6 real spell pairs across 5 units."""
    overlap = min(overlap, dom - 1)  # keep `listed` strictly increasing
    listed = _START
    removed = listed + timedelta(days=dom)
    relisted = removed - timedelta(days=overlap)
    spells = _spells(
        [(listed, removed, 2000.0), (relisted, relisted + timedelta(days=second_dom), 1950.0)]
    )
    assert (relisted - removed).days < 0, "oracle drift: these spells no longer overlap"
    chains = _chains(spells, threshold)
    assert len(chains) == 1, (
        f"two spells overlapping by {overlap}d were split into separate comps at threshold "
        f"{threshold} — the same vacancy is now counted twice"
    )


# ---------------------------------------------------------------------------
# AC2 — "gap = threshold-1 merges, gap = threshold does not"
# ---------------------------------------------------------------------------


@needs_seam
@given(threshold=st.integers(min_value=1, max_value=120), dom=st.integers(min_value=1, max_value=400))
@settings(deadline=None, max_examples=200)
def test_a_gap_one_day_under_the_threshold_merges(threshold, dom) -> None:
    """F4-S3 AC2, first half. Stated over every threshold rather than the
    42-day default, because the default is a `[DEFAULT]` and the boundary is
    the `[INVARIANT]`."""
    listed = _START
    removed = listed + timedelta(days=dom)
    spells = _spells(
        [(listed, removed, 2000.0), (removed + timedelta(days=threshold - 1), None, 1950.0)]
    )
    assert len(_chains(spells, threshold)) == 1, (
        f"a gap of {threshold - 1}d did not merge at threshold {threshold} — the boundary "
        "comparison appears to be `<=`/`<` inverted or off by one"
    )


@needs_seam
@given(threshold=st.integers(min_value=0, max_value=120), dom=st.integers(min_value=1, max_value=400))
@settings(deadline=None, max_examples=200)
def test_a_gap_exactly_at_the_threshold_does_not_merge(threshold, dom) -> None:
    """F4-S3 AC2, second half: "Off market >= threshold => prior spell is
    **complete**." A complete spell is a lease that we will count as an
    observed outcome, so merging one day too eagerly turns two real leases
    into one long fake vacancy."""
    listed = _START
    removed = listed + timedelta(days=dom)
    spells = _spells([(listed, removed, 2000.0), (removed + timedelta(days=threshold), None, 1950.0)])
    assert len(_chains(spells, threshold)) == 2, (
        f"a gap of exactly {threshold}d merged at threshold {threshold} — the boundary "
        "comparison is `<=` where the story says the prior spell is already complete"
    )


@needs_seam
@given(
    threshold=st.integers(min_value=1, max_value=120),
    gap=st.integers(min_value=0, max_value=400),
    dom=st.integers(min_value=1, max_value=400),
)
@settings(deadline=None, max_examples=400)
def test_two_spells_merge_exactly_when_their_off_market_days_are_under_the_threshold(
    threshold, gap, dom
) -> None:
    """AC2 as one biconditional over the whole non-negative range, so the
    boundary cannot be satisfied by two hand-picked values on either side of
    it. This is the assertion the two tests above are the readable form of."""
    listed = _START
    removed = listed + timedelta(days=dom)
    spells = _spells([(listed, removed, 2000.0), (removed + timedelta(days=gap), None, 1950.0)])
    merged = len(_chains(spells, threshold)) == 1
    assert merged is (gap < threshold), (
        f"gap {gap}d at threshold {threshold}d: {'merged' if merged else 'split'}, expected "
        f"{'merged' if gap < threshold else 'split'}"
    )


# ---------------------------------------------------------------------------
# AC3 / AC4 / known bug 2 — the merged record's DOM and censoring
#
# Asserted through `shape_raw_pull` rather than through a second seam: the
# chain-to-`StitchedComp` step needs an `as_of` and a `Config`, both of which
# `shape_raw_pull` already takes, and every input below is built to shape into
# exactly one comp. Asking for a `build_comp` export as well would pin more of
# `shape.py`'s internal structure than the ACs require.
# ---------------------------------------------------------------------------


def _one_comp(segments, as_of: date):
    from rentcomp.pipeline.shape import shape_raw_pull
    from rentcomp.storage.config import Config

    records = [_record(f"p{i}", listed, removed, price) for i, (listed, removed, price) in enumerate(segments)]
    active = [r for r in records if r["removedDate"] is None]
    inactive = [r for r in records if r["removedDate"] is not None]
    comps = shape_raw_pull(active, inactive, Config(), as_of, "01-01", "12-31")
    assert len(comps) == 1, (
        f"test-input defect or a merge failure: expected one chain, got {len(comps)} comps for "
        f"{[(s[0], s[1]) for s in segments]}"
    )
    return comps[0]


@given(spec=_segment_specs(min_spells=1, max_spells=5, min_gap=0, max_gap=41, open_last=False))
@settings(deadline=None, max_examples=300)
def test_a_merged_chains_dom_is_at_least_the_sum_of_its_spells_doms(spec) -> None:
    """F4-S3 AC3, and the whole point of the story: **gap days count**.

    A unit that was listed, pulled, and re-listed a fortnight later was vacant
    for that fortnight too. Summing the reported per-spell DOMs is exactly the
    laundering this stage exists to undo — RentCast's own `daysOnMarket` is
    per segment, so a triple re-list reports three short, healthy-looking
    vacancies where there was one long one.

    Scoped to non-negative gaps deliberately. With overlapping spells the sum
    of per-spell DOMs can *exceed* the chain's span (the same days counted by
    two listing ids), so `>=` is not the claim there — that case is the
    chain-boundary property below."""
    segments, gaps = spec
    as_of = max(removed for _, removed, _ in segments) + timedelta(days=1)
    comp = _one_comp(segments, as_of)

    spell_doms = sum((removed - listed).days for listed, removed, _ in segments)
    assert comp.effective_dom >= spell_doms, (
        f"merged DOM {comp.effective_dom} is under the sum of its spells' DOMs {spell_doms} — "
        f"gap days ({gaps}) are being dropped"
    )
    assert comp.effective_dom == spell_doms + sum(gaps), (
        "merged DOM is not the span from first listing to final removal"
    )
    assert comp.gap_days == sum(gaps), f"gap_days {comp.gap_days} != the merged gaps {sum(gaps)}"
    assert comp.relist_count == len(segments) - 1


@given(spec=_segment_specs(min_spells=1, max_spells=5, min_gap=0, max_gap=41, open_last=True),
       extra=st.integers(min_value=0, max_value=500))
@settings(deadline=None, max_examples=300)
def test_a_chain_ending_in_an_active_spell_is_censored_and_measured_to_the_pull_date(spec, extra) -> None:
    """F4-S3 AC4, verbatim: "a chain ending in an active spell is censored with
    DOM = today - first listed."

    "today" is the pull's `as_of`, never the wall clock (owner ruling 1,
    `models/domain.py`). Two things are asserted, and the first matters more:
    censored must be `True`. NORTH_STAR calls censored-vs-leased "the single
    most important distinction in the whole system" — a still-listed unit
    reported as leased enters the KM estimator as an observed outcome and
    biases every survival curve optimistic."""
    segments, _ = spec
    last_listed = segments[-1][0]
    as_of = last_listed + timedelta(days=extra)
    comp = _one_comp(segments, as_of)

    assert comp.censored is True, "a chain whose final spell is still active was not censored"
    assert comp.removal_class is None, (
        f"a censored comp carries removal_class={comp.removal_class!r}; a still-active listing "
        "has not been removed, let alone leased"
    )
    assert comp.effective_dom == (as_of - segments[0][0]).days, (
        f"censored DOM {comp.effective_dom} != as_of - first listed "
        f"{(as_of - segments[0][0]).days} — the floor is measured from the wrong start"
    )


@given(spec=_segment_specs(min_spells=2, max_spells=6, min_gap=-400, max_gap=41, open_last=False))
@settings(deadline=None, max_examples=400)
def test_a_merged_chains_dom_ends_at_the_latest_removal_it_observed(spec) -> None:
    """Known bug 2, as the story states it: "effectiveDOM = **final removal** -
    first listed."

    `_build_comp` ends the chain at `chain[-1].removed` — the last spell *by
    listed date*, which is not the last spell to end once any two spells in
    the chain overlap. Two real comps are wrong today:

        2350 S Leavitt St 1R    dom  91, observed through 2026-01-17 => 128
        2453 W 46th Pl 1        dom 165, observed through 2026-05-20 => 205

    Both under-report, and DOM is the kNN *target* (D19a): a truncated DOM
    tells the survival curve a unit leased six weeks before it actually did."""
    segments, _ = spec
    removals = [removed for _, removed, _ in segments]
    as_of = max(removals) + timedelta(days=60)
    comp = _one_comp(segments, as_of)

    assert comp.censored is False
    assert comp.effective_dom == (max(removals) - segments[0][0]).days, (
        f"chain DOM {comp.effective_dom} does not run to the latest removal observed "
        f"({max(removals)}); it appears to end at the last spell by listed date "
        f"({removals[-1]}), which is {(removals[-1] - segments[0][0]).days}"
    )


#: Chains whose spells OVERLAP and then re-list, so that "off market since the
#: previous spell's removal" and "off market since the chain's latest removal"
#: give different totals. Each row is (segments, true_off_market, naive_per_pair).
#:
#: This shape does not occur in the committed pull — every real overlapping
#: chain has `gap_days == 0` under both readings — which is exactly why it
#: needs pinning here: a dormant rule regresses silently.
_OVERLAP_THEN_RELIST = [
    pytest.param(
        [(date(2025, 1, 1), date(2025, 6, 1)), (date(2025, 2, 1), date(2025, 3, 1)), (date(2025, 6, 21), date(2025, 7, 1))],
        20,
        112,
        id="one-overlap-then-a-20d-gap",
    ),
    pytest.param(
        [(date(2025, 1, 1), date(2025, 9, 1)), (date(2025, 2, 1), date(2025, 3, 1)), (date(2025, 3, 10), date(2025, 4, 1))],
        0,
        9,
        id="two-spells-swallowed-whole-never-off-market",
    ),
    pytest.param(
        [(date(2025, 1, 1), date(2025, 5, 1)), (date(2025, 4, 1), date(2025, 4, 20)), (date(2025, 5, 11), date(2025, 6, 1)), (date(2025, 6, 10), date(2025, 7, 1))],
        19,
        30,
        id="overlap-then-two-real-gaps",
    ),
]


@pytest.mark.parametrize(("segments", "true_off_market", "naive_per_pair"), _OVERLAP_THEN_RELIST)
def test_gap_days_is_time_the_unit_was_actually_off_market(
    segments, true_off_market, naive_per_pair
) -> None:
    """`gapDays` is "retained for badges" (F4-S3) — the row badge tells the
    user how long this unit sat off market between listings, so it must be
    time the unit was actually off market.

    Summing per *adjacent pair* from the previous spell's removal double-counts
    across an overlap: it charges the unit for days it was demonstrably still
    listed, and can report more off-market days than the chain has days. Here
    the two readings disagree by construction, and the naive total is asserted
    to be wrong explicitly so this test cannot silently start agreeing with it.

    Written as a second, independent guard on a rule that is **dormant on the
    committed pull** (every real overlapping chain reports `gap_days == 0`
    either way). A dormant rule defended by a single test is one careless edit
    from regressing unnoticed; QA owns that risk, so this lives beside the
    developer's own case rather than relying on it."""
    assert true_off_market != naive_per_pair, "test-input defect: the readings must disagree"

    as_of = max(removed for _, removed in segments) + timedelta(days=60)
    comp = _one_comp([(listed, removed, 2000.0) for listed, removed in segments], as_of)

    assert comp.gap_days == true_off_market, (
        f"gap_days={comp.gap_days}, expected {true_off_market}. "
        f"{naive_per_pair} is the per-adjacent-pair total, which counts days the unit was "
        f"still listed under another id."
    )
    assert comp.gap_days <= comp.effective_dom, (
        f"gap_days={comp.gap_days} exceeds the chain's own span {comp.effective_dom} — the "
        "unit cannot be off market for longer than the chain lasted"
    )


# ---------------------------------------------------------------------------
# the merged record's remaining defined fields
# ---------------------------------------------------------------------------


@given(spec=_segment_specs(min_spells=1, max_spells=5, min_gap=0, max_gap=41, open_last=False))
@settings(deadline=None, max_examples=200)
def test_the_merged_records_initial_ask_is_the_first_spells_price(spec) -> None:
    """F4-S3: "initialAsk = first spell's price". The premium numerator
    (`NORTH_STAR`): taking a later, already-cut price would make every
    laundered listing look like it was priced correctly from the start,
    which is the exact illusion the re-list creates."""
    segments, _ = spec
    as_of = max(removed for _, removed, _ in segments) + timedelta(days=1)
    comp = _one_comp(segments, as_of)
    assert comp.initial_ask == segments[0][2]
    assert comp.first_listed == segments[0][0]
    assert comp.cohort_year == segments[0][0].year


@given(
    high=st.integers(min_value=1000, max_value=9000).map(float),
    delta=st.integers(min_value=1, max_value=3000).map(float),
    gap=st.integers(min_value=0, max_value=41),
)
@settings(deadline=None, max_examples=200)
def test_a_relist_at_a_lower_price_is_recorded_as_a_cut(high, delta, gap) -> None:
    """F4-S3: "cutHistory = all price changes across spells (a re-list at a
    lower price **is** a cut)". The clause exists because the price drop is
    invisible in the raw data — each spell reports its own price as if it
    were an opening ask."""
    listed, removed = _START, _START + timedelta(days=30)
    relisted = removed + timedelta(days=gap)
    comp = _one_comp(
        [(listed, removed, high), (relisted, relisted + timedelta(days=10), high - delta)],
        relisted + timedelta(days=100),
    )
    assert len(comp.cut_history) == 1, f"expected one cut, got {comp.cut_history}"
    cut = comp.cut_history[0]
    assert (cut.on, cut.from_price, cut.to_price) == (relisted, high, high - delta)


@given(
    low=st.integers(min_value=500, max_value=5000).map(float),
    delta=st.integers(min_value=0, max_value=3000).map(float),
    gap=st.integers(min_value=0, max_value=41),
)
@settings(deadline=None, max_examples=200)
def test_a_relist_at_the_same_or_a_higher_price_is_not_a_cut(low, delta, gap) -> None:
    """The complement. A cut history that also logs raises would make the
    `[SCISSORS] 2150->2050` row badge (spec §6.4) meaningless, and would
    inflate the "priced down" evidence a user reads before setting a rent."""
    listed, removed = _START, _START + timedelta(days=30)
    relisted = removed + timedelta(days=gap)
    comp = _one_comp(
        [(listed, removed, low), (relisted, relisted + timedelta(days=10), low + delta)],
        relisted + timedelta(days=100),
    )
    assert comp.cut_history == (), f"a price rise of {delta} was recorded as a cut: {comp.cut_history}"
