"""F10-S1 [INVARIANT] — "no interpolation anywhere", at the DOM median.

QA-authored (AGENT_QA.md protocol). Layer 1: `bucket_stats()` is directly
importable and callable with plain `StitchedComp` instances.

WHAT THIS FILE IS FOR
---------------------
§150 tags **both** the formulas and the "no interpolation anywhere" rule as
honesty invariants. `test_ws1_bucket_outcome_stats.py` already covers the
exclusion formulas (pending out, censored never leased, provisional counted).
It does **not** cover the arithmetic of the median itself, and every leased
set it builds is odd-sized — so the one place interpolation can enter silently
is exactly the place nothing looks.

THE INVARIANT, STATED AS AN OUTCOME (not as a technique)
--------------------------------------------------------
    `leased_dom_median` must be an ELEMENT of the bucket's leased DOM set.

That phrasing is deliberate. AGENT_QA.md forbids pinning a `[DEFAULT]`'s
suggested technique, so nothing here asserts *which* median function is
called — `statistics.median_low`, `numpy.percentile(..., method="lower")`,
`stats.weighted.weighted_median` with uniform weights, or a hand-rolled
`sorted(x)[(n - 1) // 2]` all satisfy it identically. What is locked is the
*outcome*: the reported median is a day-count some real comp actually
exhibited, never a value invented between two observations.

WHY THAT READING, AND NOT "a conventional median is fine"
----------------------------------------------------------
Three independent sources point the same way:

1. **F0-S3 is `[INVARIANT]` and already locks this project's median.**
   `stats/weighted.py`'s module docstring: "the *lower* weighted median [...]
   Never the upper variant, **never interpolated** — those are different
   statistics", and "with uniform weights this reproduces the plain *lower*
   median (`statistics.median_low`)". The anchor, the cohort medians, the kNN
   aggregation and the KM estimator all take their median from there. A
   *fourth* median with different arithmetic, reported next to those three, is
   not a defensible product.
2. **NORTH_STAR's thesis:** "Every number this tool produces must trace back
   to real, observed comps." A DOM median is presented to the owner as an
   outcome ("comps leased in 2-4 weeks", F11's flow) — an averaged value has
   no comp behind it to click through to, which also breaks F10's own
   evidence-first promise that "every number is one click from its evidence".
3. **§150's own words:** "no interpolation anywhere". *Anywhere* is broader
   than the empty-bucket clause that follows it, which §150 states separately.

⚠ **The interpretation is flagged to the PM as a semantic question** — QA does
not adjudicate meaning (SEMANTIC_CHANGE_PROTOCOL). If the owner rules that an
averaged median is what "leased DOM median" should mean, this file is what has
to be edited to say so, in the same commit as the ruling. That is the point of
pinning it: the choice becomes visible and blessable instead of incidental.

MEASURED, NOT REASONED ABOUT (2026-07-29, `ws1-real`, default request body)
---------------------------------------------------------------------------
Every legal `bucket_half_width_pct` puts at least one bucket's leased set at
an even size, and that bucket then reports a median no comp exhibited:

    hw=2.0   below  leased=196  median=34.0   <- not in the leased set
    hw=4.0   above  leased=198  median=33.0   <- not in the leased set (32, 34)
    hw=6.0   below  leased=160  median=34.0   <- not in the leased set
    hw=8.0   above  leased=162  median=31.5   <- not in the leased set
    hw=10.0  at     leased=160  median=35.0   <- not in the leased set

At the shipped default (hw=4.0) it is the **above-market** bucket — the knee
detector, the whole reason F10 exists — and at hw=8.0 the number rendered is
**31.5 days**, a half-day DOM in a data set of integer day-counts.
`test_f10s1_real_pull_median_is_observed.py` holds the real-data arm; this
file holds the arithmetic.
"""

from __future__ import annotations

from statistics import median_low

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.pipeline.buckets import bucket_of, bucket_stats


def _comp(
    *,
    address: str,
    dom: int,
    censored: bool = False,
    removal_class: str | None = "confirmed",
    cuts: int = 0,
) -> StitchedComp:
    return StitchedComp(
        address=address,
        unit=None,
        lat=41.83,
        lng=-87.66,
        beds=3,
        baths=2.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=censored,
        removal_class=removal_class,
        cohort_year=2026,
        cut_history=tuple(
            PriceCut(on="2026-01-01", from_price=2100.0, to_price=2000.0)
            for _ in range(cuts)
        ),
    )


