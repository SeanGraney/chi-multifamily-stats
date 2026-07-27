"""The anchor — F8-S1 / F8-S2.

WHAT THE ANCHOR MEANS (NORTH_STAR): the weighted median of **drift-adjusted**
comp $/sqft × subject sqft — "what a similar unit would rent for today, given
real recent comps adjusted for assumed market movement since they were
listed." It is a market reference point, not a personalised prediction, and
it is never rendered without its sensitivity band.

THE FORMULA (F8-S1 [INVARIANT], thesis-protected — see NORTH_STAR and
SEMANTIC_CHANGE_PROTOCOL.md; this is the meaning, not a coding choice)::

    adjPsf_i = initialPsf_i * (1 + drift)^(currentYear - cohortYear_i)
    anchor_psf = weighted_median(adjPsf, weight)
    anchor_rent = anchor_psf * subject_sqft

The exponent is PER COMP — a comp two cohort years old gets `(1+d)^2` — so
the drift-adjusted median is computed from per-comp adjusted values, never
from a flat multiplier over the plain median (those are different numbers
whenever weights or cohort ages differ). At `drift=0` the exponent term is 1
for every comp regardless of age, so the result is exactly the plain
weighted median (F8-S1 AC2).

`currentYear`: the pull's `as_of` year (`DeriveContext.as_of.year`), PM
ruling — never a wall-clock read, consistent with owner ruling 1
(`pipeline/derive.py`'s docstring: the wall clock is unreachable from here).

Computed once per drift-band point (`drift.low`, `.mid`, `.high`) — the same
per-comp adjustment, run three times — because the sensitivity band has to
carry all the way through (F8-S2 [INVARIANT]).

**Evidence gate**, unchanged from F0-S2: `None` when there is no evidence
(no positive-weight comp with a $/sqft), and `comp_keys` names exactly the
comps a real anchor was computed from — positive-weight, included, with a
$/sqft. A weight-0 comp or a comp with no sqft is never cited as evidence
(T-S2, ADR-001 §1.2).
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.responses import Anchor, Band
from rentcomp.stats.weighted import weighted_median

__all__ = ["anchor"]


def anchor(
    keys: Sequence[str],
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    weights: Sequence[float],
    included: Sequence[bool],
    drift: Band[float],
    subject_sqft: float,
    current_year: int,
) -> Anchor | None:
    """The market reference point for `subject_sqft`, or `None` with no evidence.

    `current_year` is the pull's `as_of` year — see the module docstring.
    """
    evidence: list[tuple[str, float, float, int]] = [
        (key, psf, weight, year)
        for key, psf, weight, keep, year in zip(
            keys, psfs, weights, included, cohort_years, strict=True
        )
        if keep and weight > 0.0 and psf is not None
    ]
    if not evidence:
        return None

    evidence_keys = [key for key, _, _, _ in evidence]
    evidence_weights = [weight for _, _, weight, _ in evidence]

    def _psf_at(drift_pct: float) -> float:
        rate = 1.0 + drift_pct / 100.0
        adjusted = [
            psf * rate ** (current_year - year) for _, psf, _, year in evidence
        ]
        value = weighted_median(adjusted, evidence_weights)
        assert value is not None  # evidence is non-empty by construction above
        return value

    psf_band = Band[float](
        low=_psf_at(drift.low),
        mid=_psf_at(drift.mid),
        high=_psf_at(drift.high),
    )
    rent_band = Band[float](
        low=psf_band.low * subject_sqft,
        mid=psf_band.mid * subject_sqft,
        high=psf_band.high * subject_sqft,
    )
    return Anchor(
        rent=rent_band,
        psf=psf_band,
        drift_pct=drift.mid,
        drift_sensitivity_pts=drift.high - drift.mid,
        subject_sqft=subject_sqft,
        n_comps=len(evidence_keys),
        comp_keys=evidence_keys,
    )
