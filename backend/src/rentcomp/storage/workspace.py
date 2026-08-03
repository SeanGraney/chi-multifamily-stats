"""F1-S2 [BE] Workspace persistence — the mutable half of ARCHITECTURE.md §5.

**AC (story text, verbatim):** open recent → full state restored (including
candidate rent and filter settings) with zero API calls; corrupt entry → error
row with refresh offer, never a crash.

    <RENTCOMP_HOME>/
      cache/<cache-key>/raw/...    immutable, paid-for API responses (D24)
      workspaces/<cache-key>.json  mutable curation state  ← this module

The two stores share a key and nothing else, and that separation is the whole
design (§5): a refresh replaces the evidence without touching the curation, a
pipeline change re-derives for free over the same bytes, and deleting one never
destroys the other. **Nothing in this module reads or writes under `cache/`.**

WHAT IS ON DISK (this story's [DEFAULT]; the *location* is §5, which the story
text itself makes binding)
--------------------------------------------------------------------------
The curation state at the top level of the file, exactly as `POST /api/derive`
takes it, plus two keys the store owns: `saved_at` and the `search` params.
Not an envelope with the state nested inside, for one concrete reason — a
workspace file is then *readable and writable by hand*, which is what the
error messages in this system tell a user to do, and a hand-written file that
is a bare curation state parses as a valid workspace (with `saved_at` falling
back to the file's mtime) rather than as a corrupt one.

CORRUPTION IS ONE CONDITION, NOT A LIST OF EXCEPTION TYPES
-----------------------------------------------------------
`_read_document` converts **any** failure to become a `WorkspaceDocument` into
`WorkspaceCorruptError`. That breadth is deliberate: F4-S9's near-identical
handler enumerated `(OSError, ValueError, KeyError)` and two corruption shapes
escaped through it as unhandled `AttributeError`/`TypeError` raised inside
coercion — which made "never a crash" false while every status-code assertion
still passed. The question this store asks is "can this file become a
workspace", and every no is the same answer to the user: an error row offering
refresh.

**Reading is never a repair and never a deletion.** A file this module cannot
parse is left exactly as it is, byte for byte. The user's only chance of
recovering hand-edited state is that it is still there, and a store that
"cleaned up" what it could not read would destroy it on the first render of
Home — before the user had even seen the error row.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rentcomp.models.requests import DeriveRequest, WorkspaceState
from rentcomp.storage import rentcomp_home

__all__ = [
    "InvalidWorkspaceKeyError",
    "WorkspaceCorruptError",
    "WorkspaceDocument",
    "WorkspaceEntry",
    "WorkspaceNotFoundError",
    "list_workspaces",
    "load_workspace",
    "save_workspace",
    "workspace_path",
    "workspaces_dir",
]

WORKSPACES_DIRNAME = "workspaces"
WORKSPACE_SUFFIX = ".json"

#: A workspace key becomes a path component, and it arrives from a URL — so
#: `../secrets.json` is a thing a client can ask for. Whitelist rather than a
#: blacklist of separators, the same reasoning as `storage/pulls.py::_safe_ref`:
#: it also rules out the null byte (which would otherwise reach `open()` and
#: surface as a 500 rather than a 404) and every encoding trick, and it cannot
#: be defeated by a separator this code did not think of.
_SAFE_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)

#: Cache keys are 64 hex characters. The cap only exists so a hostile or buggy
#: client gets a named 400 instead of a filename-too-long `OSError` as a 500.
_MAX_KEY_LENGTH = 128

#: Sort floor for a row whose timestamp could not be established at all, so
#: "newest first" is still a total order. Aware, because every timestamp this
#: module hands out is aware — mixing the two raises `TypeError` on compare.
_UNDATED = datetime.min.replace(tzinfo=timezone.utc)


class InvalidWorkspaceKeyError(ValueError):
    """The key cannot address a file inside the workspace store.

    A `ValueError` and not a `LookupError`: the caller sent something that is
    not a key at all, which is a different fact from "no workspace is saved
    under this key" and is answered differently at the edge.
    """


class WorkspaceNotFoundError(LookupError):
    """No workspace is saved under that key.

    An ordinary condition — a row the user deleted, a link from an old
    session — so the API answers 404, never a 500.
    """


class WorkspaceCorruptError(Exception):
    """A workspace file exists and cannot be read as one.

    Deliberately not a `ValueError`: a caller distinguishing "your file is bad"
    from "your code is bad" is the same distinction `storage/config.py`'s
    `ConfigError` draws, and for the same reason — one of them is something the
    user can go and fix.
    """


class WorkspaceDocument(WorkspaceState):
    """The on-disk shape: the saved curation state plus when it was saved.

    Extends the wire model rather than restating its fields, so a field added
    to what a client may save is a field that gets persisted — there is no
    second list to forget to update.
    """

    #: Stamped by the store, never accepted from a client. `None` for a file
    #: that was written by hand or restored from a backup; callers fall back to
    #: the file's mtime.
    saved_at: datetime | None = None

    def curation(self) -> DeriveRequest:
        """The state as `POST /api/derive` takes it — bookkeeping stripped."""
        return DeriveRequest.model_validate(
            self.model_dump(exclude={"saved_at", "search"})
        )


@dataclass(frozen=True)
class WorkspaceEntry:
    """One row of the index: a stored workspace, or the reason it cannot be read.

    Exactly one of `workspace` / `error` is set. `saved_at` is populated either
    way, so a broken row still sorts into the right place rather than falling
    to the bottom of the recents table and looking like the oldest thing there.
    """

    key: str
    saved_at: datetime | None
    workspace: WorkspaceDocument | None = None
    error: str | None = None


def workspaces_dir() -> Path:
    """`<RENTCOMP_HOME>/workspaces` — resolved at call time, never cached."""
    return rentcomp_home() / WORKSPACES_DIRNAME


def workspace_path(key: str) -> Path:
    """The file holding `key`'s curation state. Raises for an unsafe key."""
    return workspaces_dir() / f"{_safe_key(key)}{WORKSPACE_SUFFIX}"


