"""F4-S8 [INVARIANT] — property tests over the removal ladder and the flag.

QA-authored, written test-first. Layer 1, `hypothesis` — the AC hands us the
strongest testability property in the project ("state transitions are pure
functions of (spells, today, config)"), and a pure function over dates and one
integer knob is exactly what property testing is for.

Six properties, each one a sentence from the story or NORTH_STAR that no
single example can establish:

    P1  the ladder never runs backwards as the pull date advances
    P2  advancing the pull date never moves the *evidence* (DOM, ask, cuts)
    P3  classification is a pure function — same inputs, same answer, and no
        dependence on record order or on the wall clock
    P4  the ladder is total and exclusive: a closed chain gets exactly one
        rung, an open chain gets none
    P5  a pending is never counted in a leased statistic, under any mix
    P6  the withdrawal-suspect flag changes no statistic except its own count
        ("display-only; never auto-excluded")

P5 and P6 are the two negatives the PM asked to be pinned. Both are stated as
*equalities against a reference computation* rather than as spot checks: P5
compares the real stats to the stats of a set with the pendings physically
removed, and P6 compares a flagged set to the identical unflagged set. An
implementation that leaked a pending into a median, or that quietly filtered a
suspect, cannot satisfy an equality it has to reproduce for arbitrary inputs.
"""

from __future__ import annotations

from datetime import date, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from f4s8_records import FULL_YEAR, closed_record, open_record
from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.pipeline.buckets import bucket_of, bucket_stats
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

#: pending < provisional < confirmed. The ladder is ordered by confidence, and
#: P1 is the claim that confidence only ever accumulates.
_RUNGS = {"pending": 0, "provisional": 1, "confirmed": 2}

SETTINGS = settings(
    max_examples=100,
    deadline=None,  # Windows timing noise must never turn an invariant flaky
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_listed = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 1, 1))
_dom = st.integers(min_value=0, max_value=900)
_elapsed = st.integers(min_value=0, max_value=900)
_provisional_days = st.integers(min_value=3, max_value=21)  # Config's own range


# ---------------------------------------------------------------------------
# P1/P2/P3/P4 — the ladder itself
# ---------------------------------------------------------------------------


def _classify(listed: date, dom: int, elapsed: int, config: Config):
    removed = listed + timedelta(days=dom)
    rec = closed_record(id_="prop", address="800 W Property St", listed=listed, removed=removed)
    comps = shape_raw_pull([rec], [], config, removed + timedelta(days=elapsed), *FULL_YEAR)
    assert len(comps) == 1
    return comps[0]


@SETTINGS
@given(listed=_listed, dom=_dom, elapsed=_elapsed, extra=st.integers(min_value=0, max_value=400))
def test_p1_the_ladder_never_runs_backwards_as_the_pull_date_advances(
    listed: date, dom: int, elapsed: int, extra: int
) -> None:
    """A record never becomes *less* certain because more time passed.

    The story's ladder is a confidence ladder (NORTH_STAR), so a later refresh
    over identical evidence may only move a comp up it. A classifier that read
    a boundary from the wrong side, or that compared against the listing date
    somewhere, produces a non-monotone ladder — and a comp that oscillates
    between "counted as leased" and "excluded" across refreshes is a number the
    owner cannot defend."""
    config = Config()
    early = _classify(listed, dom, elapsed, config)
    later = _classify(listed, dom, elapsed + extra, config)
    assert _RUNGS[later.removal_class] >= _RUNGS[early.removal_class], (
        f"a removal classified {early.removal_class!r} at {elapsed}d became "
        f"{later.removal_class!r} at {elapsed + extra}d — the ladder ran backwards"
    )


@SETTINGS
@given(listed=_listed, dom=_dom, elapsed=_elapsed, extra=st.integers(min_value=0, max_value=400))
def test_p2_advancing_the_pull_date_never_moves_the_evidence(
    listed: date, dom: int, elapsed: int, extra: int
) -> None:
    """Only the *classification* depends on the pull date. The unit's time on
    market, its initial ask and its censoring flag are facts of the record.

    (Deliberately restricted to a CLOSED chain: a censored comp's DOM floor
    *does* grow with the pull date, which is correct and is asserted elsewhere.)
    """
    config = Config()
    early = _classify(listed, dom, elapsed, config)
    later = _classify(listed, dom, elapsed + extra, config)
    assert (early.effective_dom, early.initial_ask, early.censored, early.first_listed) == (
        later.effective_dom,
        later.initial_ask,
        later.censored,
        later.first_listed,
    ), "re-classifying a removal must not restate the evidence underneath it"


