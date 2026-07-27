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

WHAT IS STUBBED (F10-S1 owns it) — see `_STUB_OUTCOME_STATS`.
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.domain import StitchedComp
from rentcomp.models.responses import Anchor, Band, BucketId, BucketStat

__all__ = ["BUCKET_IDS", "bucket_of", "bucket_stats", "premium_bounds"]

#: Render order, always all three.
BUCKET_IDS: tuple[BucketId, ...] = ("below", "at", "above")

#: PLACEHOLDER RULE (F0-S2 → replaced by F10-S1): every *outcome* statistic of
#: a bucket — the leased-DOM median/min/max and the cut-before-lease rate — is
#: reported as `None`, in every bucket, however many comps it holds.
#:
#: `None` is not a fabrication: it is the exact value an empty bucket reports,
#: and the view already renders it as a dash. So a stubbed bucket looks like a
#: bucket with no evidence — never like a bucket with an answer. Computing
#: these requires F10-S1's exclusion rules (pendings are *excluded* from
#: aggregates, not merely marked; censored floors are never counted as leased),
#: and those rules are that story's [INVARIANT], not something to guess at
#: here: a wrong-but-plausible median would silently inflate apparent lease
#: velocity, which is the specific failure NORTH_STAR names.
_STUB_OUTCOME_STATS = True


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
        stats.append(
            BucketStat(
                id=bucket_id,
                premium_min=premium_min,
                premium_max=premium_max,
                dollar_min=_dollars(anchor_value, premium_min),
                dollar_max=_dollars(anchor_value, premium_max),
                count=len(member_keys),
                leased_dom_median=None,  # _STUB_OUTCOME_STATS (F10-S1)
                leased_dom_min=None,  # _STUB_OUTCOME_STATS (F10-S1)
                leased_dom_max=None,  # _STUB_OUTCOME_STATS (F10-S1)
                cut_before_lease_rate=None,  # _STUB_OUTCOME_STATS (F10-S1)
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
