"""F10-S1 — the bucket stats' INPUT SPACE, adversarially.

QA-authored, Layer 1. This file is the probe-first half of the plan: not
"does the AC happen" but "what must never happen when the input is hostile,
degenerate, or simply at a boundary nobody drew".

WHY IT EXISTS (queue row 13c, measured)
---------------------------------------
On row 13c all thirteen defects were found *after* implementation existed,
across two review rounds — and six came from inputs knowable before a line was
written (an unbounded integer, a corrupt field, a zero-byte file, a non-empty
directory). The plan had covered acceptance criteria and not hostile inputs.
So these rows are deliberately about the shape of the input, and each one
names the wrong answer it is there to catch.

WHAT IS *NOT* REOPENED HERE
---------------------------
`buckets.py`'s `_LEASED_CLASSES` excludes `pending`, and that is settled, not
open: queue row 11b's open owner decision is about the **Kaplan-Meier event
set**, and its own text confirms the AC names only bucket stats. It is pinned
below (`test_pending_is_never_admitted_to_the_leased_set`) precisely *because*
it is settled — a future change that quietly admits `pending` would inflate
apparent lease velocity, which is the exact failure NORTH_STAR exists to
prevent, and it would pass every AC-shaped test in the suite.
"""

from __future__ import annotations

import math

import pytest

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.pipeline.buckets import (
    BUCKET_IDS,
    bucket_of,
    bucket_stats,
    premium_bounds,
)
from rentcomp.storage.config import Config


def _comp(
    *,
    address: str = "1 W A St",
    dom: int = 20,
    censored: bool = False,
    removal_class: str | None = "confirmed",
    cuts: int = 0,
    withdrawal_suspect: bool = False,
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
        withdrawal_suspect=withdrawal_suspect,
        cut_history=tuple(
            PriceCut(on="2026-01-01", from_price=2100.0, to_price=2000.0)
            for _ in range(cuts)
        ),
    )


def _stats(
    pairs: list[tuple[StitchedComp, float | None]],
    half_width_pct: float = 4.0,
    included: list[bool] | None = None,
    anchor=None,
):
    """`pairs` is (comp, premium); membership is derived by `bucket_of`."""
    comps = [c for c, _ in pairs]
    premiums = [p for _, p in pairs]
    keys = [f"key-{i}" for i in range(len(comps))]
    buckets = [bucket_of(p, half_width_pct) for p in premiums]
    stats = bucket_stats(
        comps,
        keys,
        premiums,
        [True] * len(comps) if included is None else included,
        buckets,
        anchor,
        half_width_pct,
    )
    return {s.id: s for s in stats}


# ==========================================================================
# P1 — a bucket whose ONLY member is censored: non-empty bucket, empty
#      leased set. The two "emptiness" notions are different and §150 only
#      describes one of them.
# ==========================================================================


def test_bucket_whose_only_member_is_censored_is_not_an_empty_bucket() -> None:
    at = _stats([(_comp(dom=45, censored=True, removal_class=None), 0.0)])["at"]

    assert at.count == 1, "the bucket HAS a member — it is not an empty bucket"
    assert at.censored_floors == [45], "the floor is the evidence that survives"
    assert at.leased_dom_median is None, (
        "a censored comp is never counted as leased (NORTH_STAR: the single most "
        "important distinction in the system) — so there is no leased median"
    )
    assert at.leased_dom_min is None
    assert at.leased_dom_max is None
    assert at.cut_before_lease_rate is None, (
        "0 leased comps is a division by zero, and the honest answer is None — "
        "0.0 would read as a real, measured 'nobody had to cut'"
    )


