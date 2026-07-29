"""`POST /api/search` — what a search form submits to, and the only route that
can spend money (F4-S9).

The edge, and nothing more: it supplies the clock, decides whether this search
may go to the source at all, and translates the orchestrator's refusals into
answers a user can act on. Every decision about *what* to fetch, and every
byte written, belongs to `client/pull.py`.

WHEN THIS ROUTE FETCHES — an ARCHITECTURE.md §4 erratum, corrected here
------------------------------------------------------------------------
§4 says this endpoint "never hits the network unless `force_refresh: true`".
Read literally that makes a first-ever search impossible: `force_refresh` is
set only by the REFRESH click in F3-S2's cache modal, and that modal only
exists when a cache entry does. F2's own flow says what was meant — "user
submits → if a cache exists for identical params → F3; else system runs the
pull (F4)". So (PM ruling, F4-S9):

    cache MISS  → pull. The user has already seen the cost: F2 step 3 shows
                  "est. N API calls" live, before submit, computed by
                  `POST /api/search/plan` (`api/plan.py`) from the same
                  `client/planner.py::fetchable_queries` function this route's
                  own `estimated_calls` is counted with.
    cache HIT / STALE → fetch NOTHING. Return the existing ref and
                  `cache_status`, so F3-S2's modal can offer the choice.
    force_refresh → pull regardless.

*(Erratum on the erratum, F2-S1: this paragraph previously said the preview
number came "from the same `fetchable_queries` count **this route** reports
back", which is only true once a pull has already happened — and F2 step 3 is
by definition before that. QA caught it. The preview is a separate,
non-spending route; what the two share is the function, not the response.)*

"Stale" (an entry with a gap in it) deliberately sits on the no-fetch side:
completing a partial pull costs real calls, so it is the user's decision, not
a side effect of reopening a search. The response says exactly what finishing
would cost (`calls_to_complete`).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from rentcomp.client.pull import (
    InsufficientCallBudgetError,
    PullOutcome,
    pull_ref_for,
    pull_status,
    run_pull,
)
from rentcomp.client.rentcast import RentCastError
from rentcomp.models.requests import SearchRequest
from rentcomp.models.responses import SearchResult
from rentcomp.storage.cache import CacheMissError

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["search"])

#: Being out of calls is a known condition with a specific user action
#: attached ("narrow the search, or wait for the month to reset") — not a
#: crash, and not an upstream 429, which means something the far end did.
CALL_BUDGET_STATUS = 402


@router.post("/search", response_model=SearchResult, summary="Run (or resolve) a pull")
def post_search(request: SearchRequest) -> SearchResult:
    """Resolve a search to a `pull_ref`, fetching only if it must (see module
    docstring).

    422 for a window/params the planner refuses, 402 when the month cannot
    afford the pull, 400 when live mode is configured wrong. A per-query
    failure is NOT an error: the answer is a 200 whose `complete` is false and
    whose `missing` names the windows that never arrived (§5a — an incomplete
    first pull is usable, with the gap named loudly).
    """
    try:
        # A bed/bath value the parser refuses (`"4-2"`, `"abc"`) is the user's
        # input being wrong, not the tool — 422, not the 500 an uncaught
        # ValueError out here would be.
        search = request.plan_args()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    ref = pull_ref_for(**search)

    try:
        prior: PullOutcome | None = pull_status(ref)
    except CacheMissError:
        prior = None
    except (OSError, ValueError, KeyError) as exc:
        # An entry that exists but cannot be read is NOT the same as no entry.
        # Treating it as a miss would re-pull — spending real calls to work
        # around a corrupt file — so it is surfaced instead, naming the entry
        # so the user can delete it (spec §7; F0-S5's ConfigError precedent in
        # `api/derive.py`).
        raise HTTPException(
            status_code=500,
            detail=(
                f"the cache entry for this search ({ref}) exists but could not be read: "
                f"{exc}. Nothing was fetched. Delete <RENTCOMP_HOME>/cache/{ref} to start "
                "that search over."
            ),
        ) from exc

    cache_status = "miss" if prior is None else ("hit" if prior.complete else "stale")

    if prior is not None and not request.force_refresh:
        outcome = prior
    else:
        try:
            outcome = run_pull(
                **search,
                force_refresh=request.force_refresh,
                # The one legitimate clock reading in the whole pull path. It
                # becomes the manifest's `as_of`, which is the pipeline's only
                # "now" (owner ruling 1) — data from here down, never ambient.
                today=date.today(),
            )
        except InsufficientCallBudgetError as exc:
            raise HTTPException(
                status_code=CALL_BUDGET_STATUS,
                detail={
                    "message": str(exc),
                    "required": exc.required,
                    "remaining": exc.remaining,
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RentCastError as exc:
            # Reachable only from the pre-flight refusals (a missing key, live
            # mode requested without the flag) — a per-query failure returns a
            # partial outcome instead. Both are the user's to fix, and both
            # name what to fix, so 400 rather than 500.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return SearchResult(
        pull_ref=outcome.pull_ref,
        cache_status=cache_status,
        # `outcome.fetchable` IS `len(fetchable_queries(plan))` — never
        # recomputed here, so the estimate the user is shown and the count the
        # cap is enforced on cannot drift (F2-S3 [INVARIANT]).
        estimated_calls=outcome.fetchable,
        calls_spent=outcome.calls_spent,
        complete=outcome.complete,
        missing=list(outcome.missing),
        calls_to_complete=outcome.calls_to_complete,
    )
