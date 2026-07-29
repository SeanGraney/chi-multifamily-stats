"""F1-S2 (Layer 1, developer-authored) — `storage/workspace.py` directly.

QA owns the story's Layer-2 contract (`backend/tests/api/f1s2/`), which asks
every question over HTTP. This file is the layer underneath it, and it exists
for the three kinds of question the route cannot ask at all:

1.  **Keys HTTP cannot deliver.** `httpx` refuses to build a URL holding a raw
    NUL, and a `\\`-separated key never survives a URL path — so the route-level
    traversal test can only probe what a client can *send*. `_safe_key` is the
    thing that must be total, and only a direct call can drive it with a
    backslash, a drive letter or an over-long name.
2.  **Breadth of the corruption handler, at the level where the breadth lives.**
    The route turns two named exceptions into two status codes; the store turns
    *every* way a file can fail to become a workspace into exactly one of them.
    Asserting `pytest.raises(WorkspaceCorruptError)` per shape is a stronger and
    cheaper statement than asserting "some 4xx/5xx came back".
3.  **Absence of writes.** "Reading is never a repair" and "`cache/` is never
    touched" are properties of the store, not of the edge.

Nothing here goes near the network: the module under test imports no client.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pydantic")

from rentcomp.models.requests import (  # noqa: E402
    DeriveRequest,
    Filters,
    SearchParams,
    Subject,
    WorkspaceState,
)
from rentcomp.storage.workspace import (  # noqa: E402
    InvalidWorkspaceKeyError,
    WorkspaceCorruptError,
    WorkspaceNotFoundError,
    list_workspaces,
    load_workspace,
    save_workspace,
    workspace_path,
    workspaces_dir,
)

KEY = "a" * 64
OTHER_KEY = "b" * 64
THIRD_KEY = "c" * 64

SUBJECT = Subject(
    address="3651 S Wood St Unit 2", lat=41.8286, lng=-87.6716, sqft=1000.0, beds=3.0, baths=1.0
)

SEARCH = SearchParams(
    address="3651 S Wood St, Chicago, IL 60609",
    radius=2.0,
    bedrooms="3:4",
    bathrooms=None,
    property_types=["Multi-Family"],
    years_back=3,
    window_start="01-01",
    window_end="12-31",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    """An empty `RENTCOMP_HOME` for one test (ARCHITECTURE.md §9/D21)."""
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    return root


def state(pull_ref: str = KEY, **overrides) -> WorkspaceState:
    """A fully-curated workspace: every field moved off its default.

    Deliberately the values a naive serializer loses — a falsy `0.0` weight
    (toggle-off IS weight 0, F5-S2 [INVARIANT]), a non-empty override list, all
    three filters set, a negative drift (markets go down) and a candidate rent.
    """
    fields = {
        "pull_ref": pull_ref,
        "subject": SUBJECT,
        "weights": {"comp-one": 3.5, "comp-two": 0.0},
        "include_overrides": ["comp-two"],
        "filters": Filters(max_distance_mi=1.25, hide_censored=True, leased_only=True),
        "drift_pct": -2.5,
        "candidate_rent": 2450.0,
        "search": SEARCH,
    }
    fields.update(overrides)
    return WorkspaceState(**fields)


# ===========================================================================
# the round trip
# ===========================================================================


def test_a_saved_workspace_reads_back_as_the_same_curation_state(home) -> None:
    """"Restored exactly as last left" at the storage layer.

    Compared as a `DeriveRequest`, not field by field: that is the object the
    pipeline consumes, so equality here is the strongest available statement
    that nothing was dropped, defaulted or coerced on the way through disk.
    """
    saved = state()
    expected = DeriveRequest.model_validate(saved.model_dump(exclude={"search"}))

    save_workspace(KEY, saved)
    restored = load_workspace(KEY)

    assert restored.curation() == expected
    assert restored.weights["comp-two"] == 0.0, "a falsy weight was dropped"
    assert restored.candidate_rent == 2450.0
    assert restored.drift_pct == -2.5
    assert restored.filters == saved.filters
    assert restored.subject == SUBJECT, "the subject carries the sqft the anchor needs"
    assert restored.search == SEARCH


def test_a_candidate_rent_that_was_never_set_stays_none(home) -> None:
    """`None` is a real value: no price has been tested yet."""
    save_workspace(KEY, state(candidate_rent=None))

    assert load_workspace(KEY).candidate_rent is None


def test_curation_strips_the_store_s_own_bookkeeping(home) -> None:
    """`saved_at` and `search` belong to the store, not to the derivation.

    `DeriveRequest` forbids extra fields, so a `curation()` that leaked either
    of them would 422 the moment a restored workspace was POSTed back — the
    exact round trip the whole story exists to make work.
    """
    save_workspace(KEY, state())
    curation = load_workspace(KEY).curation()

    dumped = curation.model_dump()
    assert "saved_at" not in dumped and "search" not in dumped
    assert dumped["pull_ref"] == KEY


def test_saving_replaces_rather_than_merges(home) -> None:
    """F14-S2 autosaves every mutation, so the second save is the truth. A
    store that merged would make a removed weight impossible to remove."""
    save_workspace(KEY, state())
    save_workspace(KEY, state(weights={"comp-one": 1.0}, include_overrides=[], candidate_rent=None))

    restored = load_workspace(KEY)
    assert restored.weights == {"comp-one": 1.0}
    assert restored.include_overrides == []
    assert restored.candidate_rent is None


def test_the_file_lands_at_workspaces_slash_key_dot_json(home) -> None:
    """ARCHITECTURE.md §5's location, which the story text makes binding."""
    save_workspace(KEY, state())

    path = home / "workspaces" / f"{KEY}.json"
    assert path.is_file(), f"expected {path}; found {sorted(p.name for p in home.rglob('*'))}"
    assert workspace_path(KEY) == path