def test_a_censored_comps_cuts_never_enter_the_cut_rate_numerator() -> None:
    """A censored comp that HAS cut its price is the trap: it looks like
    evidence for the knee, but it has not leased, so it is evidence for
    nothing yet. Numerator and denominator must both ignore it."""
    at = _stats(
        [
            (_comp(address="1 W A St", dom=45, censored=True, removal_class=None, cuts=4), 0.0),
            (_comp(address="2 W B St", dom=20, cuts=0), 0.0),
        ]
    )["at"]
    assert at.cut_before_lease_rate == 0.0, (
        "one leased comp with no cuts -> 0.0; the censored comp's 4 cuts must not "
        f"appear in either term, got {at.cut_before_lease_rate}"
    )
    assert at.leased_dom_max == 20, "the censored 45-day floor must not become a max"


def test_pending_is_never_admitted_to_the_leased_set() -> None:
    """PINNED, not reopened (see the module docstring / queue row 11b).

    A `pending` removal is <7d off market — too recent to trust. NORTH_STAR:
    "Pendings are *excluded*, not just marked — mixing them in silently
    inflates apparent lease velocity." A pending comp with a very fast DOM is
    the exact shape that would drag a median down if admitted.
    """
    at = _stats(
        [
            (_comp(address="1 W A St", dom=1, removal_class="pending", cuts=3), 0.0),
            (_comp(address="2 W B St", dom=2, removal_class="pending", cuts=3), 0.0),
            (_comp(address="3 W C St", dom=60, removal_class="confirmed", cuts=0), 0.0),
        ]
    )["at"]
    assert at.count == 3
    assert at.leased_dom_median == 60.0, (
        f"the only trustworthy lease sat 60 days; got {at.leased_dom_median}. A "
        f"median at or near 2 means the two pendings were admitted, which inflates "
        f"apparent lease velocity by 30x on this input."
    )
    assert at.leased_dom_min == 60
    assert at.cut_before_lease_rate == 0.0, (
        "the pendings' 6 cuts must not enter the numerator"
    )
    assert at.censored_floors == [], "a pending comp is removed, not censored — it is "
    "not a floor either; it is simply out of the statistics"


def test_a_bucket_of_only_pendings_reports_nulls_while_still_counting_them() -> None:
    at = _stats(
        [
            (_comp(address="1 W A St", dom=3, removal_class="pending"), 0.0),
            (_comp(address="2 W B St", dom=4, removal_class="pending"), 0.0),
        ]
    )["at"]
    assert at.count == 2, "the user can still click through to them"
    assert (at.leased_dom_median, at.leased_dom_min, at.leased_dom_max) == (None, None, None)
    assert at.cut_before_lease_rate is None
    assert at.censored_floors == []


# ==========================================================================
# P2 — a comp with NO premium. `bucket_of(None, ...) is None`, so the comp
#      belongs to no bucket at all. That is a THIRD state next to
#      included/excluded and nothing in §150 mentions it.
# ==========================================================================


def test_a_comp_with_no_premium_lands_in_no_bucket_and_is_not_at_market() -> None:
    """`bucket_of(None, hw)` must be `None`, never `"at"`.

    Silently treating "we could not compute a premium" as "priced at market"
    would put a comp with no $/sqft into the at-market DOM median — evidence
    for a premium it was never shown to have.
    """
    assert bucket_of(None, 4.0) is None
    stats = _stats([(_comp(dom=20), None)])
    for bucket_id in BUCKET_IDS:
        assert stats[bucket_id].count == 0, (
            f"a premium-less comp appeared in the {bucket_id!r} bucket"
        )
        assert stats[bucket_id].leased_dom_median is None
        assert stats[bucket_id].comp_keys == []


def test_premium_less_comps_are_orphaned_from_every_bucket_total() -> None:
    """The reconciliation consequence, stated so it cannot be rediscovered.

    Sum of bucket counts < number of included comps whenever any included comp
    has no premium. Today that gap is 0 on real data only because a comp with
    no `sqft` gets weight 0 and is therefore `excluded` before it reaches a
    bucket — a coupling nothing states and **queue row 9b moves** (it changes
    which comps carry a `sqft`). If 9b makes a premium-less comp includable,
    F9's rail reconciliation silently stops adding up.
    """
    pairs = [
        (_comp(address="1 W A St", dom=20), 0.0),
        (_comp(address="2 W B St", dom=30), None),
        (_comp(address="3 W C St", dom=40), None),
    ]
    stats = _stats(pairs)
    assert sum(stats[b].count for b in BUCKET_IDS) == 1, (
        "2 of the 3 included comps have no premium and so belong to no bucket"
    )


