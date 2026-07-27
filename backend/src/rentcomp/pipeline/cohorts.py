"""Cohort medians — the per-listing-year market reference each premium is
measured against.

Premium is **time-local** (NORTH_STAR): a comp's $/sqft relative to *its own
cohort's* median $/sqft at the time it was listed, so no drift math is needed
to compute it. That is why this stage exists at all, and why it runs per
request rather than once per pull: F4-S5 [INVARIANT] defines the median over
the **selected** comps, so it moves when the user toggles a comp
(ADR-001 §2.1's one genuine conflict, and its ruling).

The median itself is F0-S3's locked `weighted_median` — the *lower* weighted
median — reused rather than reimplemented.

SCOPE (F0-S2): the median over the selected set, and the `thin` flag as a
direct reading of the `min_cohort_size` knob. **F4-S4 owns the fallback** —
what a thin cohort falls back *to* (`basis="pulled"`) is that story's
[INVARIANT], so this stage never emits `basis="pulled"` yet. A cohort with no
selected evidence reports `median_psf=None` and `basis=None`; downstream that
means `premium=None`, never a fabricated 0.
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.responses import CohortStat
from rentcomp.stats.weighted import weighted_median

__all__ = ["cohort_medians", "median_by_year"]


def cohort_medians(
    keys: Sequence[str],
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    weights: Sequence[float],
    included: Sequence[bool],
    min_cohort_size: int,
) -> list[CohortStat]:
    """One `CohortStat` per listing year present in the pull, ascending.

    A comp counts toward a cohort's *evidence* only if it has a $/sqft: a comp
    with no `squareFootage` cannot contribute to a $/sqft median, so it is
    absent from `comp_keys` and from both counts. It is still counted for the
    user in `breakdown.missing_sqft`, where it remains one click away (T-S2).

    `selected_count` is `len(comp_keys)` by construction — the count and the
    evidence list cannot disagree.
    """
    stats: list[CohortStat] = []
    # Sorted, not set-ordered: an explicit total order is what keeps two
    # identical requests byte-identical (ADR-001 §4.5).
    for year in sorted(set(cohort_years)):
        selected_keys: list[str] = []
        selected_psfs: list[float] = []
        selected_weights: list[float] = []
        pulled_count = 0
        for key, psf, comp_year, weight, keep in zip(
            keys, psfs, cohort_years, weights, included, strict=True
        ):
            if comp_year != year or psf is None:
                continue
            pulled_count += 1
            if keep and weight > 0.0:
                selected_keys.append(key)
                selected_psfs.append(psf)
                selected_weights.append(weight)

        median = weighted_median(selected_psfs, selected_weights)
        stats.append(
            CohortStat(
                year=year,
                selected_count=len(selected_keys),
                pulled_count=pulled_count,
                median_psf=median,
                basis="selected" if median is not None else None,
                thin=len(selected_keys) < min_cohort_size,
                comp_keys=selected_keys,
            )
        )
    return stats


def median_by_year(cohorts: Sequence[CohortStat]) -> dict[int, float]:
    """The `{year: median_psf}` mapping `compute_premiums` consumes.

    Years with no median are absent rather than present-with-`None`: the
    premium stage then has nothing to divide by, which is exactly the
    condition under which a premium must be `None`.
    """
    return {c.year: c.median_psf for c in cohorts if c.median_psf is not None}