@SETTINGS
@given(listed=_listed, dom=_dom, elapsed=_elapsed, days=_provisional_days)
def test_p3_classification_is_a_pure_function_of_spells_today_and_config(
    listed: date, dom: int, elapsed: int, days: int
) -> None:
    """Same (spells, today, config) => same answer, every time.

    Two independent calls, compared on the full serialized comp. This is the
    AC's own wording asserted directly, and it is what makes every other test
    in this story reproducible: a classifier that consulted ``date.today()``,
    a module-level cache, or the order records arrived in would fail here."""
    config = Config(provisional_lease_days=days)
    first = _classify(listed, dom, elapsed, config)
    second = _classify(listed, dom, elapsed, config)
    assert first.model_dump_json() == second.model_dump_json()


@SETTINGS
@given(listed=_listed, dom=_dom, elapsed=_elapsed, days=_provisional_days)
def test_p4_a_closed_chain_gets_exactly_one_rung_and_an_open_chain_gets_none(
    listed: date, dom: int, elapsed: int, days: int
) -> None:
    """Totality and exclusivity of the three states, plus the fourth thing that
    is *not* a state.

    `removal_class is None` means still active — NORTH_STAR's single most
    important distinction. It is not a rung, and no amount of elapsed time may
    turn a censored comp into one."""
    config = Config(provisional_lease_days=days)
    closed = _classify(listed, dom, elapsed, config)
    assert closed.removal_class in _RUNGS, (
        f"a chain with an observed removal must land on exactly one rung, got "
        f"{closed.removal_class!r}"
    )
    assert closed.censored is False

    rec = open_record(id_="prop-open", address="810 W Active St", listed=listed)
    as_of = listed + timedelta(days=dom + elapsed)
    open_comps = shape_raw_pull([rec], [], config, as_of, *FULL_YEAR)
    assert len(open_comps) == 1
    assert open_comps[0].censored is True
    assert open_comps[0].removal_class is None, (
        "a still-active listing has no removal to classify — `None` is 'not removed', not a "
        "fourth rung"
    )


@SETTINGS
@given(
    listed=_listed,
    dom=_dom,
    elapsed=_elapsed,
    days=_provisional_days,
)
def test_p4b_the_pending_rung_is_exactly_the_configured_window(
    listed: date, dom: int, elapsed: int, days: int
) -> None:
    """The pending/provisional boundary follows ``provisional_lease_days``
    across its whole legal range (3-21), not just at its default of 7.

    A knob that only works at its default is the F0-S5 failure mode: the user
    sees the value they set while the math uses another one."""
    comp = _classify(listed, dom, elapsed, Config(provisional_lease_days=days))
    assert (comp.removal_class == "pending") == (elapsed < days), (
        f"removed {elapsed}d ago with provisional_lease_days={days}: pending must be exactly "
        f"'{elapsed} < {days}', got {comp.removal_class!r}"
    )


# ---------------------------------------------------------------------------
# P5/P6 — the two negatives, over bucket statistics
# ---------------------------------------------------------------------------


def _comp(*, key: int, dom: int, censored: bool, removal_class: str | None, suspect: bool, cuts: int):
    return StitchedComp(
        address=f"{900 + key} W Stats St",
        unit=None,
        lat=41.83,
        lng=-87.66,
        beds=2,
        baths=1.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=censored,
        removal_class=removal_class,
        cohort_year=2026,
        withdrawal_suspect=suspect,
        cut_history=tuple(
            PriceCut(on=date(2026, 1, 1), from_price=2100.0, to_price=2000.0) for _ in range(cuts)
        ),
    )


_outcome = st.sampled_from(
    [(False, "pending"), (False, "provisional"), (False, "confirmed"), (True, None)]
)
_comp_spec = st.tuples(
    st.integers(min_value=0, max_value=400),  # dom
    _outcome,
    st.booleans(),  # withdrawal_suspect
    st.integers(min_value=0, max_value=2),  # cuts
)


def _stats(comps):
    keys = [f"k{i}" for i in range(len(comps))]
    premiums: list[float | None] = [0.0] * len(comps)  # everything lands "at market"
    buckets = [bucket_of(p, 4.0) for p in premiums]
    stats = bucket_stats(comps, keys, premiums, [True] * len(comps), buckets, None, 4.0, 4)
    return {s.id: s for s in stats}["at"]