def test_the_store_writes_nothing_under_cache(home) -> None:
    """§5's separation: `cache/` is immutable evidence, `workspaces/` is the
    mutable half. A save that touched `cache/` would make refresh unsafe."""
    cache = home / "cache"
    cache.mkdir()
    (cache / "sentinel.json").write_text("{}", encoding="utf-8")
    before = {p: p.read_bytes() for p in cache.rglob("*") if p.is_file()}

    save_workspace(KEY, state())
    load_workspace(KEY)
    list_workspaces()

    assert {p: p.read_bytes() for p in cache.rglob("*") if p.is_file()} == before


def test_a_completed_save_leaves_no_temporary_file_behind(home) -> None:
    """The `.tmp -> fsync -> rename` dance must not litter the store.

    A stray temp file is not cosmetic here: `workspaces/` IS the recents index,
    so anything left in it is a candidate row, and a half-written one would
    render as a corrupt workspace the user never created.
    """
    save_workspace(KEY, state())

    names = sorted(p.name for p in workspaces_dir().iterdir())
    assert names == [f"{KEY}.json"], f"the store left something behind: {names}"


def test_a_workspace_survives_a_process_boundary(home) -> None:
    """D2: JSON on disk, not a dict in memory. Asserted by reading the bytes
    back with `json` rather than through the store that wrote them."""
    save_workspace(KEY, state())

    payload = json.loads((home / "workspaces" / f"{KEY}.json").read_text(encoding="utf-8"))
    assert payload["candidate_rent"] == 2450.0
    assert payload["weights"]["comp-two"] == 0.0


# ===========================================================================
# saved_at
# ===========================================================================


def test_saving_stamps_saved_at_and_loading_returns_it(home) -> None:
    before = datetime.now(timezone.utc)
    document = save_workspace(KEY, state())
    after = datetime.now(timezone.utc)

    assert document.saved_at is not None
    assert before <= document.saved_at <= after
    assert load_workspace(KEY).saved_at == document.saved_at


def test_two_saves_in_a_row_are_distinguishable_in_time(home) -> None:
    """"Newest first" has no stable meaning if two saves seconds apart carry
    the same timestamp — the recents table's order would be arbitrary."""
    first = save_workspace(KEY, state())
    second = save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))

    assert first.saved_at != second.saved_at


def test_a_hand_written_file_falls_back_to_the_file_s_mtime(home) -> None:
    """A bare curation state, written by hand, is a valid workspace.

    The error messages in this system tell the user to edit or delete these
    files, so one that a human wrote must load rather than read as corrupt —
    and it still has to sort into the recents table, which is what the mtime
    fallback is for.
    """
    workspaces_dir().mkdir(parents=True, exist_ok=True)
    path = workspaces_dir() / f"{KEY}.json"
    path.write_text(json.dumps(state().model_dump(mode="json")), encoding="utf-8")

    document = load_workspace(KEY)

    assert document.saved_at is not None, "an undated workspace has no place in a newest-first list"
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    assert abs((document.saved_at - mtime).total_seconds()) < 1.0


# ===========================================================================
# keys — total, including the ones HTTP cannot carry
# ===========================================================================