def save_workspace(key: str, state: WorkspaceState) -> WorkspaceDocument:
    """Persist `state` under `key`, replacing whatever was there.

    **Replaces, never merges.** F14-S2 autosaves on every mutation, so a store
    that merged would make a removed weight impossible to remove: the second
    save is the truth by definition.

    Writes `.tmp` → fsync → rename (§5a), so a crash mid-write cannot leave a
    file that parses as a *different* curation state — the failure mode a
    partial write would otherwise produce is silent and wrong, not loud.
    """
    path = workspace_path(key)
    document = WorkspaceDocument.model_validate(
        {**state.model_dump(), "saved_at": datetime.now(timezone.utc)}
    )
    body = json.dumps(document.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    _atomic_write_bytes(path, body.encode("utf-8"))
    return document


def load_workspace(key: str) -> WorkspaceDocument:
    """Read one stored workspace, with `saved_at` filled in from the file when
    the document itself does not carry it.

    Raises `InvalidWorkspaceKeyError`, `WorkspaceNotFoundError` or
    `WorkspaceCorruptError` — three named conditions, so the edge can answer
    each one differently and none of them can reach a client as a crash.
    """
    path = workspace_path(key)
    document = _read_document(path)
    if document.saved_at is not None:
        return document
    return document.model_copy(update={"saved_at": _mtime(path)})


def list_workspaces() -> list[WorkspaceEntry]:
    """Every stored workspace, newest first — the recents index (F1-S1).

    **A view over the directory, computed on every call.** Not a maintained
    index file, which could drift from the store in both directions and does so
    silently: a workspace present on disk but absent from the list is
    unreachable (Home is the only way in), and a row whose workspace is gone
    opens into nothing. Neither is representable if the directory *is* the
    index, which is what the story's "an index over stored workspaces" asks
    for.

    Derives nothing. Building this list is what Home does on mount, so anything
    expensive here is paid on every launch, and anything that reached the
    network here would make merely opening the tool cost money.
    """
    directory = workspaces_dir()
    try:
        paths = sorted(directory.iterdir())
    except OSError:
        # No store yet (first launch) is the normal state, not an error; an
        # unreadable one still has to render Home rather than break it.
        return []

    entries: list[WorkspaceEntry] = []
    for path in paths:
        key = _key_of(path)
        if key is None:
            continue
        entry = _entry(key, path)
        if entry is not None:
            entries.append(entry)
    return sorted(entries, key=lambda e: (e.saved_at or _UNDATED, e.key), reverse=True)


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _safe_key(key: str) -> str:
    if (
        not key
        or len(key) > _MAX_KEY_LENGTH
        or key.startswith(".")
        or not _SAFE_ALPHABET.issuperset(key)
    ):
        raise InvalidWorkspaceKeyError(f"{key!r} is not a valid workspace key")
    return key


def _key_of(path: Path) -> str | None:
    """The workspace a file in the store is about, or `None` if it is not one.

    Name-based, and it deliberately accepts directories: a directory standing
    where a workspace file belongs is one of the ways an entry breaks, and it
    has to appear as an error row rather than quietly not exist.
    """
    if not path.name.endswith(WORKSPACE_SUFFIX):
        return None
    key = path.name[: -len(WORKSPACE_SUFFIX)]
    try:
        return _safe_key(key)
    except InvalidWorkspaceKeyError:
        # Not a name this store wrote, and not one the API could address — a
        # row for it would open into a 404.
        return None


def _entry(key: str, path: Path) -> WorkspaceEntry | None:
    mtime = _mtime(path)
    try:
        document = _read_document(path)
    except WorkspaceNotFoundError:
        # Deleted between the directory scan and the read. No ghost row: a row
        # whose workspace is gone opens into nothing.
        return None
    except WorkspaceCorruptError as exc:
        return WorkspaceEntry(key=key, saved_at=mtime, error=str(exc))
    return WorkspaceEntry(
        key=key, saved_at=document.saved_at or mtime, workspace=document
    )


def _read_document(path: Path) -> WorkspaceDocument:
    """Parse one workspace file. Every way it can fail is one of two errors."""
    try:
        raw = path.read_bytes()
    except (FileNotFoundError, NotADirectoryError) as exc:
        raise WorkspaceNotFoundError(f"no workspace saved at {path.name}") from exc
    except OSError as exc:
        # A directory where the file belongs (`IsADirectoryError` on POSIX,
        # `PermissionError` on Windows), or a file this process cannot read.
        raise WorkspaceCorruptError(_unreadable(path, exc)) from exc

    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError(
                f"expected a JSON object of curation state, got {type(payload).__name__}"
            )
        return WorkspaceDocument.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        raise WorkspaceCorruptError(_unreadable(path, exc)) from exc


def _unreadable(path: Path, exc: Exception) -> str:
    """One message for every corruption shape, naming the file and the way out.

    Names the file because the user has to know which one to delete or refresh
    (spec §7), and says "refresh" because a full re-pull is the only thing that
    can rebuild a workspace whose state is gone (F1's edge).
    """
    return (
        f"the saved workspace at {path} could not be read: "
        f"{type(exc).__name__}: {exc}. Refresh to run the search again, or delete "
        "the file to start this workspace over."
    )


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _atomic_write_bytes(path: Path, body: bytes) -> None:
    """`write .tmp -> fsync -> rename` (ARCHITECTURE.md §5a).

    A third copy of this four-line dance (`storage/cache.py`,
    `storage/config.py`) — flagged to the PM as a candidate extraction rather
    than refactored here, because touching two modules other stories are in
    flight over is exactly the drive-by WORKFLOW.md §7 forbids inside a story
    branch.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