def test_nan_premium_is_not_silently_classified_as_at_market() -> None:
    """A NaN premium fails every comparison, so a naive `if p < lo / elif
    p > hi / else "at"` chain falls through to `"at"` — the worst possible
    default, because "at market" is the bucket the owner reads as the safe
    reference. NaN is reachable from float arithmetic upstream (0/0 on a
    degenerate cohort median), so the honest answer is `None` (no bucket) the
    same as a missing premium.
    """
    assert bucket_of(math.nan, 4.0) is None, (
        "a NaN premium must produce NO bucket; got "
        f"{bucket_of(math.nan, 4.0)!r} — falling through to 'at market' puts a "
        "comp with unknown pricing into the at-market DOM median"
    )


# ==========================================================================
# P3 — degenerate distributions: everything in one bucket, one comp per bucket.
# ==========================================================================


def test_all_comps_in_one_bucket_leaves_the_other_two_null_not_missing() -> None:
    """Always three buckets, in render order, even when two are empty."""
    stats = _stats([(_comp(address=f"{i} W A St", dom=10 * i + 5), 0.0) for i in range(1, 6)])
    assert [s.id for s in (stats[b] for b in BUCKET_IDS)] == list(BUCKET_IDS)
    assert stats["at"].count == 5
    for empty in ("below", "above"):
        assert stats[empty].count == 0
        assert stats[empty].leased_dom_median is None
        assert stats[empty].cut_before_lease_rate is None
        assert stats[empty].censored_floors == []


def test_exactly_one_comp_per_bucket_median_equals_min_equals_max() -> None:
    stats = _stats(
        [
            (_comp(address="1 W A St", dom=10), -0.20),
            (_comp(address="2 W B St", dom=30), 0.00),
            (_comp(address="3 W C St", dom=90), +0.20),
        ]
    )
    for bucket_id, dom in (("below", 10), ("at", 30), ("above", 90)):
        s = stats[bucket_id]
        assert s.count == 1
        assert (s.leased_dom_median, s.leased_dom_min, s.leased_dom_max) == (
            float(dom),
            dom,
            dom,
        ), f"{bucket_id}: a single-member bucket's median IS that member's DOM"


def test_boundary_premiums_are_inclusive_on_the_at_market_side() -> None:
    """A comp priced exactly at ±4% is at market, not above/below it — and the
    boundary is where an off-by-one lives. Asserted on both edges and one ULP
    outside each, because "±4%" with float ratios is not exact.
    """
    hw = 4.0
    assert bucket_of(-0.04, hw) == "at", "the lower edge itself is at market"
    assert bucket_of(+0.04, hw) == "at", "the upper edge itself is at market"
    assert bucket_of(math.nextafter(-0.04, -1.0), hw) == "below"
    assert bucket_of(math.nextafter(+0.04, +1.0), hw) == "above"
    assert bucket_of(0.0, hw) == "at"


def test_advertised_premium_bounds_agree_with_actual_bucket_assignment() -> None:
    """`premium_min`/`premium_max` go on the wire and F10-S2 draws the "< −4%"
    / "±4%" / "> +4%" labels from them. So the advertised interval has to claim
    exactly the premiums `bucket_of` actually assigns — otherwise the label
    lies about a comp the user can see in the other bucket.

    The gap this catches: `premium_bounds` reports `below = (None, −0.04)` and
    `at = (−0.04, +0.04)`, so **−0.04 is claimed by both**, while `bucket_of`
    (strict `<`) assigns it to `at` alone. The bounds pair has no way to say
    "exclusive at the top", so the two edges are the one place the wire cannot
    express the documented rule. Same at +0.04.
    """
    edge = -0.04
    for premium in (-10.0, -0.5, -0.0401, edge, -0.001, 0.0, 0.04, 0.0401, 0.5, 10.0):
        claimants = [b for b in BUCKET_IDS if _claims(b, premium, 4.0)]
        assert len(claimants) == 1, (
            f"premium {premium} is claimed by the advertised bounds of {claimants} — "
            f"exactly one bucket may claim it, and `bucket_of` assigns it to "
            f"{bucket_of(premium, 4.0)!r}"
        )
        assert bucket_of(premium, 4.0) == claimants[0], (
            f"premium {premium}: advertised bounds say {claimants[0]!r}, "
            f"bucket_of says {bucket_of(premium, 4.0)!r}"
        )