def _at_bucket(comps: list[StitchedComp], half_width_pct: float = 4.0):
    """Every comp premium 0.0, so they all land in the "at" bucket."""
    keys = [f"key-{i}" for i in range(len(comps))]
    premiums: list[float | None] = [0.0] * len(comps)
    buckets = [bucket_of(p, half_width_pct) for p in premiums]
    stats = bucket_stats(
        comps, keys, premiums, [True] * len(comps), buckets, None, half_width_pct
    )
    return {s.id: s for s in stats}["at"]


# --------------------------------------------------------------------------
# The invariant, at its smallest possible witness.
# --------------------------------------------------------------------------


def test_even_sized_leased_set_median_is_an_observed_dom_not_a_midpoint() -> None:
    """§150 [INVARIANT] "no interpolation anywhere" — the two-comp witness.

    Leased DOMs {20, 40}. A conventional median averages the two middles and
    reports 30.0 — a day-count neither comp exhibited, and one the user cannot
    click through to any evidence for. The locked project median (F0-S3,
    reproduced by `median_low` at uniform weights) reports 20.
    """
    at = _at_bucket(
        [
            _comp(address="1 W A St", dom=20),
            _comp(address="2 W B St", dom=40),
        ]
    )

    assert at.leased_dom_median == 20.0, (
        f"leased DOMs are {{20, 40}}; got {at.leased_dom_median}. "
        f"30.0 is the interpolated midpoint of two observations and §150 forbids "
        f"interpolation anywhere — no comp sat for 30 days, so the number has no "
        f"evidence behind it to click through to (F10's evidence-first promise)."
    )
    assert at.leased_dom_median in {20.0, 40.0}, (
        f"the median must be one of the observed DOMs, got {at.leased_dom_median}"
    )


def test_even_sized_leased_set_median_is_never_fractional_for_integer_doms() -> None:
    """The most visible face of the same defect: a half-day DOM.

    `effective_dom` is an `int` by construction (`domain.StitchedComp`), so a
    fractional median is proof of invented precision on its face — nothing in
    the evidence can produce a `.5`. Measured on `ws1-real` at
    `bucket_half_width_pct=8.0`: the above-market bucket reports **31.5**.
    """
    at = _at_bucket(
        [
            _comp(address="1 W A St", dom=31),
            _comp(address="2 W B St", dom=32),
        ]
    )
    assert at.leased_dom_median is not None
    assert float(at.leased_dom_median).is_integer(), (
        f"every `effective_dom` is an integer, so a bucket's DOM median cannot "
        f"honestly be fractional; got {at.leased_dom_median}"
    )


@pytest.mark.parametrize(
    "doms",
    [
        pytest.param([20, 40], id="n=2-simplest-even"),
        pytest.param([10, 20, 30, 40], id="n=4-even"),
        pytest.param([1, 2, 3, 4, 5, 6], id="n=6-even-consecutive"),
        pytest.param([32, 34], id="n=2-the-real-pulls-two-middles-at-hw-4"),
        pytest.param([5, 5, 6, 6], id="n=4-even-with-ties"),
        pytest.param([0, 1], id="n=2-including-a-zero-dom"),
        pytest.param([7, 7], id="n=2-both-middles-equal-median-is-7-either-way"),
    ],
)
def test_median_is_always_an_element_of_the_leased_set(doms: list[int]) -> None:
    """Exhaustive at L1 — the loop belongs here, never in a browser."""
    at = _at_bucket(
        [_comp(address=f"{i} W A St", dom=d) for i, d in enumerate(doms)]
    )
    assert at.leased_dom_median in {float(d) for d in doms}, (
        f"leased DOMs {sorted(doms)} -> median {at.leased_dom_median}, which is not "
        f"one of them. §150 [INVARIANT]: no interpolation anywhere."
    )


def test_odd_sized_leased_set_is_unaffected(  ) -> None:
    """The regression guard on the other side of the fix.

    An odd-sized set has an unambiguous middle observation; whatever change
    makes the even case honest must not move the odd case. (This is also why
    the defect survived: two of the three buckets on the real pull are
    odd-sized at the default half-width, so two thirds of the visible evidence
    looks right.)
    """
    at = _at_bucket(
        [
            _comp(address="1 W A St", dom=20),
            _comp(address="2 W B St", dom=25),
            _comp(address="3 W C St", dom=40),
        ]
    )
    assert at.leased_dom_median == 25.0


