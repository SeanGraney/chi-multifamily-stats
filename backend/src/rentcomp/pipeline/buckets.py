"""Premium buckets — F10-S1.

A bucket is a **premium-percentage cutoff** (±`bucket_half_width_pct`)
translated to a live dollar figure through the current anchor (NORTH_STAR).
The percentage definition is stable; the dollar boundaries move with the
anchor, and therefore arrive as a `Band` because the anchor is a band.

Buckets never interpolate. An empty bucket carries `None` statistics and
renders as a dash, never an estimate.

WHAT IS REAL IN F0-S2
---------------------
* the three buckets, always present, in render order;
* their premium boundaries, read from the `bucket_half_width_pct` knob (a
  percentage-points knob, converted to a ratio exactly once — here);
* **membership**: which comps fall in which bucket, by premium alone;
* the dollar translation of the boundaries through the anchor;
* the flag counts that are counts rather than statistics (provisional,
  withdrawal-suspect) and the censored DOM *floors*, which are listed
  separately from any leased statistic and never mixed into one.

WHAT F10-S1 ADDS — the outcome statistics
------------------------------------------
`leased_dom_median/min/max` and `cut_before_lease_rate`, over the LEASED set
of a bucket's members: `removal_class in ("provisional", "confirmed")` —
pending removals are *excluded entirely* (not merely marked, NORTH_STAR),
and censored comps are never counted as leased (their DOM-so-far is a floor,
reported separately in `censored_floors`). An empty leased set reports
`None` on every outcome statistic — never `0`/`0.0`, which would read as a
real computed value for zero evidence.
"""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import Anchor, Band, BucketId, BucketStat

__all__ = ["BUCKET_IDS", "bucket_of", "bucket_stats", "premium_bounds"]

#: Render order, always all three.
BUCKET_IDS: tuple[BucketId, ...] = ("below", "at", "above")

#: The removal classes counted as "leased" for outcome statistics (F10-S1).
#: `pending` is excluded entirely; `None` (still active/censored) never
#: reaches this set at all.
_LEASED_CLASSES = frozenset({"provisional", "confirmed"})


def premium_bounds(
    bucket_id: BucketId, half_width_pct: float
) -> tuple[float | None, float | None]:
    """The (min, max) premium **ratios** of a bucket. `None` means unbounded.

    The knob is in percentage points (4.0 == ±4%, spec §2.3) and `premium` is
    a ratio, so the conversion happens exactly once — here — rather than at
    each comparison site where one of them could be forgotten.
    """
    half_width = half_width_pct / 100.0
    if bucket_id == "below":
        return (None, -half_width)
    if bucket_id == "at":
        return (-half_width, half_width)
    return (half_width, None)


def bucket_of(premium: float | None, half_width_pct: float) -> BucketId | None:
    """Which bucket a premium falls in; `None` when there is no premium.

    Boundaries are inclusive on the "at" side: a comp priced exactly at the
    ±4% edge is at market, not above or below it. A comp with no premium (no
    sqft, or no cohort median) has no bucket at all — it is not "at market".
    """
    if premium is None:
        return None
    half_width = half_width_pct / 100.0
    if premium < -half_width:
        return "below"
    if premium > half_width:
        return "above"
    return "at"


def bucket_stats(
    comps: Sequence[StitchedComp],
    keys: Sequence[str],
    premiums: Sequence[float | None],
    included: Sequence[bool],
    buckets: Sequence[BucketId | None],
    anchor_value: Anchor | None,
    half_width_pct: float,
) -> list[BucketStat]:
    """One `BucketStat` per bucket, always three, in render order.

    Only *included* comps populate a bucket: an excluded or filtered comp is
    not evidence for anything, and the count has to agree with the comps the
    user can click through to.
    """
    stats: list[BucketStat] = []
    for bucket_id in BUCKET_IDS:
        members = [
            (comp, key)
            for comp, key, keep, comp_bucket in zip(
                comps, keys, included, buckets, strict=True
            )
            if keep and comp_bucket == bucket_id
        ]
        member_keys = [key for _, key in members]
        premium_min, premium_max = premium_bounds(bucket_id, half_width_pct)
        leased = [comp for comp, _ in members if comp.removal_class in _LEASED_CLASSES]
        leased_doms = sorted(comp.effective_dom for comp in leased)
        stats.append(
            BucketStat(
                id=bucket_id,
                premium_min=premium_min,
                premium_max=premium_max,
                dollar_min=_dollars(anchor_value, premium_min),
                dollar_max=_dollars(anchor_value, premium_max),
                count=len(member_keys),
                leased_dom_median=float(median(leased_doms)) if leased_doms else None,
                leased_dom_min=leased_doms[0] if leased_doms else None,
                leased_dom_max=leased_doms[-1] if leased_doms else None,
                cut_before_lease_rate=(
                    sum(1 for comp in leased if comp.cut_history) / len(leased)
                    if leased
                    else None
                ),
                provisional_count=sum(
                    1 for comp, _ in members if comp.removal_class == "provisional"
                ),
                withdrawal_suspect_count=sum(
                    1 for comp, _ in members if comp.withdrawal_suspect
                ),
                censored_floors=sorted(
                    comp.effective_dom for comp, _ in members if comp.censored
                ),
                comp_keys=member_keys,
            )
        )
    return stats


def _dollars(anchor_value: Anchor | None, premium: float | None) -> Band[float] | None:
    """A premium cutoff as dollars, at each drift point of the anchor band.

    `None` on either side of the boundary or the anchor: with no anchor there
    is no dollar figure to state, and an unbounded premium side has no dollar
    edge. Translating a cutoff through the anchor is arithmetic, and arithmetic
    is Python's job (D5) — the view formats what arrives.
    """
    if anchor_value is None or premium is None:
        return None
    return Band[float](
        low=anchor_value.rent.low * (1.0 + premium),
        mid=anchor_value.rent.mid * (1.0 + premium),
        high=anchor_value.rent.high * (1.0 + premium),
    )
