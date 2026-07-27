"""1-D neighbour retrieval over premium — F11-S1.

D19's default, implemented as written: two lines of numpy, `argsort` with
`kind="stable"` so tied distances break deterministically (by original
index, i.e. insertion order) rather than however an unstable partition
happens to land.

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

import numpy as np

__all__ = ["select_neighbors"]


def select_neighbors(
    premiums: Sequence[float], candidate_premium: float, k: int
) -> list[int]:
    """Indices of the `k` comps nearest the candidate in premium space.

    D19: `d = |premiums - candidate|`, then a stable argsort so ties break
    the same way every time (reproducibility is the AC, not a specific
    winner). Distance is the *only* thing computed here — no comp record, no
    outcome, ever enters this function (D19a; see the module docstring and
    the AST guard in `test_ws1_knn_retrieval.py`).
    """
    if not premiums:
        return []
    distances = np.abs(np.asarray(premiums, dtype=float) - candidate_premium)
    order = np.argsort(distances, kind="stable")
    return [int(index) for index in order[: max(k, 0)]]