def _claims(bucket_id: str, premium: float, hw: float) -> bool:
    lo, hi = premium_bounds(bucket_id, hw)  # type: ignore[arg-type]
    return (lo is None or premium >= lo) and (hi is None or premium <= hi)


# ==========================================================================
# P4 — the half-width knob: 0, negative, huge, NaN. §150 calls it
#      "configurable", which is an input space, not a constant.
# ==========================================================================


@pytest.mark.parametrize("hw", [0.0, 1.9, 10.1, -4.0, 1e9, math.nan, math.inf])
def test_illegal_half_widths_are_rejected_at_the_config_boundary_never_clamped(
    hw: float,
) -> None:
    """`Config`'s contract (F0-S5): "Invalid value in process => ValueError.
    Never clamped, never replaced." §2.3's range is 2-10 percentage points
    inclusive, so 0 — the PM's probe — is illegal and must be *refused*, not
    quietly turned into 2.0 (which would relabel every comp in the pull
    without anybody choosing to).
    """
    with pytest.raises(ValueError):
        Config(bucket_half_width_pct=hw)


@pytest.mark.parametrize("hw", [2.0, 4.0, 10.0])
def test_legal_half_width_endpoints_are_accepted_inclusively(hw: float) -> None:
    assert Config(bucket_half_width_pct=hw).bucket_half_width_pct == hw


def test_the_widest_legal_half_width_still_leaves_three_real_buckets() -> None:
    """"Does one bucket swallow everything?" — at ±10% the at-market band is
    5x the default, so this checks the *definition* still partitions rather
    than degenerating. (Measured on `ws1-real` at hw=10.0: 153 / 174 / 157 —
    the at-market bucket becomes the largest but does not swallow the pull.)
    """
    lo, hi = premium_bounds("at", 10.0)
    assert (lo, hi) == (-0.10, 0.10)
    assert bucket_of(-0.11, 10.0) == "below"
    assert bucket_of(+0.11, 10.0) == "above"
    assert bucket_of(0.099, 10.0) == "at"


def test_half_width_is_percentage_points_converted_exactly_once() -> None:
    """4.0 means ±4%, i.e. the ratio ±0.04 — not ±400%. The unit confusion is
    a silent 100x: every comp lands "at market" and every bucket statistic
    becomes the whole-pull statistic, which still *looks* like a plausible
    number. Locked at the boundary values §2.3 names.
    """
    assert premium_bounds("at", 4.0) == (-0.04, 0.04)
    assert premium_bounds("below", 4.0) == (None, -0.04)
    assert premium_bounds("above", 4.0) == (0.04, None)
    assert bucket_of(0.05, 4.0) == "above", (
        "a +5% premium is above a ±4% band; 'at' here means the knob was read "
        "as a ratio (±400%) instead of percentage points"
    )


def test_unbounded_bucket_sides_report_none_not_a_sentinel_number() -> None:
    """`below` has no floor and `above` has no ceiling. A sentinel like -999.0
    or 1e9 would render as a real boundary."""
    assert premium_bounds("below", 4.0)[0] is None
    assert premium_bounds("above", 4.0)[1] is None


# ==========================================================================
# P5 — cut_before_lease_rate at both ends of its range.
# ==========================================================================


