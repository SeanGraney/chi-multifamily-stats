"""Membership — which of `included | excluded | filtered` each comp is in.

F7-S1 [INVARIANT]: `included + excluded + filtered == pulled`. That holds here
by construction, because this function assigns each comp exactly one label and
the breakdown counts are derived from these labels rather than counted
separately. A filter never removes a comp from the payload — it re-labels it,
so the breakdown can always click through to the comps it counted.

SCOPE (F0-S2): the three filters named in ADR-001 §1.1 are applied literally.
F7-S1 owns the full semantics — filter presets, reset behaviour, and how
`include_overrides` is populated by the UI.

[DEFAULT] Precedence when a comp is both filtered out and weight-0: it is
labelled `filtered`. Rationale: the filter is the *later*, coarser statement
of intent ("do not show me these at all"), and the label the user is looking
for when they ask "where did that comp go" is the filter that removed it. The
partition is valid under either precedence; this one is a judgment call, and
F7-S1 may reverse it without touching anything else.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from math import asin, cos, radians, sin, sqrt
from typing import Literal

from rentcomp.models.domain import StitchedComp
from rentcomp.models.requests import Filters, Subject

__all__ = [
    "MembershipState",
    "classify_membership",
    "distances_mi",
    "haversine_miles",
]

MembershipState = Literal["included", "excluded", "filtered"]

#: Mean Earth radius in statute miles. Haversine over a few miles of Chicago is
#: accurate to well under the precision anybody reads off a comp row.
_EARTH_RADIUS_MI = 3958.7613


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in miles between two WGS84 points.

    [DEFAULT] Haversine rather than a projected/geodesic distance: at this
    scale (a few miles) the difference is metres, and this has no dependency.
    """
    phi1, phi2 = radians(lat1), radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = radians(lng2 - lng1)
    h = sin(d_phi / 2.0) ** 2 + cos(phi1) * cos(phi2) * sin(d_lambda / 2.0) ** 2
    return 2.0 * _EARTH_RADIUS_MI * asin(sqrt(min(1.0, h)))


def distances_mi(comps: Sequence[StitchedComp], subject: Subject) -> list[float]:
    """Each comp's distance from the subject, in miles."""
    return [haversine_miles(subject.lat, subject.lng, c.lat, c.lng) for c in comps]


def classify_membership(
    comps: Sequence[StitchedComp],
    keys: Sequence[str],
    weights: Sequence[float],
    distances: Sequence[float],
    filters: Filters,
    include_overrides: Iterable[str],
) -> list[MembershipState]:
    """Label every comp exactly once.

    `include_overrides` is the user's manual "keep this one anyway" — it beats
    every filter, which is what lets a filter change (or reset) leave a
    deliberate re-inclusion standing (F7-S1).
    """
    overrides = frozenset(include_overrides)
    states: list[MembershipState] = []
    for comp, key, weight, distance in zip(comps, keys, weights, distances, strict=True):
        if key not in overrides and _is_filtered_out(comp, distance, filters):
            states.append("filtered")
        elif weight > 0.0:
            states.append("included")
        else:
            states.append("excluded")
    return states


def _is_filtered_out(comp: StitchedComp, distance: float, filters: Filters) -> bool:
    if filters.max_distance_mi is not None and distance > filters.max_distance_mi:
        return True
    if filters.hide_censored and comp.censored:
        return True
    if filters.leased_only and comp.removal_class not in ("provisional", "confirmed"):
        # "Leased only" keeps comps that reached the removal ladder with
        # enough confidence to count. A censored comp is not a slow lease, and
        # a `pending` removal is too recent to trust (NORTH_STAR) — neither is
        # being reclassified here, both are simply out of view.
        return True
    return False