@pytest.mark.parametrize(
    "key",
    [
        "",
        "..",
        "../secrets",
        "..\\secrets",
        "a/b",
        "a\\b",
        "/etc/passwd",
        "C:\\Windows\\System32",
        ".hidden",
        ".",
        "a\x00b",
        "sec rets",
        "a" * 129,
    ],
    ids=repr,
)
def test_an_unsafe_key_is_refused_before_anything_is_written(home, key) -> None:
    """A key becomes a path component and arrives from a URL.

    Both halves are asserted: the call is refused with the *named* error (a
    caller can then answer 400/404 rather than letting an `OSError` become a
    500), and the filesystem is untouched — `secrets.json` in particular must
    never be reachable, and the API key must never be read (CLAUDE.md).
    """
    before = sorted(str(p) for p in home.rglob("*"))

    with pytest.raises(InvalidWorkspaceKeyError):
        workspace_path(key)
    with pytest.raises(InvalidWorkspaceKeyError):
        save_workspace(key, state())
    with pytest.raises(InvalidWorkspaceKeyError):
        load_workspace(key)

    assert sorted(str(p) for p in home.rglob("*")) == before, "an unsafe key wrote to disk"


def test_a_key_that_is_not_saved_is_a_named_not_found(home) -> None:
    """A row the user deleted, or a link from an old session — ordinary, and
    a different fact from "that is not a key", which the edge answers with a
    different status code."""
    with pytest.raises(WorkspaceNotFoundError):
        load_workspace(KEY)


def test_a_valid_key_is_not_rejected(home) -> None:
    """The negative control for the parametrize above: `_safe_key` must not be
    so strict that a real cache key (64 hex) or WS-1's `ws1-real` fails."""
    for key in (KEY, "ws1-real", "synthetic-basic"):
        save_workspace(key, state(pull_ref=key))
        assert load_workspace(key).pull_ref == key


# ===========================================================================
# corruption — one condition, not a list of exception types
# ===========================================================================


def _make_a_directory(path) -> None:
    path.unlink()
    path.mkdir()


CORRUPTIONS = {
    "truncated json": lambda p: p.write_text('{"pull_ref": "abc"', encoding="utf-8"),
    "unparseable": lambda p: p.write_text("{ not json", encoding="utf-8"),
    "empty file": lambda p: p.write_text("", encoding="utf-8"),
    "not an object": lambda p: p.write_text("null", encoding="utf-8"),
    "a json array": lambda p: p.write_text("[]", encoding="utf-8"),
    "a bare string": lambda p: p.write_text('"weights"', encoding="utf-8"),
    "right shape, wrong types": lambda p: p.write_text(
        json.dumps(
            {
                "pull_ref": 17,
                "subject": "unit 2",
                "weights": "lots",
                "include_overrides": "none",
                "filters": 5,
                "drift_pct": "up a bit",
                "candidate_rent": "expensive",
            }
        ),
        encoding="utf-8",
    ),
    "none of the fields": lambda p: p.write_text(
        json.dumps({"note": "hand-edited"}), encoding="utf-8"
    ),
    "an unknown extra field": lambda p: p.write_text(
        json.dumps({**state().model_dump(mode="json"), "selections": ["comp-one"]}),
        encoding="utf-8",
    ),
    "a negative weight": lambda p: p.write_text(
        json.dumps({**state().model_dump(mode="json"), "weights": {"comp-one": -1.0}}),
        encoding="utf-8",
    ),
    "utf-8 that is not": lambda p: p.write_bytes(b"\xff\xfe\x00{"),
    "a directory": _make_a_directory,
}


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_every_corruption_shape_is_a_workspace_corrupt_error(home, shape) -> None:
    """One question — "can this file become a workspace" — and one no.

    F4-S9's near-identical handler enumerated `(OSError, ValueError, KeyError)`
    and two shapes escaped through it as `AttributeError`/`TypeError` raised
    inside coercion, which made "never a crash" false while every status-code
    assertion still passed. `pytest.raises` is exact about the type, so an
    escape of any other kind fails here rather than at the edge.

    Two of these are not filesystem damage at all: an unknown field and a
    negative weight are *invalid curation*, and they have to be refused for the
    same reason `DeriveRequest` forbids them on the wire — a `selections` key
    would be a second source of truth for selection, and a negative weight
    would silently corrupt every weighted statistic downstream.
    """
    save_workspace(KEY, state())
    CORRUPTIONS[shape](workspace_path(KEY))

    with pytest.raises(WorkspaceCorruptError):
        load_workspace(KEY)