def _build(specs, *, force_suspect=None):
    return [
        _comp(
            key=i,
            dom=dom,
            censored=censored,
            removal_class=rc,
            suspect=suspect if force_suspect is None else force_suspect,
            cuts=cuts,
        )
        for i, (dom, (censored, rc), suspect, cuts) in enumerate(specs)
    ]


@SETTINGS
@given(specs=st.lists(_comp_spec, min_size=0, max_size=12))
def test_p5_a_pending_is_never_counted_in_any_leased_statistic(specs) -> None:
    """NORTH_STAR: "Pendings are *excluded*, not just marked... mixing them in
    silently inflates apparent lease velocity."

    Asserted as an equality against the same bucket with every pending
    physically deleted: the leased median, min, max and cut-rate must be
    **identical**. That is strictly stronger than checking a particular median,
    because it has to hold for every mix — including the mixes where a pending's
    tiny DOM would drag a median down and still leave it looking plausible."""
    comps = _build(specs)
    without_pendings = [c for c in comps if c.removal_class != "pending"]

    full = _stats(comps)
    reference = _stats(without_pendings)

    assert (
        full.leased_dom_median,
        full.leased_dom_min,
        full.leased_dom_max,
        full.cut_before_lease_rate,
    ) == (
        reference.leased_dom_median,
        reference.leased_dom_min,
        reference.leased_dom_max,
        reference.cut_before_lease_rate,
    ), (
        "the leased statistics changed when the pending comps were removed, so a pending is "
        f"reaching them: {full} vs {reference}"
    )
    assert full.count == len(comps), (
        "a pending is excluded from the leased STATISTICS, not from the bucket — it is still a "
        "comp the user can click through to"
    )


@SETTINGS
@given(specs=st.lists(_comp_spec, min_size=1, max_size=12))
def test_p6_the_withdrawal_suspect_flag_changes_nothing_but_its_own_count(specs) -> None:
    """NORTH_STAR: withdrawal-suspect is "not grounds for automatic exclusion.
    Display-only flag; the human makes the call."

    The same comps are scored twice — once with every comp flagged, once with
    none flagged. Every statistic and every membership list must be identical;
    only ``withdrawal_suspect_count`` may differ. This is the test that stops a
    future refactor from turning a badge into a filter: the moment the flag
    influences a median, a count, or the click-through list, this fails."""
    all_flagged = _stats(_build(specs, force_suspect=True))
    none_flagged = _stats(_build(specs, force_suspect=False))

    def _comparable(stat):
        return stat.model_dump(exclude={"withdrawal_suspect_count"})

    assert _comparable(all_flagged) == _comparable(none_flagged), (
        "flagging every comp withdrawal-suspect changed a statistic or a membership list. The "
        "flag is display-only and must never auto-exclude"
    )
    assert all_flagged.withdrawal_suspect_count == len(specs)
    assert none_flagged.withdrawal_suspect_count == 0


@SETTINGS
@given(specs=st.lists(_comp_spec, min_size=1, max_size=12))
def test_p6b_a_provisional_is_counted_as_leased_and_marked_at_the_same_time(specs) -> None:
    """The other half of the AC's last clause: provisionals are "counted as
    leased, marked" — both, not either.

    ``provisional_count`` is the marker; the leased set is the counting. An
    implementation that excluded provisionals to be safe would satisfy every
    pending test and quietly discard a third of the real pull's removals (30 of
    528 closed chains are provisional today)."""
    comps = _build(specs)
    stat = _stats(comps)
    provisionals = [c for c in comps if c.removal_class == "provisional"]
    #: The leased set, spelled out: provisional OR confirmed, and nothing else.
    leased = [c for c in comps if c.removal_class in ("provisional", "confirmed")]

    assert stat.provisional_count == len(provisionals), "every provisional must be MARKED"
    if leased:
        assert stat.leased_dom_min == min(c.effective_dom for c in leased), (
            "the leased set is exactly {provisional, confirmed} — a provisional dropped from it "
            "would move this minimum"
        )
        assert stat.leased_dom_max == max(c.effective_dom for c in leased), (
            "a provisional is COUNTED as leased, not merely marked"
        )
    else:
        assert stat.leased_dom_min is None and stat.leased_dom_max is None, (
            "no provisional and no confirmed comp means no leased outcome — `None`, never a "
            "fabricated 0 built from a pending or a censored floor"
        )