def test_cut_rate_of_exactly_one_is_distinguishable_from_no_data() -> None:
    """"Every leased comp had to cut" is the knee, fully triggered — the single
    most actionable reading in F10 ("above-market: 5 of 7 cut before leasing").
    It must be 1.0, and an empty leased set must be None, so the two can never
    be confused by a caller or a renderer.
    """
    all_cut = _stats(
        [
            (_comp(address="1 W A St", dom=40, cuts=1), 0.0),
            (_comp(address="2 W B St", dom=60, cuts=3), 0.0),
        ]
    )["at"]
    assert all_cut.cut_before_lease_rate == 1.0

    no_leases = _stats([(_comp(dom=40, censored=True, removal_class=None, cuts=2), 0.0)])["at"]
    assert no_leases.cut_before_lease_rate is None
    assert all_cut.cut_before_lease_rate is not no_leases.cut_before_lease_rate


def test_cut_rate_of_exactly_zero_is_a_real_value_not_an_absence() -> None:
    """0.0 and None are different findings: "nobody had to cut" is strong
    evidence the ask held, "no leased comps" is no evidence at all. Both are
    falsy in Python and in JS, which is how they get conflated downstream.
    """
    at = _stats(
        [
            (_comp(address="1 W A St", dom=20, cuts=0), 0.0),
            (_comp(address="2 W B St", dom=30, cuts=0), 0.0),
        ]
    )["at"]
    assert at.cut_before_lease_rate == 0.0
    assert at.cut_before_lease_rate is not None


def test_cut_rate_counts_comps_with_cuts_not_the_number_of_cuts() -> None:
    """The formula is (leased comps with >=1 cut) / (leased comps) — a comp
    that cut five times is ONE comp. Summing cuts instead of comps can exceed
    1.0, which is unreadable as a rate.
    """
    at = _stats(
        [
            (_comp(address="1 W A St", dom=20, cuts=5), 0.0),
            (_comp(address="2 W B St", dom=30, cuts=0), 0.0),
        ]
    )["at"]
    assert at.cut_before_lease_rate == 0.5, (
        f"1 of 2 leased comps cut -> 0.5, got {at.cut_before_lease_rate}"
    )
    assert 0.0 <= at.cut_before_lease_rate <= 1.0


@pytest.mark.parametrize("n_leased,n_with_cuts", [(1, 0), (1, 1), (3, 1), (7, 5), (10, 10)])
def test_cut_rate_is_always_a_proportion_in_the_closed_unit_interval(
    n_leased: int, n_with_cuts: int
) -> None:
    pairs = [
        (_comp(address=f"{i} W A St", dom=20 + i, cuts=1 if i < n_with_cuts else 0), 0.0)
        for i in range(n_leased)
    ]
    rate = _stats(pairs)["at"].cut_before_lease_rate
    assert rate == pytest.approx(n_with_cuts / n_leased)
    assert 0.0 <= rate <= 1.0


# ==========================================================================
# P6 — effective_dom == 0. The domain layer permits it (`Field(ge=0)`), so it
#      is representable whether or not it is meaningful.
# ==========================================================================


def test_a_zero_dom_leased_comp_reports_zero_not_null() -> None:
    """A same-day lease. `0` is a real observation and `None` means "no
    evidence"; both are falsy, so this is the pair most likely to be conflated
    by a renderer writing `{median || '—'}` — which would print a dash over
    the fastest lease in the pull.

    (There is a standing open question about whether a 0-DOM listing should be
    representable at all — that is upstream of this story. What F10-S1 owes is
    that *if* it arrives, it is reported as the 0 it is, not laundered into a
    dash. Measured: `ws1-real`'s minimum `effective_dom` is 2, so this case is
    unreachable on real data today and must be built by hand.)
    """
    at = _stats([(_comp(dom=0), 0.0)])["at"]
    assert at.count == 1
    assert at.leased_dom_median == 0.0
    assert at.leased_dom_median is not None
    assert at.leased_dom_min == 0
    assert at.leased_dom_max == 0


def test_a_zero_dom_censored_floor_is_reported_as_a_floor_of_zero() -> None:
    at = _stats([(_comp(dom=0, censored=True, removal_class=None), 0.0)])["at"]
    assert at.censored_floors == [0]
    assert at.leased_dom_median is None