@pytest.mark.parametrize("shape", sorted(set(CORRUPTIONS) - {"a directory"}))
def test_reading_a_corrupt_workspace_neither_rewrites_nor_deletes_it(home, shape) -> None:
    """A read is not a repair, and certainly not a deletion.

    The user's only chance of recovering hand-edited state is that the file is
    still there; a store that "cleaned up" what it could not parse would
    destroy it on the first render of Home, before the error row was ever seen.
    """
    save_workspace(KEY, state())
    path = workspace_path(KEY)
    CORRUPTIONS[shape](path)
    before = path.read_bytes()

    with pytest.raises(WorkspaceCorruptError):
        load_workspace(KEY)
    list_workspaces()

    assert path.exists() and path.read_bytes() == before


def test_the_corruption_message_names_the_file_and_offers_the_way_out(home) -> None:
    """Spec §7: the user has to know which file to refresh or delete."""
    save_workspace(KEY, state())
    workspace_path(KEY).write_text("{ not json", encoding="utf-8")

    with pytest.raises(WorkspaceCorruptError) as caught:
        load_workspace(KEY)

    message = str(caught.value)
    assert KEY in message
    assert "refresh" in message.lower()


def test_a_corrupt_workspace_can_be_replaced_by_saving_over_it(home) -> None:
    """Refusing has to be a response to the corruption, not a permanent state,
    or the refresh the AC offers lands right back on the broken file."""
    save_workspace(KEY, state())
    workspace_path(KEY).write_text("{ not json", encoding="utf-8")

    save_workspace(KEY, state(drift_pct=8.0))

    assert load_workspace(KEY).drift_pct == 8.0


# ===========================================================================
# the index is a view over the directory
# ===========================================================================


def test_an_absent_store_and_an_empty_store_are_both_an_empty_list(home) -> None:
    """First launch is the normal state, not an error — Home asks for this on
    mount before anything has ever been searched."""
    assert list_workspaces() == []

    workspaces_dir().mkdir(parents=True)
    assert list_workspaces() == []


def test_the_index_is_newest_first(home) -> None:
    save_workspace(KEY, state())
    save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))
    save_workspace(THIRD_KEY, state(pull_ref=THIRD_KEY))

    assert [entry.key for entry in list_workspaces()] == [THIRD_KEY, OTHER_KEY, KEY]


def test_re_saving_moves_a_workspace_back_to_the_top(home) -> None:
    """"Recent" means recently worked on, not first created — otherwise the
    table degrades into creation order and stops being "the workspace I was
    just in"."""
    save_workspace(KEY, state())
    save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))
    assert [e.key for e in list_workspaces()][0] == OTHER_KEY

    save_workspace(KEY, state(drift_pct=2.0))

    assert [e.key for e in list_workspaces()][0] == KEY


def test_a_file_that_appears_on_disk_appears_in_the_index(home) -> None:
    """The store IS the index. A maintained index file could not pass this —
    nothing told it about the new workspace — and that divergence is not
    hypothetical: restoring a backup or syncing a home directory produces it.
    """
    save_workspace(KEY, state())
    planted = workspaces_dir() / f"{OTHER_KEY}.json"
    planted.write_text(
        json.dumps(state(pull_ref=OTHER_KEY).model_dump(mode="json")), encoding="utf-8"
    )

    assert {entry.key for entry in list_workspaces()} == {KEY, OTHER_KEY}


def test_a_file_that_disappears_from_disk_leaves_the_index(home) -> None:
    """The other direction: no ghost rows opening into nothing."""
    save_workspace(KEY, state())
    save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))

    workspace_path(KEY).unlink()

    assert [entry.key for entry in list_workspaces()] == [OTHER_KEY]


@pytest.mark.parametrize("name", ["notes.txt", "workspace.json.tmp", ".hidden.json", "README"])
def test_the_index_ignores_files_that_could_never_be_workspaces(home, name) -> None:
    """A row the API could not address would open into a 404.

    `.hidden.json` matters most: `_safe_key` refuses a leading dot, so a row
    for it would be permanently un-openable — worse than absent, because it
    looks like lost work.
    """
    save_workspace(KEY, state())
    (workspaces_dir() / name).write_text("{}", encoding="utf-8")

    assert [entry.key for entry in list_workspaces()] == [KEY]


