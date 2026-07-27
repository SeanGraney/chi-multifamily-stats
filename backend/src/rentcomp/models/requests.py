"""Layer 3, inbound half — the `/api/derive` request contract (ADR-001 §1.1).

`responses.py` is the outbound half. Both codegen into TypeScript (D12), so
what is expressible here is exactly what a client may send.

Four decisions restated from ADR-001 §1.1, because each one is load-bearing
and each one is a thing a future change might quietly undo:

* **Selection IS the weight.** There is no `selections` field, and
  `extra="forbid"` makes adding one client-side an error rather than a
  silently ignored second source of truth (F5-S2 [INVARIANT]: "toggle-off ≡
  weight 0").
* **Comps are addressed by normalized address+unit** (`pipeline.keys.comp_key`),
  never by listing id — ids churn between pulls and a curation state keyed on
  a churning id silently loses the user's work (F13-S1 [INVARIANT]).
* **Filters are parameters, not a pre-filtered comp list.** The server must be
  able to compute `included + excluded + filtered == pulled` in one place
  (F7-S1).
* **`pull_ref` addresses server-held evidence** rather than the client
  uploading comps. NORTH_STAR requires every number to trace to real observed
  comps the server holds; it also avoids re-uploading ~40KB every 150ms during
  a slider drag.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DeriveRequest", "Filters", "Subject", "Weight"]

#: A curation weight. Non-negative: a negative weight has no meaning in this
#: system and would silently corrupt every weighted statistic downstream, so
#: it is rejected at the wire (422) rather than laundered into a plausible
#: number — the same "caller bugs are loud" convention F0-S3 locked for
#: `weighted_median`.
Weight = Annotated[float, Field(ge=0.0)]


class Filters(BaseModel):
    """The F7-S1 filter set. Filters *re-label* comps, never remove them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_distance_mi: float | None = Field(default=None, ge=0.0)
    hide_censored: bool = False
    #: Keep only comps that reached the removal ladder (provisional/confirmed).
    #: The field name is the UI's word; the semantics are "removed with enough
    #: confidence to count", never "censored counts as leased".
    leased_only: bool = False


class Subject(BaseModel):
    """The unit being priced. Not a comp: it is never evidence for itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    lat: float
    lng: float
    sqft: float = Field(gt=0.0)
    beds: float = Field(ge=0.0)
    baths: float = Field(ge=0.0)


class DeriveRequest(BaseModel):
    """The complete curation state — the whole input to one derivation pass.

    Frozen so no stage can mutate the request and leave a footprint for the
    next one (ADR-001 §4.3); `extra="forbid"` so a typo'd field is a loud 422
    rather than curation that silently fails to apply.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Cache key of the immutable raw pull (F3-S1). Resolved through the
    #: `PullLoader` seam; unknown refs are a clean 404, not a 500 — a stale
    #: workspace pointing at a deleted pull is a normal condition.
    pull_ref: str
    subject: Subject
    #: comp_key → weight. Absent ⇒ the defaulting rule in `pipeline.weights`
    #: (1.0, or 0.0 for a comp with no sqft). 0 means excluded-but-visible.
    weights: dict[str, Weight] = Field(default_factory=dict)
    #: Manual re-includes that survive a filter change (F7-S1).
    include_overrides: list[str] = Field(default_factory=list)
    filters: Filters = Field(default_factory=Filters)
    #: Annual drift assumption in **percentage points** (7.0 == +7%/yr), the
    #: same unit the slider shows. Negative is legal: markets go down.
    drift_pct: float = 0.0
    #: `None` on the Results view — no price has been tested yet.
    candidate_rent: float | None = Field(default=None, gt=0.0)