# ==========================================================================
# P7 — ties, and the ordering guarantees of the two list-valued fields.
# ==========================================================================


def test_all_leased_doms_identical_gives_that_value_for_median_min_and_max() -> None:
    at = _stats([(_comp(address=f"{i} W A St", dom=30), 0.0) for i in range(4)])["at"]
    assert (at.leased_dom_median, at.leased_dom_min, at.leased_dom_max) == (30.0, 30, 30)


def test_censored_floors_are_sorted_ascending_and_hold_every_censored_member() -> None:
    """The UI reads them as "2 active at 45+, 60+", so order is part of the
    contract, and a censored comp must never be dropped — a dropped floor is a
    piece of counter-evidence to a fast median that silently disappears."""
    at = _stats(
        [
            (_comp(address="1 W A St", dom=90, censored=True, removal_class=None), 0.0),
            (_comp(address="2 W B St", dom=45, censored=True, removal_class=None), 0.0),
            (_comp(address="3 W C St", dom=60, censored=True, removal_class=None), 0.0),
            (_comp(address="4 W D St", dom=45, censored=True, removal_class=None), 0.0),
            (_comp(address="5 W E St", dom=20), 0.0),
        ]
    )["at"]
    assert at.censored_floors == [45, 45, 60, 90], "ascending, duplicates kept"
    assert len(at.censored_floors) == 4
    assert at.leased_dom_median == 20.0


def test_a_comp_flagged_censored_and_removal_classed_is_never_double_counted() -> None:
    """A contradictory input — censored (still active) yet carrying a removal
    class. The two readings disagree about the most important distinction in
    the system, and whatever the resolution is, the comp must not appear in
    BOTH the leased set and the floors: that would let one listing raise the
    lease count *and* supply a floor arguing the lease count is optimistic.

    ⚠ Reachability, measured (`ws1-real`, 2026-07-29): **0 comps** are censored
    with a non-`None` `removal_class` — F4-S8's classifier is the guarantor and
    it currently holds. So this is a latent robustness gap, not a live wrong
    number. It is pinned anyway because `bucket_stats` tests `.removal_class`
    and `.censored` on two independent lines with no mutual exclusion between
    them, so if F4-S8 ever regresses, the double count is silent and lands on
    the one statistic NORTH_STAR calls the most important in the system.
    """
    at = _stats([(_comp(dom=50, censored=True, removal_class="confirmed"), 0.0)])["at"]
    in_leased = at.leased_dom_median is not None
    in_floors = at.censored_floors == [50]
    assert in_leased != in_floors, (
        f"censored+confirmed comp: in leased set={in_leased}, in floors={in_floors} — "
        f"it must be in exactly one. NORTH_STAR: censored comps are never counted "
        f"as leased, so the expected resolution is floors only."
    )
    assert not in_leased, "censored wins: a censored comp is never counted as leased"


# ==========================================================================
# P8 — selection. §150 says "over SELECTED comps", so a deselected comp is
#      evidence for nothing.
# ==========================================================================


def test_excluded_comps_enter_no_count_no_statistic_and_no_floor_list() -> None:
    pairs = [
        (_comp(address="1 W A St", dom=20), 0.0),
        (_comp(address="2 W B St", dom=500), 0.0),
        (_comp(address="3 W C St", dom=999, censored=True, removal_class=None), 0.0),
    ]
    at = _stats(pairs, included=[True, False, False])["at"]
    assert at.count == 1, "only the selected comp is counted"
    assert at.comp_keys == ["key-0"], "the count must agree with the click-through set"
    assert at.leased_dom_median == 20.0, "the deselected 500-day lease must not enter"
    assert at.leased_dom_max == 20
    assert at.censored_floors == [], (
        "a deselected censored comp is not this bucket's counter-evidence"
    )


