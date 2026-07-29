"""`POST /api/search/plan` — what a search *would* cost, for free (F2-S3).

ARCHITECTURE.md §4 ERRATUM — THIS ROUTE IS AN ADDITION TO THE ROUTE TABLE
--------------------------------------------------------------------------
§4's table lists `POST /api/search` and no preview alongside it. That table is
incomplete, and the gap is forced rather than stylistic (PM ruling A1, F2-S1;
recorded here in the route's own docstring, following the precedent F4-S9 set
for §4's `force_refresh` clause in `api/search.py`):

* F2 step 3 requires the cohort plan and "est. N API calls" to be on screen
  **before** the user submits — spec §6.3, *"Jun 15–30 · 2026, 2025 (2 cohorts)
  · est. N API calls."*
* `POST /api/search` cannot serve that line: it *pulls* on a cache miss, so a
  debounced form driving it would spend the month's calls while the user was
  still typing an address.
* D5/D13 forbid the alternative — the SPA computing the number itself. It is a
  statistic about a plan, and a second implementation of the planner's calendar
  rules in TypeScript is exactly the drift the architecture exists to remove.

With both alternatives closed, a separate non-spending route is the only shape
left. `SearchPlan` deliberately re-uses `SearchResult`'s field names for the
facts they share.

WHY THE F2-S3 [INVARIANT] SURVIVES A SECOND CONSUMER
-----------------------------------------------------
"One function, two consumers, no drift." The function is
`client/planner.py::fetchable_queries`; the two consumers are this route and
`client/pull.py`. Neither counts anything itself — there is no second
implementation of "what does this pull cost" anywhere in the tree, which is the
whole of what the invariant asks for. What the route reports and what the pull
spends are the same number because they are the same call.

WHY THIS ROUTE CANNOT SPEND MONEY, AS A MATTER OF STRUCTURE
------------------------------------------------------------
Not "does not today" — *cannot*. This module imports the planner (pure
arithmetic over dates), the cache-key function, and the manifest reader. It does
**not** import `run_pull`, `RentCastClient`, the ledger, or `httpx`, and it
never writes to disk, so there is no expression in it that could reach the
network or mint a cache entry however its inputs are shaped. `force_refresh` is
accepted on the body (it is the same `SearchRequest`) and is unreachable here
for the same reason: there is nothing for it to switch on.
`tests/unit/test_search_plan_route.py` pins that import list, so a later story
cannot quietly reintroduce the capability.

The one thing this route reads from disk is the manifest, via `pull_status` —
a read, and F4-S9 already documents it as one ("no network, no clock, no ledger
movement"). Without it the form could not tell a first-ever search from one
whose evidence is already paid for, which is the branch F2 step 4 turns into
F3-S2's cache modal.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.pull import pull_ref_for, pull_status
from rentcomp.models.requests import SearchRequest
from rentcomp.models.responses import CohortPlan, SearchPlan
from rentcomp.storage.cache import CacheMissError

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["search"])


@router.post(
    "/search/plan",
    response_model=SearchPlan,
    summary="Preview a search's cohorts and call cost (fetches nothing)",
)
def post_search_plan(request: SearchRequest) -> SearchPlan:
    """Price a search without running it.

    422 for a window the planner refuses — trimming removes whitespace the user
    cannot see, never repairs a month-day they got wrong, so `"02-30"` still
    comes back as something the form can show inline.
    """
    try:
        # Raises for a bed/bath value that is neither a bare count nor a
        # range — including `"4-2"`, which F2-S2's parser refuses rather than
        # silently reordering. The form blocks it first; this is what stops it
        # being a 500 when something else does not.
        search = request.plan_args()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        # `date.today()` is read here and passed down, the same way
        # `api/search.py` supplies it: the planner takes time as data so it is
        # testable at exactly the boundaries the AC cares about, and so the
        # preview and the submit that follows it agree about which year "now"
        # is in.
        plan = plan_pull_queries(date.today(), **search)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    fetchable = fetchable_queries(plan)
    fetchable_years = {query.year for query in fetchable}
    ref = pull_ref_for(**search)

    return SearchPlan(
        pull_ref=ref,
        cache_status=_cache_status(ref),
        # The [INVARIANT] number. Counted, never derived: `2 * years_back`
        # would agree with this most of the time and overcharge the user by two
        # calls on exactly the days a window has not opened yet.
        estimated_calls=len(fetchable),
        # Newest first, matching §6.3's "2026, 2025" wording. `dict.fromkeys`
        # rather than `sorted(set(...))` so the order is the planner's own
        # (`today.year - y` for y in 0..years_back-1) rather than a re-derived
        # one — and every planned cohort is listed, fetchable or not.
        cohorts=[
            CohortPlan(year=year, fetchable=year in fetchable_years)
            for year in dict.fromkeys(query.year for query in plan)
        ],
    )


def _cache_status(pull_ref: str) -> str:
    """What is already on disk for this search: `miss`, `hit` or `stale`.

    A read of the manifest and nothing else. A *corrupt* entry deliberately
    reads as `miss` here rather than raising: the preview's job is to keep a
    live-updating form honest, and taking the whole form down over an unreadable
    cache file would be a worse answer than "this looks like it needs a pull".
    `POST /api/search` still refuses that entry loudly (F4-S9), which is where
    it matters — that is the route that would otherwise re-buy it.
    """
    try:
        return "hit" if pull_status(pull_ref).complete else "stale"
    except CacheMissError:
        return "miss"
    except (OSError, ValueError, KeyError):
        return "miss"
