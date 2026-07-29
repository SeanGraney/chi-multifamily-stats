"""`GET /api/workspaces` · `GET/PUT /api/workspaces/{key}` — F1-S2.

ARCHITECTURE.md §4's third row: the recents list, and load/save of one
workspace's curation state. The edge, and nothing more — `storage/workspace.py`
owns the file format and every way it can fail; this module turns those named
failures into answers a user can act on.

**Zero API calls, structurally.** Nothing in this module imports the RentCast
client, the planner or `client/pull.py`. Opening a recent search cannot spend
money because there is no code path from here to the source — not because a
counter happens to read zero. That distinction is the whole AC: `calls_this_month`
never moves in fixture mode, so a route that re-pulled everything would satisfy
a counter-based check and cost real money on the live path.

A gap in a workspace's evidence is *not* permission to go and fill it. §5a says
completing a partial pull costs exactly the remainder — real calls — so it is
the user's decision in F3-S2's modal, never a side effect of clicking a row.

**Reading is never writing.** Neither route writes under `cache/`, and neither
repairs or deletes a workspace file it cannot parse.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from rentcomp.models.requests import WorkspaceState
from rentcomp.models.responses import RecentWorkspace, Workspace
from rentcomp.storage.pulls import pull_exists
from rentcomp.storage.workspace import (
    InvalidWorkspaceKeyError,
    WorkspaceCorruptError,
    WorkspaceDocument,
    WorkspaceEntry,
    WorkspaceNotFoundError,
    list_workspaces,
    load_workspace,
    save_workspace,
)

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["workspaces"])


@router.get("/workspaces", response_model=list[RecentWorkspace], summary="Recent searches")
def get_workspaces() -> list[RecentWorkspace]:
    """The recents index: one row per stored workspace, newest first.

    Home asks for this on mount, so it must answer for *whatever is on disk* —
    an empty store is an empty list (not a 404), and a store with one
    unreadable file is a list with one error row in it, never a broken front
    door. One bad file cannot cost the user every other search they have run.
    """
    return [_row(entry) for entry in list_workspaces()]


@router.get("/workspaces/{key}", response_model=Workspace, summary="Load a workspace")
def get_workspace(key: str) -> Workspace:
    """Restore one curation state exactly as it was last saved.

    404 when nothing is saved under that key — a row the user deleted, or a
    link from an old session, is an ordinary condition. 404 too for a key that
    could never address a file (a traversal attempt); the two are deliberately
    indistinguishable, so a probe learns nothing about what is on disk.

    500, **naming the file**, when the workspace exists and cannot be read. A
    named error is the point: an unhandled exception rendered as a status code
    is still a crash, and the user has to know which file to refresh or delete.

    Deliberately silent about how old the evidence is. Routing a stale workspace
    through the cache modal is F1's edge and F3-S2's contract, and F3-S2 does
    not exist yet; inventing a freshness verdict here would be a contract two
    stories would then have to agree on after the fact.
    """
    try:
        document = load_workspace(key)
    except (InvalidWorkspaceKeyError, WorkspaceNotFoundError) as exc:
        raise HTTPException(
            status_code=404, detail=f"no workspace saved for {key!r}"
        ) from exc
    except WorkspaceCorruptError as exc:
        raise HTTPException(status_code=500, detail=f"workspace {key!r}: {exc}") from exc
    return _workspace(key, document)


@router.put("/workspaces/{key}", response_model=Workspace, summary="Save a workspace")
def put_workspace(key: str, state: WorkspaceState) -> Workspace:
    """Persist one curation state, replacing whatever was stored under `key`.

    Saving is not deriving. This route computes no statistic and the response
    carries none — a client that got its numbers from a save response would
    have two sources of truth for them (D5) — which is also what keeps F14-S2's
    debounced autosave cheap enough to run on every mutation.

    422 (from `WorkspaceState`) for a negative weight, an unknown field or a
    `selections` key: each of those is curation that would otherwise fail
    silently. 400 for a key that cannot address a file, or one that disagrees
    with the `pull_ref` in the body.

    Never validates the evidence. Curation outlives the pull it curates: a
    workspace over a deleted cache entry is an error row offering refresh, not
    a save the store refuses to accept.
    """
    if state.pull_ref != key:
        # The workspace key IS the cache key IS the `pull_ref` (F3-S1). Saving
        # curation under a key that addresses different evidence would restore
        # one unit's weights against another unit's comps — silently, and with
        # every number plausible.
        raise HTTPException(
            status_code=400,
            detail=(
                f"workspace key {key!r} does not match the curation state's pull_ref "
                f"{state.pull_ref!r}; a workspace is filed under the cache key of the "
                "evidence it curates"
            ),
        )
    try:
        document = save_workspace(key, state)
    except InvalidWorkspaceKeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"could not save workspace {key!r}: {exc}",
        ) from exc
    return _workspace(key, document)


def _workspace(key: str, document: WorkspaceDocument) -> Workspace:
    return Workspace(
        key=key,
        saved_at=document.saved_at,
        state=document.curation(),
        search=document.search,
    )


def _row(entry: WorkspaceEntry) -> RecentWorkspace:
    """One recents row — including the two ways it can be un-openable.

    Both of F1's edge cases land here: the workspace file itself is unreadable
    (`entry.error`), or the file is perfectly readable and the evidence it
    curates is gone. The second one matters more than it looks: without it the
    row opens into a Results view with no comps in it, which reads as "there
    are no comps in this market" rather than "the data was deleted".
    """
    error = entry.error
    if error is None and entry.workspace is not None:
        error = _missing_evidence(entry.workspace.pull_ref)

    return RecentWorkspace(
        key=entry.key,
        saved_at=entry.saved_at,
        subject=entry.workspace.subject if entry.workspace else None,
        search=entry.workspace.search if entry.workspace else None,
        error=error,
        offer_refresh=error is not None,
    )


def _missing_evidence(pull_ref: str) -> str | None:
    """Why this row cannot be opened, or `None` when its evidence is on disk.

    A presence check, never a derivation (D5, and the epic's "<1s, zero API
    calls"): serving an `anchor` on every row would mean deriving every stored
    workspace on Home's mount.

    "Missing **or** unreadable", in one message, because `pull_exists` cannot
    tell a caller which without doing the read twice — and because the two are
    the same fact to the user: the row cannot be opened, the curation survived,
    and a full re-pull is the only thing that brings the evidence back.
    """
    if pull_exists(pull_ref):
        return None
    return (
        f"the evidence this workspace curates ({pull_ref}) is missing from disk or "
        "cannot be read. Your curation is intact — refresh to run the search again."
    )