def test_count_always_equals_the_length_of_the_click_through_set() -> None:
    """F10's evidence-first invariant: "every count clicks through to its
    comps". A count that disagrees with `comp_keys` is a number the user cannot
    verify — the exact thing NORTH_STAR forbids.
    """
    pairs = [
        (_comp(address="1 W A St", dom=20), -0.30),
        (_comp(address="2 W B St", dom=25), 0.00),
        (_comp(address="3 W C St", dom=30), 0.00),
        (_comp(address="4 W D St", dom=35, censored=True, removal_class=None), +0.30),
        (_comp(address="5 W E St", dom=40, removal_class="pending"), +0.30),
    ]
    stats = _stats(pairs)
    for bucket_id in BUCKET_IDS:
        s = stats[bucket_id]
        assert s.count == len(s.comp_keys), f"{bucket_id}: {s.count} != {len(s.comp_keys)}"
    assert sum(stats[b].count for b in BUCKET_IDS) == 5
    assert len({k for b in BUCKET_IDS for k in stats[b].comp_keys}) == 5, (
        "no comp may appear in two buckets"
    )


def test_no_comps_at_all_yields_three_fully_null_buckets_not_an_error() -> None:
    stats = {s.id: s for s in bucket_stats([], [], [], [], [], None, 4.0)}
    assert list(stats) == list(BUCKET_IDS)
    for s in stats.values():
        assert s.count == 0
        assert s.leased_dom_median is None
        assert s.leased_dom_min is None
        assert s.leased_dom_max is None
        assert s.cut_before_lease_rate is None
        assert s.censored_floors == []
        assert s.comp_keys == []
        assert s.provisional_count == 0
        assert s.withdrawal_suspect_count == 0


def test_mismatched_input_lengths_raise_rather_than_silently_truncating() -> None:
    """Five parallel sequences are zipped. A caller bug that passes 3 comps and
    2 premiums must be loud: silently dropping the third comp is a bucket
    statistic computed over less evidence than the caller believes, which is
    the same failure shape as queue row 13c's short paginated pull.
    """
    comps = [_comp(address=f"{i} W A St", dom=20) for i in range(3)]
    with pytest.raises(ValueError):
        bucket_stats(comps, ["a", "b", "c"], [0.0, 0.0], [True] * 3, ["at"] * 3, None, 4.0)


# ==========================================================================
# P9 — the flag counts that ride along on BucketStat.
# ==========================================================================


def test_provisional_and_withdrawal_suspect_counts_are_marks_not_exclusions() -> None:
    """NORTH_STAR: a provisional lease is "counted but marked"; a
    withdrawal-suspect flag is "display-only; the human makes the call" and is
    explicitly NOT grounds for automatic exclusion. So both comps must still be
    inside the leased median.
    """
    at = _stats(
        [
            (_comp(address="1 W A St", dom=20, removal_class="provisional"), 0.0),
            (_comp(address="2 W B St", dom=30, removal_class="confirmed", withdrawal_suspect=True), 0.0),
            (_comp(address="3 W C St", dom=40, removal_class="confirmed"), 0.0),
        ]
    )["at"]
    assert at.provisional_count == 1
    assert at.withdrawal_suspect_count == 1
    assert at.leased_dom_min == 20, "the provisional comp is counted, not dropped"
    assert at.leased_dom_max == 40
    assert at.count == 3


def test_withdrawal_suspect_count_includes_censored_and_pending_members() -> None:
    """The flag is a property of the listing's history, not of its lease
    outcome, so it is a count over MEMBERS — unlike the DOM statistics, which
    are over the leased subset. Conflating the two denominators is easy and
    invisible.
    """
    at = _stats(
        [
            (_comp(address="1 W A St", dom=20, removal_class="pending", withdrawal_suspect=True), 0.0),
            (_comp(address="2 W B St", dom=30, censored=True, removal_class=None, withdrawal_suspect=True), 0.0),
            (_comp(address="3 W C St", dom=40, removal_class="confirmed"), 0.0),
        ]
    )["at"]
    assert at.withdrawal_suspect_count == 2
    assert at.leased_dom_median == 40.0
