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

THE THIN-COHORT FALLBACK (F4-S5 [INVARIANT])
--------------------------------------------
"if selected cohort count < min (4), fall back to all *pulled* comps in
cohort and set a flag the UI must surface." The minimum is the
`min_cohort_size` knob (spec §2.3, range 2–10), never a literal 4 — it
arrives as a scalar argument like every other config value (ADR-001 §2.2).

The comparison is strictly `<`: a cohort with exactly `min_cohort_size`
selected comps is *at* the minimum, not below it, and does not fall back.

**Deciding the basis happens here, not in the premium stage.** This stage
already computes both counts and already owns `CohortStat.basis`; premium
then reads its own cohort's basis. That keeps `compute_premiums`' signature
(psfs, years, medians — and nothing else) intact, which is what makes D19a's
target-leakage prohibition a property of the call graph rather than a promise
(ADR-001 §2.2, `tests/unit/test_f4s5_premium_stage_isolation.py`).

A cohort with no evidence at all in *either* set reports `median_psf=None` and
`basis=None`; downstream that means `premium=None`, never a fabricated 0.
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.responses import Basis, CohortStat
from rentcomp.stats.weighted import weighted_median

__all__ = ["FALLBACK_WEIGHT", "basis_by_year", "cohort_medians", "median_by_year"]

#: [DEFAULT] In the fallback set, **every pulled comp counts once.**
#:
#: The story switches the *set*, not the weighting, and says nothing about
#: weights, which leaves three readings. PM ruling (2026-07-29), taken here:
#: the fallback exists precisely *because* the user's selection is
#: insufficient evidence, so importing that same user's weights into the
#: replacement set is self-contradictory. Reusing the effective weights is
#: also excluded by execution — an excluded comp carries weight 0, so the
#: "fallback" would return the selected median it was meant to replace,
#: making it a literal no-op exactly when a cohort is thin *because* comps
#: were excluded (`test_the_fallback_set_includes_comps_the_user_excluded`).
FALLBACK_WEIGHT = 1.0


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
    absent from `comp_keys`, from both counts, and — F4-S5 obligation 5 —
    from the fallback set as well. "Excluded from every median" includes the
    pulled-set one. It is still counted for the user in
    `breakdown.missing_sqft`, where it remains one click away (T-S2).

    `selected_count` is `len(comp_keys)` by construction — the count and the
    evidence list cannot disagree. That holds under the fallback too: the
    fallback changes which set the *median* is taken over, and reports that
    change in `basis`; `comp_keys` stays the user's own selected evidence, so
    F5-S2's "weight 0 leaves every evidence list" is untouched. The fallback
    set is `pulled_count` comps and is reconstructible from the payload (every
    comp carries its `cohort_year` and its `psf`).
    """
    stats: list[CohortStat] = []
    # Sorted, not set-ordered: an explicit total order is what keeps two
    # identical requests byte-identical (ADR-001 §4.5).
    for year in sorted(set(cohort_years)):
        selected_keys: list[str] = []
        selected_psfs: list[float] = []
        selected_weights: list[float] = []
        pulled_psfs: list[float] = []
        for key, psf, comp_year, weight, keep in zip(
            keys, psfs, cohort_years, weights, included, strict=True
        ):
            if comp_year != year or psf is None:
                continue
            pulled_psfs.append(psf)
            if keep and weight > 0.0:
                selected_keys.append(key)
                selected_psfs.append(psf)
                selected_weights.append(weight)

        # Strictly `<`: exactly `min_cohort_size` selected comps is *at* the
        # minimum, not below it (F4-S5's own AC arm, and what a `<=` gets
        # wrong while every number on screen stays correct).
        thin = len(selected_keys) < min_cohort_size
        basis: Basis
        if thin:
            basis = "pulled"
            median = weighted_median(pulled_psfs, [FALLBACK_WEIGHT] * len(pulled_psfs))
        else:
            basis = "selected"
            median = weighted_median(selected_psfs, selected_weights)

        stats.append(
            CohortStat(
                year=year,
                selected_count=len(selected_keys),
                pulled_count=len(pulled_psfs),
                median_psf=median,
                # A basis is a claim about where a number came from, so a
                # cohort with no number makes no claim.
                basis=basis if median is not None else None,
                thin=thin,
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


def basis_by_year(cohorts: Sequence[CohortStat]) -> dict[int, Basis]:
    """The `{year: basis}` mapping each comp's `premium_basis` is read from.

    Deliberately the same shape, and the same "absent rather than
    present-with-`None`" rule, as `median_by_year`: the two mappings are keyed
    on exactly the same years by construction, so a comp's premium and the
    label saying which set it was measured against cannot come from different
    cohorts. That is the disagreement F8-S3 would render as a row marked
    "selected" inside a cohort marked "pulled".
    """
    return {c.year: c.basis for c in cohorts if c.basis is not None}
