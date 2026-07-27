"""1-D neighbour retrieval over premium — F11-S1.

**STUB (F0-S2).** This module currently pins the *interface* and returns no
neighbours. See `PLACEHOLDER` below. F11-S1 implements retrieval; D19 already
fixes how ("no library, two lines of numpy: ``d = np.abs(premiums -
candidate); idx = np.argsort(d, kind='stable')[:k]``" — `kind="stable"`
satisfies that story's deterministic-tie AC).

WHY THIS MODULE IS DELIBERATELY IGNORANT
----------------------------------------
D19a: the kNN here has exactly one feature (`premium`) and one target (the
outcome pair). Selecting neighbours partly by their outcome is target
leakage — it would find comps that both priced like the candidate *and* took a
similar time to lease, then report that time as a prediction. Circular, and
convincing-looking at n≈40.

So this module takes **plain floats** and imports nothing from `rentcomp`. A
comp record carries the outcome; if a record were in scope here, the leak
would be one attribute access away and only a code review would stand between
us and it. With the record out of scope, Python's own argument binding is the
enforcement: a function that was not handed the outcome cannot reach it.

Retrieval only. The neighbour set goes to the Kaplan-Meier estimator (D8),
which is what preserves "47 days and still counting" instead of averaging a
still-vacant unit in as though it had leased.
"""

from __future__ import annotations

from collections.abc import Sequence

__all__ = ["select_neighbors"]


def select_neighbors(
    premiums: Sequence[float], candidate_premium: float, k: int
) -> list[int]:
    """Indices of the `k` comps nearest the candidate in premium space.

    PLACEHOLDER RULE (F0-S2 → replaced by F11-S1): **returns no indices at
    all**, for any input. Nothing here approximates a neighbourhood: an empty
    result makes the price test honestly evidence-thin (zero neighbours *is*
    "too few in range") instead of fabricating a set of comps that would look
    like retrieved evidence in the UI and in a screenshot.
    """
    return []
