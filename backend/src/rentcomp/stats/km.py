"""Weighted Kaplan-Meier product-limit estimator — F11-S2 [INVARIANT].

The statistical core of the price test. A named, standard procedure with a
defined formula: at each distinct event day ``t``, in ascending order,

    S(t) = S(t-) x (1 - d_w(t) / n_w(t))

* ``d_w(t)`` — summed weights of comps that **actually leased** on day ``t``
  (``effective_dom == t`` and not censored). A censored comp never contributes
  an event: NORTH_STAR's single most important distinction is that a censored
  comp's DOM-so-far is a *floor*, not an outcome.
* ``n_w(t)`` — summed weights of every comp still at risk at day ``t``, i.e.
  every comp with ``effective_dom >= t``.
* ``S(t-)`` — 1.0 before the first event day.

Nothing here is fitted or learned (D20). It is one accumulation over sorted
event days; ``numpy`` is a convenience for expressing the risk-set masks, not
a performance requirement, and ``lifelines`` is deliberately absent — it is a
dev-only dependency (D3/D8) that may verify fixtures but may never ship, and
an AST guard over this package asserts exactly that.

THE TIE CONVENTION (PM ruling, F11-S2 dispatch)
-----------------------------------------------
**A comp censored at exactly day ``t`` is still at risk for the event at day
``t``.** Events precede censorings at ties — the standard Kaplan-Meier
convention, and the story names Kaplan-Meier.

It is also the honest direction. A comp censored on day ``t`` was observed
*still vacant through* day ``t``: it genuinely was at risk of leasing that
day. Dropping it from ``n_w(t)`` shrinks the denominator, inflates
``d_w/n_w``, and biases ``S`` downward — i.e. makes the tool **overstate lease
velocity**, which for a pricing tool is the dangerous direction to be wrong
in. So the conservative reading and the textbook reading agree.

This falls out of the definition rather than being a special case: the risk
set is ``effective_dom >= t``, and censoring simply never enters the numerator.

WHAT S(t) MEANS AT AN EVENT DAY (PM ruling)
--------------------------------------------
``S(t) = P(T > t)`` — the share still vacant *at the end of* day ``t``, so the
value at an event day is the **post-drop** one. A comp with
``effective_dom == 14`` sat vacant for 14 days and then leased; it is not
still vacant at day 14. F11-S5's 14/30/45/60 horizon readouts consume
``survival_at`` directly, so reading this backwards would misreport every
horizon by one step.

DAY-0 EVENTS ARE NOT CLAMPED (PM ruling)
-----------------------------------------
``StitchedComp.effective_dom`` is ``ge=0``, so a same-day removal is a real
day-0 event and produces ``S(0) < 1``. This module reports what the data says.
Whether a DOM-0 removal should be classified as a lease at all is a
removal-classification question (F4-S3/F4-S8), not something to paper over
here by silently moving an observation to day 1. The AC's "S(0) = 1" holds in
its general form: **S(t) = 1 for every t strictly before the first event day.**

TRUNCATION
----------
``truncated_at`` is the last time the data actually observed anything — the
largest retained duration, event or censoring floor — and ``None`` when
nothing was observed to lease. It is never beyond ``max(durations)``, so the
curve never makes a claim about time the pull did not see (NORTH_STAR).
F11-S4's stated formula ("min of last event time and largest censoring
floor") is deliberately *not* implemented: QA showed it discards a genuinely
observed event for ``[10 leased, 30 leased, 20 censored]``, and F11-S4
resolves the full formula. See ``expected_days``.

WEIGHTS
-------
F0-S3's locked rulings, held identically here so the two weighted-stats
modules cannot drift apart: weight-0 entries (including ``-0.0``) are excluded
*before* any computation, so a zero-weight comp is exactly equivalent to an
omitted one; a negative weight or a length mismatch is a loud ``ValueError``,
never laundered into a quiet result. Only weight *ratios* carry meaning —
``d_w/n_w`` is scale-free.

This module imports nothing from ``rentcomp`` (matching ``weighted.py`` and
``knn.py``): ``stats/`` is the math layer, it takes plain values and returns a
plain result. Mapping a ``KMEstimate`` onto ``models/responses.py``'s
``KMCurve``/``KMPoint`` belongs in ``pipeline/pricetest.py``, with the rest of
the mapping (CLAUDE.md's three model layers).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

__all__ = ["KMEstimate", "KMStep", "expected_days", "km_estimate", "survival_at"]


@dataclass(frozen=True, slots=True)
class KMStep:
    """One drop in the product-limit estimate, at a day some comp leased."""

    #: An **event** day: some retained, non-censored comp has this
    #: `effective_dom`. A censoring never creates a step.
    day: int
    #: `S(day)`, i.e. **after** this day's drop.
    survival: float
    #: `n_w(day)` — summed weights at risk (`effective_dom >= day`).
    at_risk: float
    #: `d_w(day)` — summed weights that actually leased on exactly this day.
    events: float
    #: Bookkeeping for the render: how many comps were censored in
    #: `(previous_event_day, day]`. Not part of the product-limit formula —
    #: censorings act on the estimate only by leaving the risk set.
    censored_count: int


@dataclass(frozen=True, slots=True)
class KMEstimate:
    """A weighted Kaplan-Meier curve, as event-day steps plus its truncation."""

    #: Event days only, strictly ascending. Empty when nothing leased —
    #: which is a legitimate state (`S(t) = 1` everywhere), not an error.
    #: Deciding that such a cohort cannot be *shown* is F11-S3's job.
    steps: tuple[KMStep, ...]
    #: The last observable time; `None` when there are no events.
    truncated_at: int | None


def km_estimate(
    durations: Sequence[int],
    censored: Sequence[bool],
    weights: Sequence[float] | None = None,
) -> KMEstimate:
    """The weighted product-limit estimate over one set of comps.

    `durations[i]` is comp *i*'s `effective_dom` (a floor when censored),
    `censored[i]` says whether it was still active as of the pull, and
    `weights[i]` is its curation weight. `weights=None` is the unweighted
    estimator (uniform weight 1).

    Returns steps at event days only. See the module docstring for the tie
    convention, the meaning of `S` at an event day, and the weight contract.
    """
    count = len(durations)
    if len(censored) != count:
        raise ValueError(
            f"durations and censored must have equal length "
            f"(got {count} and {len(censored)})"
        )
    if weights is None:
        weights = [1.0] * count
    elif len(weights) != count:
        raise ValueError(
            f"durations and weights must have equal length "
            f"(got {count} and {len(weights)})"
        )

    w = np.asarray(weights, dtype=np.float64)
    if np.any(w < 0.0):
        # F0-S3's locked ruling: a caller bug is loud. `-0.0 < 0.0` is False,
        # so a negative zero passes here and is excluded as a zero below —
        # a zero weight, never a raise.
        raise ValueError("weights must be non-negative")

    days = _whole_days(durations)
    is_censored = np.asarray(censored, dtype=bool)

    # [INVARIANT] weight-0 entries are excluded BEFORE computation (F0-S3), so
    # they cannot create an event day of their own or sit in a risk set.
    keep = w > 0.0
    days, is_censored, w = days[keep], is_censored[keep], w[keep]

    event_days = np.unique(days[~is_censored])
    if event_days.size == 0:
        # Nothing was observed to lease: no product to take. An empty curve
        # with S == 1 everywhere, and no truncation point to state — never a
        # fabricated decay, never a raise.
        return KMEstimate(steps=(), truncated_at=None)

    steps: list[KMStep] = []
    survival = 1.0
    # -1 rather than 0: day 0 is a representable duration (`effective_dom` is
    # `ge=0`), so the first window has to be able to contain it.
    previous_day = -1
    for event_day in event_days:
        at_risk = float(w[days >= event_day].sum())
        events = float(w[(days == event_day) & ~is_censored].sum())
        survival *= 1.0 - events / at_risk
        window = (days > previous_day) & (days <= event_day)
        steps.append(
            KMStep(
                day=int(event_day),
                survival=survival,
                at_risk=at_risk,
                events=events,
                censored_count=int(np.count_nonzero(is_censored & window)),
            )
        )
        previous_day = int(event_day)

    return KMEstimate(steps=tuple(steps), truncated_at=int(days.max()))


def survival_at(estimate: KMEstimate, day: float) -> float:
    """`S(day)` — the share still vacant at the end of `day`.

    A right-continuous step lookup that includes `day`'s own drop (see the
    module docstring). 1.0 strictly before the first event; flat at the last
    step's value forever after — the curve never decays to 0 on its own, and
    `truncated_at` is what states where it stops being informative.
    """
    survival = 1.0
    for step in estimate.steps:  # ascending by construction
        if step.day > day:
            break
        survival = step.survival
    return survival


def expected_days(estimate: KMEstimate, truncated_at: int) -> float:
    """Area under the step function over `[0, truncated_at]`, in days.

    The restricted mean: expected vacant days *under a truncated empirical
    curve*, which is a lower bound on the unrestricted expectation and is
    never valid beyond the observation window (NORTH_STAR — this is why the
    truncation point travels with the number everywhere it is rendered).

    `truncated_at` is a parameter rather than read off `estimate` so a caller
    holding several curves (the drift band holds three) can integrate them all
    over one common window; integrating each over its own would produce a band
    whose edges answer different questions. F11-S4 owns the final formula for
    choosing that window.
    """
    if truncated_at <= 0:
        return 0.0
    area = 0.0
    previous_day = 0.0
    previous_survival = 1.0
    for step in estimate.steps:
        if step.day >= truncated_at:
            break
        area += previous_survival * (step.day - previous_day)
        previous_day, previous_survival = float(step.day), step.survival
    return area + previous_survival * (truncated_at - previous_day)


def _whole_days(durations: Sequence[int]) -> np.ndarray:
    """`durations` as int64, or a loud `ValueError`.

    DOM is a whole-day count (D15) and `effective_dom` is `ge=0`, so a
    fractional or negative duration means something upstream is broken.
    Silently truncating it would put a day on the wire that no comp has.
    """
    values = np.asarray(durations, dtype=np.float64)
    if values.size and (np.any(values < 0.0) or np.any(values != np.floor(values))):
        raise ValueError("durations must be whole, non-negative day counts")
    return values.astype(np.int64)
