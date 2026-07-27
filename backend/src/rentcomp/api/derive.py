"""`POST /api/derive` — the one endpoint the whole UI derives from.

ARCHITECTURE.md §4: one fat endpoint that recomputes everything from scratch,
so "one derivation pass, no stale panels" is structurally true rather than
something careful cache invalidation has to defend. Partial-update endpoints
would reintroduce exactly the staleness F0-S2 forbids.

This module is *the edge*, and its whole job is to be the only place that
touches the world: it loads config, resolves the pull, assembles a
`DeriveContext`, and calls a pure function. It computes no statistic itself
(D5) — a route that did arithmetic would be a second implementation of a
formula, which is the drift risk the architecture exists to remove.

**The endpoint writes nothing.** Workspace autosave is F14-S2's separate
`PUT /api/workspaces/{key}`. POST is used only because the payload is an
object; the operation is semantically safe to retry, and identical bodies
return byte-identical responses (AC1).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from rentcomp.models.requests import DeriveRequest
from rentcomp.models.responses import DerivedState
from rentcomp.pipeline.derive import DeriveContext, derive
from rentcomp.storage.config import load_config
from rentcomp.storage.pulls import PullNotFoundError, config_digest, load_shaped_pull

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["derive"])


@router.post("/derive", response_model=DerivedState, summary="Derive the full state")
def post_derive(request: DeriveRequest) -> DerivedState:
    """Derive everything from one complete curation state.

    404 when the pull is gone — a stale workspace pointing at a deleted pull
    is a normal condition, not a server error. 422 (from the request model)
    for an unknown field, a `selections` key, or a negative weight: each of
    those is curation that would otherwise fail silently.
    """
    config = load_config()
    try:
        pull = load_shaped_pull(request.pull_ref, config)
    except PullNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    context = DeriveContext(
        config=config,
        comps=pull.comps,
        # Data, not a clock reading (owner ruling 1). The pipeline has no
        # access to the wall clock at all; this is the only date it sees.
        as_of=pull.as_of,
        pull_ref=pull.ref,
        pull_digest=pull.digest,
        config_digest=config_digest(config),
    )
    return derive(request, context)