def test_a_corrupt_entry_is_a_row_with_an_error_not_a_missing_row(home) -> None:
    """Silently dropping it loses the user's curation with no explanation and
    no way back; the AC asks for a row that says so and offers refresh."""
    save_workspace(KEY, state())
    workspace_path(KEY).write_text("{ not json", encoding="utf-8")

    entries = list_workspaces()

    assert [entry.key for entry in entries] == [KEY]
    assert entries[0].error and KEY in entries[0].error
    assert entries[0].workspace is None
    assert entries[0].saved_at is not None, "a broken row still has to sort into the table"


def test_one_corrupt_entry_does_not_take_its_healthy_neighbours_down(home) -> None:
    """Home renders whatever it can. One unreadable file cannot cost the user
    every other search they have ever run."""
    save_workspace(KEY, state())
    save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))
    save_workspace(THIRD_KEY, state(pull_ref=THIRD_KEY))
    workspace_path(OTHER_KEY).write_text("{ not json", encoding="utf-8")

    entries = {entry.key: entry for entry in list_workspaces()}

    assert set(entries) == {KEY, OTHER_KEY, THIRD_KEY}
    assert entries[OTHER_KEY].error
    assert entries[KEY].error is None and entries[KEY].workspace is not None
    assert entries[THIRD_KEY].error is None and entries[THIRD_KEY].workspace is not None


def test_an_unreadable_store_directory_is_an_empty_list_not_an_exception(home) -> None:
    """Home must render even when the store itself cannot be listed."""
    path = home / "workspaces"
    path.write_text("not a directory", encoding="utf-8")

    assert list_workspaces() == []


def test_the_index_carries_what_the_recents_table_renders(home) -> None:
    """F1-S1's columns are address / specs / radius / age, and none of them is
    recoverable from anything else on disk: the cache key is a one-way SHA256
    of the search params and the manifest holds only `as_of` and the window.
    """
    save_workspace(KEY, state())

    entry = list_workspaces()[0]

    assert entry.workspace is not None
    assert entry.workspace.search == SEARCH
    assert entry.workspace.subject == SUBJECT
    assert entry.saved_at is not None


def test_the_index_holds_no_derived_statistic(home) -> None:
    """Deriving to build a row would blow the epic's "<1s, zero API calls"
    budget on Home's mount, and would put a number in front of the user with
    no evidence behind it (D5). The row is the stored state and nothing more.
    """
    save_workspace(KEY, state())

    entry = list_workspaces()[0]

    assert not hasattr(entry, "anchor")
    assert entry.workspace is not None
    assert not any(
        field in entry.workspace.model_dump()
        for field in ("anchor", "buckets", "comps", "km_curve", "expected_vacancy")
    )


def test_listing_reads_the_directory_every_time(home) -> None:
    """No memo between calls: a change on disk between two renders of Home has
    to be visible on the second one."""
    assert list_workspaces() == []
    save_workspace(KEY, state())
    assert [entry.key for entry in list_workspaces()] == [KEY]
    workspace_path(KEY).unlink()
    assert list_workspaces() == []


def test_the_store_is_read_from_rentcomp_home_at_call_time(tmp_path, monkeypatch) -> None:
    """`RENTCOMP_HOME` is resolved per call, never captured at import (D21).

    A module-level `Path` would make every test in this suite share the
    developer's real `~/.rentcomp` the moment one of them forgot to override.
    """
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()

    monkeypatch.setenv("RENTCOMP_HOME", str(first))
    save_workspace(KEY, state())
    assert [entry.key for entry in list_workspaces()] == [KEY]

    monkeypatch.setenv("RENTCOMP_HOME", str(second))
    assert list_workspaces() == []
    assert workspaces_dir() == second / "workspaces"


def test_a_stale_timestamp_in_the_file_still_orders_the_row(home) -> None:
    """`saved_at` in the document wins over the mtime, which is what makes the
    order survive a file copy (a copy resets the mtime, not the content)."""
    old = datetime.now(timezone.utc) - timedelta(days=30)
    save_workspace(KEY, state())
    save_workspace(OTHER_KEY, state(pull_ref=OTHER_KEY))

    payload = json.loads(workspace_path(OTHER_KEY).read_text(encoding="utf-8"))
    payload["saved_at"] = old.isoformat()
    workspace_path(OTHER_KEY).write_text(json.dumps(payload), encoding="utf-8")
    os.utime(workspace_path(OTHER_KEY), None)  # freshest mtime, oldest saved_at

    assert [entry.key for entry in list_workspaces()] == [KEY, OTHER_KEY]