def test_min_and_max_are_observed_by_construction_and_bracket_the_median() -> None:
    """AC4 — the range, plus the ordering the median must respect."""
    at = _at_bucket(
        [
            _comp(address="1 W A St", dom=12),
            _comp(address="2 W B St", dom=20),
            _comp(address="3 W C St", dom=40),
            _comp(address="4 W D St", dom=99),
        ]
    )
    assert at.leased_dom_min == 12
    assert at.leased_dom_max == 99
    assert at.leased_dom_median is not None
    assert at.leased_dom_min <= at.leased_dom_median <= at.leased_dom_max


# --------------------------------------------------------------------------
# The property form — the invariant over the whole input space, not 7 cases.
# --------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    doms=st.lists(st.integers(min_value=0, max_value=1200), min_size=1, max_size=40),
)
def test_property_reported_median_is_always_one_of_the_leased_doms(
    doms: list[int],
) -> None:
    """For ANY leased set, the reported median is an element of it.

    `max_value=1200` because the real pull's `effective_dom` ranges 2..1006 —
    long re-listing chains are real, so the property must hold over the range
    the product actually sees, not just over small numbers.
    """
    at = _at_bucket([_comp(address=f"{i} W A St", dom=d) for i, d in enumerate(doms)])
    assert at.leased_dom_median in {float(d) for d in doms}, (
        f"median {at.leased_dom_median} is not an observed DOM in {sorted(doms)}"
    )


@settings(max_examples=300, deadline=None)
@given(doms=st.lists(st.integers(min_value=0, max_value=1200), min_size=1, max_size=40))
def test_property_median_agrees_with_the_projects_locked_median_definition(
    doms: list[int],
) -> None:
    """Consistency with F0-S3, the [INVARIANT] that already locks this repo's
    median: "with uniform weights this reproduces the plain *lower* median
    (`statistics.median_low`)".

    A bucket's leased set is unweighted (§150 says nothing about weighting the
    DOM median — user weights are aggregation-only, D19), so uniform weights is
    exactly this case. This asserts the *outcome* the locked definition
    produces, not that any particular function was called.
    """
    at = _at_bucket([_comp(address=f"{i} W A St", dom=d) for i, d in enumerate(doms)])
    assert at.leased_dom_median == float(median_low(doms)), (
        f"leased DOMs {sorted(doms)}: F0-S3's locked median gives "
        f"{median_low(doms)}, bucket reports {at.leased_dom_median}"
    )


@settings(max_examples=200, deadline=None)
@given(
    leased=st.lists(st.integers(min_value=0, max_value=1200), min_size=1, max_size=20),
    censored=st.lists(st.integers(min_value=0, max_value=1200), min_size=0, max_size=20),
    pending=st.lists(st.integers(min_value=0, max_value=1200), min_size=0, max_size=20),
)
def test_property_censored_and_pending_doms_can_never_move_the_median(
    leased: list[int], censored: list[int], pending: list[int]
) -> None:
    """The single most important distinction in the system (NORTH_STAR), as a
    property rather than three hand-picked fixtures.

    Adding any number of censored or pending comps — at any DOM, including ones
    that would dominate the ordering — must leave `leased_dom_median/min/max`
    and `cut_before_lease_rate` bit-identical. Only `count` and
    `censored_floors` may move.
    """
    leased_comps = [
        _comp(address=f"L{i} W A St", dom=d) for i, d in enumerate(leased)
    ]
    noise = [
        _comp(address=f"C{i} W B St", dom=d, censored=True, removal_class=None, cuts=3)
        for i, d in enumerate(censored)
    ] + [
        _comp(address=f"P{i} W C St", dom=d, removal_class="pending", cuts=3)
        for i, d in enumerate(pending)
    ]

    clean = _at_bucket(leased_comps)
    noisy = _at_bucket(leased_comps + noise)

    assert noisy.leased_dom_median == clean.leased_dom_median
    assert noisy.leased_dom_min == clean.leased_dom_min
    assert noisy.leased_dom_max == clean.leased_dom_max
    assert noisy.cut_before_lease_rate == clean.cut_before_lease_rate
    assert noisy.count == clean.count + len(noise)
    assert noisy.censored_floors == sorted(censored)
