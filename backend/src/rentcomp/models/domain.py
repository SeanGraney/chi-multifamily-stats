"""Layer 2 — our truth (ARCHITECTURE.md §3).

`Spell` and `StitchedComp`: fields non-Optional where the pipeline
guarantees them. The DTO→domain transition is where missing data becomes an
explicit flag instead of a silently propagating None.

F0-S2 pins `StitchedComp` because the derivation graph cannot be typed
without its input type: ADR-001 §2.1 names `tuple[StitchedComp, ...]` as the
output of Group A (record shaping: dedupe → spells → stitch → classify →
window → cohort → psf) and the input of Group B (curation derivation, which
is what `/api/derive` runs on every request).

What F0-S2 does **not** do is *produce* these records from raw RentCast
responses — that is the stitcher, F4-S3. Until then, `storage/pulls.py` reads
pre-shaped synthetic pulls through the same seam (`PullLoader`), so replacing
the stub is a change of implementation behind one function, not a change of
type.

The honest promise of Group A (ADR-001 §2.1) is repeated here because it is
what the Optionality below encodes: *every surviving record has a start date,
a DOM, and a censoring flag. It does not promise a `psf`* — 14.7% of the real
pull has no `squareFootage`, and those records must survive into the response
with `psf=None` rather than be dropped or given a fabricated zero.

`Spell` was intentionally absent through F4-S2 — it is the stitcher's
intermediate artifact and nothing in the derivation graph consumes it. F4-S3
adds it here, as that docstring promised: a domain type defined inside
`pipeline/` is our-truth data living in the derivation layer, which is how a
DTO starts leaking past the pipeline boundary. `pipeline.shape` re-exports
the same class object, so nothing that already imports it has to move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["PriceCut", "RemovalClass", "Spell", "StitchedComp"]

#: The removal-confidence ladder (NORTH_STAR, F4-S8). `None` — not a member of
#: this Literal — means *still active*: a censored listing has not been
#: removed, let alone leased. ARCHITECTURE.md §3's `leased` spelling is an
#: erratum (PM ruling, F0-S2); the ladder ends at `confirmed`.
RemovalClass = Literal["pending", "provisional", "confirmed"]


@dataclass(frozen=True, slots=True)
class Spell:
    """One reported listing interval, before stitching (F4-S2/F4-S3).

    ``removed is None`` means the spell was still open when the pull was
    taken — the censored case NORTH_STAR calls the single most important
    distinction in the system. The fields past ``price`` are the record
    attributes the spell was read from; they ride along so the stitcher can
    populate a `StitchedComp` without a second pass over the raw dicts.

    A plain frozen dataclass rather than a `BaseModel`: nothing crosses a
    trust boundary here (its values were already validated at the DTO edge),
    it is constructed once per spell over every record in a pull, and it is
    never serialized. `StitchedComp` above is the model — this is the
    intermediate the stitcher folds into one.
    """

    listed: date
    removed: date | None
    price: float
    address: str
    unit: str | None
    lat: float
    lng: float
    beds: float
    baths: float | None
    sqft: float | None


class PriceCut(BaseModel):
    """One price reduction inside a comp's stitched history (spec §5.5)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on: date
    from_price: float
    to_price: float


class StitchedComp(BaseModel):
    """One comp, after record shaping — the atomic unit the graph derives from.

    Immutable (ADR-001 §4.3): a stage that cannot mutate an upstream artifact
    cannot leave a footprint for the next request.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # --- identity / geography ------------------------------------------------
    address: str
    unit: str | None = None
    lat: float
    lng: float

    # --- the unit itself -----------------------------------------------------
    beds: float
    baths: float | None = None
    #: `None` for the 14.7% of real records with no `squareFootage`.
    sqft: float | None = None
    initial_ask: float

    # --- the outcome (the kNN TARGET — never an input to distance, D19a) ------
    #: Days on market. For a censored comp this is a *floor*, not an observed
    #: outcome, measured against the pull date (owner ruling 1), never today.
    effective_dom: int = Field(ge=0)
    censored: bool
    removal_class: RemovalClass | None = None

    # --- shaping flags -------------------------------------------------------
    cohort_year: int
    #: The chain's stitched-start date (F4-S3's `chain[0].listed`) — carried
    #: here (WS-1a) so `pipeline.keys.disambiguate_keys` can tell two disjoint
    #: chains at the same address+unit apart even when `cohort_year` collides
    #: (real data: 7 of 34 colliding groups share a cohort year). `None` only
    #: for hand-built `StitchedComp`s in tests that construct one directly
    #: without going through `shape_raw_pull` — real shaping always sets it.
    first_listed: date | None = None
    withdrawal_suspect: bool = False
    #: $/sqft >30% off the cohort median (F5-S1 computes it; carried here so
    #: the graph can surface it without re-deriving it per request).
    sqft_suspect: bool = False
    cut_history: tuple[PriceCut, ...] = ()
    relist_count: int = Field(default=0, ge=0)
    gap_days: int = Field(default=0, ge=0)

    @property
    def removed_on(self) -> date | None:
        """The chain's final removal date, or `None` while it is still active.

        F4-S8: the datum the removal ladder is *measured from*. `removal_class`
        is a function of `as_of − removed_on`, so a row that says "pending"
        without being able to say *since when* is an assertion the user cannot
        check — and the story's own row copy ("removed 4d — classifying") is
        that date, rendered.

        A **property**, not a stored field, for the same reason `psf` is one:
        it cannot drift from its inputs. F4-S3 defines `effective_dom` as
        *final removal − first listed*, so for any comp the pipeline shaped,
        `first_listed + effective_dom` **is** the final removal — reconstructed
        here rather than stored beside it, where the two could disagree after a
        change to either. (`pipeline/shape.py::_build_comp` is the one place
        that identity is established; `tests/unit/test_f4s8_removed_on.py`
        pins it against the raw `removedDate` the records actually carried.)

        `None` in exactly two cases, both honest:

        * `censored` — a still-listed unit has not been removed, let alone
          leased (NORTH_STAR's most important distinction). Its `effective_dom`
          is a floor measured to the pull date, so `first_listed +
          effective_dom` would reconstruct the *pull date*, not a removal.
        * `first_listed is None` — a hand-built `StitchedComp` from a test that
          did not go through `shape_raw_pull`. Real shaping always sets it (see
          that field's note); nothing is inferred from its absence.
        """
        if self.censored or self.first_listed is None:
            return None
        return self.first_listed + timedelta(days=self.effective_dom)

    @property
    def psf(self) -> float | None:
        """Initial ask per square foot, or `None` when sqft is unknown.

        A *property*, not a stored field, so it cannot drift from its inputs.
        ADR-001 §2.1's ruling: `psf` is record shaping (a property of the
        record); `premium` is curation derivation (it depends on which comps
        the user selected) and therefore lives in `pipeline/premium.py`.
        """
        if self.sqft is None or self.sqft <= 0.0:
            return None
        return self.initial_ask / self.sqft
