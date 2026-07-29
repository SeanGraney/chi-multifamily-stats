"""Premium — a comp's own $/sqft against its own cohort's median $/sqft.

    premium = psf / cohort_median_psf − 1

NORTH_STAR: this is **time-local**. The comparison is inside one listing year,
so no drift adjustment is involved and none belongs here. Premiums from
different cohort years are not directly comparable without adjustment — which
is precisely why the *anchor* (F8-S1) is drift-adjusted and this is not.

Where this stage lives is a ruling, not an accident (ADR-001 §2.1): the
`add-pipeline-stage` skill puts "premium" in the once-per-pull chain, and it
cannot live there, because the cohort median is taken over the **selected**
comps (F4-S5 [INVARIANT]) and therefore changes every time the user toggles a
comp. `psf` — a property of the record — stays in record shaping; `premium`
moves here, to the per-request half.

Signature (ADR-001 §2.2): plain values in, plain values out. The stage
receives psfs, years, and medians — and nothing else, so nothing else can
influence a premium.

The thin-cohort fallback (F4-S5) is deliberately *not* here. Which set a
cohort's median was taken over is a property of the cohort, decided in
`pipeline/cohorts.py`, which already computes both counts and already owns
`CohortStat.basis`; this stage divides by whatever median it is handed and
never learns where that median came from. Keeping it that way is what keeps
this signature down to psfs, years and medians — and a function that was not
handed a comp record cannot reach a comp's outcome (D19a, ADR-001 §2.2,
`tests/unit/test_f4s5_premium_stage_isolation.py`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

__all__ = ["compute_premiums"]


def compute_premiums(
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    cohort_medians: Mapping[int, float],
) -> list[float | None]:
    """One premium per comp, as a ratio (0.04 == +4%).

    `None` — never 0.0 — when the comp has no $/sqft or its cohort has no
    median. A fabricated 0.0 would read as "priced exactly at market", which
    is a claim about a comp we know nothing about.
    """
    if len(psfs) != len(cohort_years):
        raise ValueError(
            f"psfs and cohort_years must have equal length "
            f"(got {len(psfs)} and {len(cohort_years)})"
        )
    premiums: list[float | None] = []
    for psf, year in zip(psfs, cohort_years, strict=True):
        median = cohort_medians.get(year)
        if psf is None or median is None or median <= 0.0:
            premiums.append(None)
        else:
            premiums.append(psf / median - 1.0)
    return premiums
