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

__all__ = ["SQFT_SUSPECT_THRESHOLD", "compute_premiums", "compute_sqft_suspects"]

#: `[DEFAULT]` (F5-S1 AC6): how far off its own cohort's median $/sqft a comp
#: has to sit before the row asks the user to verify its square footage. The
#: story says "~30%"; the *meaning* is `[INVARIANT]` and this number is not.
#:
#: Measured on the committed real pull at dispatch: 91 of the 485 comps
#: carrying a premium (18.8%) flag at 0.30. The owner tunes this on sight — no
#: test in the suite pins the constant, only that a comp 60% off flags and one
#: 10% off does not.
SQFT_SUSPECT_THRESHOLD = 0.30


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


def compute_sqft_suspects(
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    cohort_medians: Mapping[int, float],
    threshold: float = SQFT_SUSPECT_THRESHOLD,
) -> list[bool]:
    """One "verify this comp's square footage" flag per comp (F5-S1 AC6).

    **What it means** (`[INVARIANT]`): the comp's own $/sqft sits more than
    `threshold` away — *in either direction* — from its **own cohort's** median
    $/sqft. Spec §7: a wrong `squareFootage` silently corrupts a premium, and
    the Zillow link on the row is the verification path.

    Why it is three lines on top of `compute_premiums` rather than a second
    median lookup: `premium = psf / cohort_median − 1`, so "deviates >30% from
    the cohort median" **is** `abs(premium) > 0.30` on that exact median. Any
    other formulation would be a second definition of the same distance, free
    to drift from the number the row displays beside the badge.

    That identity is also why this lives here and not in record shaping. The
    cohort median is taken over the **selected** comps (F4-S5 `[INVARIANT]`),
    so it moves as the user curates — and a flag computed once per pull would
    keep answering against a median nobody is looking at.

    `False`, never `None`, for a comp with no $/sqft or a cohort with no
    median: "we do not know this comp's size" is the *missing-sqft* badge, a
    different statement about a different thing. A non-positive median is
    likewise not a suspicion — it is a median with nothing behind it, and
    `compute_premiums` already answers `None` there rather than dividing.

    Deviation is a **distance**: an sqft typed with an extra digit drives a
    comp's $/sqft far too low, which corrupts its premium exactly as badly as
    one that is too high.
    """
    return [
        premium is not None and abs(premium) > threshold
        for premium in compute_premiums(psfs, cohort_years, cohort_medians)
    ]
