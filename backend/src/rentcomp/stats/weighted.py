"""Weighted order statistics — F0-S3 [INVARIANT].

Shared by the anchor, cohort medians, kNN aggregation, and the KM estimator.

Locked definition (the *lower* weighted median, per the story text):

    weighted_median(values, weights) is the smallest value v, taken over
    entries with weight > 0, whose cumulative weight (summed in ascending
    value order) is >= 50% of the total weight. Never the upper variant,
    never interpolated — those are different statistics.

``weighted_quantile(values, weights, q)`` generalizes the same rule with
``q x total`` in place of the 50% threshold, so:

* ``q=0.5`` agrees exactly with ``weighted_median`` (it *is* the median —
  the median delegates here),
* ``q=0`` returns the minimum and ``q=1`` the maximum of the positive-weight
  values,
* the result is always an element of the positive-weight inputs, or ``None``.

Contract:

* Weight-0 entries are excluded *before* any computation — a zero-weight
  entry can never shift any quantile, including q=0 and q=1.
* Empty input, or input whose weights are all zero, returns ``None`` —
  never NaN, never a raise. ``None`` means "no evidence".
* Caller bugs are loud, not laundered into ``None`` (PM-confirmed):
  a negative weight, a ``q`` outside [0, 1], or ``len(values) !=
  len(weights)`` each raise ``ValueError``.
* NaN/inf in ``values`` is deliberately uncontracted here — the domain
  layer guarantees finite inputs before this module is reached.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

__all__ = ["weighted_median", "weighted_quantile"]


def weighted_quantile(
    values: Sequence[float], weights: Sequence[float], q: float
) -> float | None:
    """Lower weighted quantile: smallest v with cumulative weight >= q x total.

    Returns an element of the positive-weight ``values`` (no interpolation,
    ever), or ``None`` when no entry has positive weight. See the module
    docstring for the full locked contract.
    """
    if len(values) != len(weights):
        raise ValueError(
            f"values and weights must have equal length "
            f"(got {len(values)} and {len(weights)})"
        )
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1] (got {q!r})")

    v = np.asarray(values, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    if np.any(w < 0.0):
        raise ValueError("weights must be non-negative")

    # [INVARIANT] weight-0 entries are excluded BEFORE computation.
    keep = w > 0.0
    v = v[keep]
    w = w[keep]
    if v.size == 0:
        return None  # no evidence — never NaN, never a raise (AC3)

    order = np.argsort(v, kind="stable")
    v = v[order]
    cum_frac = np.cumsum(w[order])
    # Normalize to cumulative fractions and compare against q directly,
    # rather than comparing raw cumulative weight against q * total. The
    # two are the same real-number comparison; the normalized form makes
    # weight-scale invariance exact in float64 (s*a / s*b is the identical
    # quotient whenever both products are exactly representable, as they
    # are for this project's integer-like weights), and cum_frac[-1] is
    # exactly 1.0 by construction so q=1 always resolves to the maximum.
    cum_frac /= cum_frac[-1]

    # Smallest index whose cumulative fraction is >= q (">=", not ">":
    # cumulative weight landing exactly on q x total qualifies).
    idx = int(np.searchsorted(cum_frac, q, side="left"))
    # cum_frac[-1] == 1.0 >= q guarantees idx < v.size; min() is a pure
    # defensive clamp against pathological (uncontracted) inputs.
    return float(v[min(idx, v.size - 1)])


def weighted_median(
    values: Sequence[float], weights: Sequence[float]
) -> float | None:
    """Lower weighted median: smallest v with cumulative weight >= half the total.

    Exactly ``weighted_quantile(values, weights, 0.5)`` — delegating keeps
    the q=0.5 equivalence true by construction. With uniform weights this
    reproduces the plain *lower* median (``statistics.median_low``).
    """
    return weighted_quantile(values, weights, 0.5)
