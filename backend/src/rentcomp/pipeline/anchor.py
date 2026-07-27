"""The anchor — F8-S1 / F8-S2.

**STUB (F0-S2).** What is real here is the *evidence gate* and the *shape*;
the number is a flagged placeholder. See `PLACEHOLDER_ANCHOR_PSF`.

WHAT THE ANCHOR MUST MEAN (NORTH_STAR, so that the stub cannot be mistaken
for it): the weighted median of **drift-adjusted** comp $/sqft × subject sqft
— "what a similar unit would rent for today, given real recent comps adjusted
for assumed market movement since they were listed." It is a market reference
point, not a personalised prediction, and it is never rendered without its
sensitivity band.

Two things that ARE real in F0-S2, because they are seams the later story
must not have to re-invent:

* **`None` when there is no evidence.** No positive-weight comp with a $/sqft
  ⇒ no anchor, and therefore no bucket dollar boundaries and no candidate
  premium. "No evidence" is a state, not a zero.
* **`comp_keys`** names exactly the comps a real anchor would be computed
  from: positive-weight, included, with a $/sqft. A weight-0 comp or a comp
  with no sqft is never cited as evidence (T-S2, ADR-001 §1.2).
"""

from __future__ import annotations

from collections.abc import Sequence

from rentcomp.models.responses import Anchor, Band

__all__ = ["PLACEHOLDER_ANCHOR_PSF", "anchor"]

#: PLACEHOLDER RULE (F0-S2 → replaced by F8-S1): the anchor $/sqft is the
#: sentinel **1.0**, identically at all three drift points, so that
#: `anchor.rent == subject.sqft` exactly and the band is degenerate.
#:
#: Chosen to be unmistakable rather than plausible. $1.00/sqft/month is not a
#: number this market produces (real Chicago small-multifamily is ~$2-3), and
#: `rent == sqft` is a giveaway on sight. A "nearly right" placeholder — say,
#: the undrifted weighted median of $/sqft — would have been worse than
#: useless: it looks exactly like the real statistic, and it is *structurally*
#: wrong in a way that adding drift later would not fix, because drift applies
#: per comp by cohort year and the median of adjusted values is not the
#: adjusted median.
#:
#: The degenerate band (low == mid == high) is the second signal: a sensitivity
#: band whose edges are equal is visibly not yet a sensitivity band. And every
#: response carries a `stub_stage` warning naming this stage and F8-S1.
PLACEHOLDER_ANCHOR_PSF = 1.0


def anchor(
    keys: Sequence[str],
    psfs: Sequence[float | None],
    cohort_years: Sequence[int],
    weights: Sequence[float],
    included: Sequence[bool],
    drift: Band[float],
    subject_sqft: float,
) -> Anchor | None:
    """The market reference point for `subject_sqft`, or `None` with no evidence.

    The full parameter list is F8-S1's, not the stub's: `cohort_years` and
    `drift` are unused today precisely because drift adjustment — the
    substance of that story — is not implemented. They are in the signature so
    the seam is already the right shape, and so this docstring is the only
    thing that has to change when it is filled in.
    """
    evidence = [
        key
        for key, psf, weight, keep in zip(keys, psfs, weights, included, strict=True)
        if keep and weight > 0.0 and psf is not None
    ]
    if not evidence:
        return None

    psf_band = Band[float](
        low=PLACEHOLDER_ANCHOR_PSF,
        mid=PLACEHOLDER_ANCHOR_PSF,
        high=PLACEHOLDER_ANCHOR_PSF,
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
        n_comps=len(evidence),
        comp_keys=evidence,
    )
