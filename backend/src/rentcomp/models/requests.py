"""Layer 3, inbound half — the API's request contracts (ADR-001 §1.1).

`DeriveRequest` (the curation state) and `SearchRequest` (the pull's
parameters) both live here; `responses.py` is the outbound half of each.

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

from rentcomp.client.beds_baths import parse_bed_bath_field

__all__ = [
    "DeriveRequest",
    "Filters",
    "SearchParams",
    "SearchRequest",
    "Subject",
    "Weight",
    "WorkspaceState",
]

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


class SearchParams(BaseModel):
    """The comp-net definition that produced one pull's evidence (F2-S1/F4-S9).

    Deliberately the *pull's* parameters and nothing else: the subject's own
    sqft, the drift assumption and every curation knob belong to
    `DeriveRequest`, because none of them change which records come back.
    Keeping them out is what makes the cache key a function of the search
    (F3-S1) rather than of everything the user has touched since.

    `extra="forbid"` for the same reason as `DeriveRequest`: a typo'd field
    here would silently widen or narrow a pull that costs real money.

    Split out of `SearchRequest` by F1-S2 because a stored workspace has to
    carry these values and must *not* carry `force_refresh` — consent to spend
    money is a property of one request, never of saved state. One declaration
    of the eight fields, so the params a workspace remembers and the params a
    search submits cannot drift apart.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    address: str
    #: Miles. Bounded so a slip of a decimal point cannot ask for a pull the
    #: whole city answers.
    radius: float = Field(gt=0.0, le=100.0)
    #: RentCast range syntax, passed through verbatim (`"3"`, `"3:4"`) — only
    #: the caller knows whether an exact match or a range was meant (F2-S2).
    bedrooms: str
    #: Absent means NO `bathrooms` filter on the wire, which is not the same
    #: as an empty one: an empty filter makes RentCast exclude records whose
    #: bathroom count is missing (T-S3 round 2).
    bathrooms: str | None = None
    property_types: list[str] = Field(min_length=1)
    #: F2's 1-5 cohort years.
    years_back: int = Field(ge=1, le=5)
    #: `"MM-DD"`. The seasonal window, identical in every cohort year.
    window_start: str
    window_end: str


class SearchRequest(SearchParams):
    """What a search form submits: the pull's params plus the consent flag.

    `force_refresh` lives here and nowhere else. It is the user's decision, in
    one moment, to re-spend on evidence already on disk — never a property of
    anything persisted, which is why `SearchParams` (what a workspace stores)
    stops one field short of it.
    """

    #: The user's explicit consent to re-spend on evidence already on disk —
    #: set only by the REFRESH click in F3-S2's cache modal. Read by
    #: `POST /api/search` and by nothing else: the F2-S3 preview route accepts
    #: the field (same body) but can no more act on it than it can fetch.
    force_refresh: bool = False

    def plan_args(self) -> dict:
        """This request in the planner's own argument names, whitespace trimmed.

        One translation, both consumers — `POST /api/search` and
        `POST /api/search/plan` — because the preview's `pull_ref` and call
        count only mean anything if they describe the pull the submit would
        actually perform. Two copies of this mapping would be the same drift
        F2-S3 is [INVARIANT] against, one layer up.

        Trimming is not cosmetic, and it is deliberately on *this* side of the
        boundary (PM ruling 3, F2-S1):

        * `plan_pull_queries` rejects `"06-01 "` outright and that strictness is
          correct — a month-day the user got wrong must not be repaired — so the
          space a form leaves behind has to come off before it gets there.
        * F3-S1's cache key is computed over these exact strings, so a padded
          address would make the user pay twice for one search.

        Trimming is not laundering: `"13-01"` and `"02-30"` still reach the
        planner unchanged and still raise, which is what the routes turn into a
        422 the form can show inline.

        A whitespace-only `bathrooms` resolves to `None` (no filter) rather than
        to `""`. The two are not interchangeable on the wire: T-S3 round 2 found
        that an *empty* bathrooms filter makes RentCast exclude records whose
        bathroom count is missing, so `""` would silently narrow the pull.

        Raises `ValueError` for a bed/bath value that is neither — which both
        routes turn into a 422 the form can show inline.
        """
        return {
            "address": self.address.strip(),
            "radius": self.radius,
            "bedrooms": _rentcast_range_field(self.bedrooms, field="bedrooms"),
            "bathrooms": _rentcast_range_field(self.bathrooms, field="bathrooms"),
            "property_types": [value.strip() for value in self.property_types],
            "years_back": self.years_back,
            "window_start_mmdd": self.window_start.strip(),
            "window_end_mmdd": self.window_end.strip(),
        }


def _rentcast_range_field(raw: str | None, *, field: str) -> str | None:
    """A bed/bath value in RentCast query syntax, whichever form it arrived in.

    This is where F2-S2's parser finally gets wired (QUEUE 2026-07-27: "the
    function is correctly unwired since no consuming route or form exists yet —
    that's F2-S1's job"). Two shapes reach this wire and both are legitimate:

    * **The user's hyphen form** (`"1-3"`), which is what a search form yields
      and what `parse_bed_bath_field` exists to convert. It must be converted
      *here*, in Python: `client/query.py` is "the single source of the syntax
      that spends money" — eight paid-for live calls bought the colon/pipe
      rules — and a `"1-3".replace("-", ":")` in TypeScript would be a second
      source of it, out of reach of every test that protects the first.
    * **Colon syntax already** (`"3:4"`), which is the documented wire contract
      of this field and what every existing caller sends. Passed through
      untouched, so this function is idempotent on the parser's own output.

    A malformed value raises `ValueError` naming the field and the input;
    `"4-2"` raises too, because the parser refuses to reorder rather than
    silently swapping (F2-S2 PM ruling 2). The form catches both before submit;
    the routes translate whatever gets past it into a 422 rather than a 500.

    Blank means "no filter", never `""` — see `plan_args`.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if ":" in stripped:
        # Already this function's own output. Deliberately not re-validated:
        # `rentcast_range` is what produced it, and re-parsing colon syntax
        # here would put a second reading of that syntax in the tree — the one
        # thing this function exists to avoid.
        return stripped
    return parse_bed_bath_field(stripped, field=field)


class WorkspaceState(DeriveRequest):
    """The body of `PUT /api/workspaces/{key}` — one saved workspace (F1-S2).

    Literally `DeriveRequest` plus the search that produced the evidence it
    curates. Inheritance rather than a parallel model on purpose: "restored
    exactly as last left" means the thing that comes back out of the store is
    the thing `POST /api/derive` takes, so a field that exists on one and not
    the other is a field the user can lose.

    **Why `search` has to be here at all.** F1-S1's recents table renders
    address / specs / radius per row, and none of those is recoverable from
    anything else on disk: the cache key is a one-way SHA256 of the search
    params (F3-S1) and the manifest holds only `as_of`, the window and the
    per-query record. So the workspace stores them at save time or they are
    gone. Optional, because a client that has not got them (or does not care)
    must still be able to save curation — losing a table column is not a
    reason to lose the user's work.
    """

    search: SearchParams | None = None
