"""F1-S2 (Layer 2) — "the recents list is an index OVER stored workspaces".

    **Story text, verbatim:** Recents list is an index over stored workspaces,
    served by `GET /api/workspaces`.

    **Epic F1 step 1:** recent searches table (address, specs, radius, anchor,
    age), newest first.
    **Epic F1 edge:** No recents → table hidden, NEW SEARCH is the only path.

WHAT "AN INDEX OVER" FORBIDS
----------------------------
A second store. If the recents list is maintained as its own file, it can drift
from the workspace directory in both directions, and both are silent:

* a workspace **present on disk but absent from recents** — the user's curation
  exists and is unreachable, because Home is the only way in;
* a row **in recents whose workspace is gone** — a ghost row that opens into
  nothing.

Neither is reachable if the list is derived from the directory on every read,
which is what the story's phrasing requires. The two tests named
`test_a_workspace_that_appears_on_disk...` / `...that_disappears_from_disk...`
are the ones that tell those two implementations apart; nothing else here does.

LAYER
-----
The ORDER of the data is L2 (a list in a response body). The *rendering* of
that order — and the empty state hiding the table — is L3, in
`e2e/tests/f1-s2-recents.spec.ts`, because which component mounts is a React
question. Ten rows in a browser would be the parametrize-in-Playwright smell.
"""

from __future__ import annotations

import json

import pytest
from f1s2_support import (
    OTHER_SEARCH_BODY,
    SEARCH_BODY,
    THIRD_SEARCH_BODY,
    WORKSPACES_PATH,
    curation,
    keys_in,
    row_timestamp,
    rows_by_key,
)


# ===========================================================================
# the empty state (epic edge: no recents)
# ===========================================================================


def test_an_empty_store_is_an_empty_list_not_an_error(workspaces) -> None:
    """First launch, before anything has ever been searched.

    Home asks for this on mount, so a 404/500 here is the first thing a new
    user would see. The "table hidden" half is L3's; this is the data that
    tells the view there is nothing to hide.
    """
    response = workspaces.recents()

    assert response.status_code == 200, (
        f"GET {WORKSPACES_PATH} with nothing saved returned {response.status_code}: "
        f"{response.text[:300]}"
    )
    assert keys_in(response) == [], f"expected no rows, got {keys_in(response)}"


# ===========================================================================
# the index is over the store — both directions
# ===========================================================================


def test_every_saved_workspace_appears_exactly_once(
    workspaces, pull, another_pull, comp_keys
) -> None:
    """Three saves, three rows. Duplicates would mean an append-only index
    rather than a view of the directory."""
    from f1s2_support import fully_curated

    second = another_pull(OTHER_SEARCH_BODY)
    third = another_pull(THIRD_SEARCH_BODY)
    assert workspaces.save(pull.ref, fully_curated(pull.ref, comp_keys)).status_code < 300
    assert workspaces.save(second.ref, curation(second.ref)).status_code < 300
    assert workspaces.save(third.ref, curation(third.ref, drift_pct=9.0)).status_code < 300

    listed = keys_in(workspaces.recents())

    assert sorted(listed) == sorted({pull.ref, second.ref, third.ref}), (
        f"recents lists {listed}, expected exactly the three saved workspaces"
    )
    assert len(listed) == len(set(listed)), f"a workspace is listed twice: {listed}"


def test_a_workspace_that_appears_on_disk_appears_in_recents(
    workspaces, pull, another_pull, workspace_file, saved_workspace
) -> None:
    """The store IS the index — a file that appears is a row that appears.

    A separate index file cannot pass this: nothing told it about the new
    workspace. This is the exact divergence "an index over stored workspaces"
    forbids, and it is not hypothetical — restoring a backup, syncing a home
    directory between machines, or a crash after the file was written but
    before an index was updated all produce it.
    """
    key, _ = saved_workspace
    second = another_pull(OTHER_SEARCH_BODY)
    template = workspace_file(key)

    planted = template.with_name(template.name.replace(key, second.ref))
    planted.write_text(json.dumps(curation(second.ref, drift_pct=4.0)), encoding="utf-8")

    listed = keys_in(workspaces.recents())
    assert second.ref in listed, (
        f"a workspace file present at {planted.name} is not listed in recents ({listed}). The "
        "recents list is an index over stored workspaces; a workspace on disk that Home cannot "
        "show is unreachable, because Home is the only way into it."
    )


def test_a_workspace_that_disappears_from_disk_leaves_recents(
    workspaces, saved_workspace, workspace_file
) -> None:
    """The other direction: no ghost rows.

    A row whose workspace is gone opens into nothing — and it is the shape a
    stale index file produces the moment a user deletes a file by hand, which
    the error messages in this system explicitly tell them to do.
    """
    key, _ = saved_workspace
    assert key in keys_in(workspaces.recents())

    workspace_file(key).unlink()

    listed = keys_in(workspaces.recents())
    assert key not in listed, (
        f"{key} is still listed after its workspace file was deleted: {listed}. The index is "
        "remembering something the store no longer holds."
    )


# ===========================================================================
# newest first
# ===========================================================================


def test_recents_are_newest_first(workspaces, pull, another_pull) -> None:
    """Epic F1 step 1: "newest first".

    Also asserts the two rows are *distinguishable in time* before asserting
    the order: if two searches saved seconds apart carry the same timestamp,
    "newest first" has no stable meaning and the table's order is arbitrary —
    which is a defect in the index, not a flaky test.
    """
    second = another_pull(OTHER_SEARCH_BODY)
    assert workspaces.save(pull.ref, curation(pull.ref)).status_code < 300
    assert workspaces.save(second.ref, curation(second.ref)).status_code < 300

    rows = rows_by_key(workspaces.recents())
    assert row_timestamp(rows[pull.ref]) != row_timestamp(rows[second.ref]), (
        "two workspaces saved one after the other carry the same timestamp "
        f"({row_timestamp(rows[pull.ref])!r}) — 'newest first' cannot be enforced, so the "
        "recents table's order is arbitrary. The index needs a finer-grained timestamp."
    )

    listed = keys_in(workspaces.recents())
    assert listed.index(second.ref) < listed.index(pull.ref), (
        f"recents order is {listed}; the most recently saved workspace ({second.ref}) must "
        "come first"
    )


def test_reopening_and_resaving_moves_a_row_back_to_the_top(
    workspaces, pull, another_pull
) -> None:
    """"Recent" means recently worked on, not first created.

    F14-S2 autosaves every mutation, so the row a user was curating five
    minutes ago must be the row at the top when they come back — otherwise the
    table's order degrades into creation order and the feature stops being
    "jump back into the workspace I was in".

    QA note for the PM: the story does not say whether "newest" is last-saved
    or last-fetched. This test takes last-saved, on the epic's "restored
    exactly as last left" wording. If the PM rules otherwise, this is the test
    to change.
    """
    second = another_pull(OTHER_SEARCH_BODY)
    assert workspaces.save(pull.ref, curation(pull.ref)).status_code < 300
    assert workspaces.save(second.ref, curation(second.ref)).status_code < 300
    assert keys_in(workspaces.recents())[0] == second.ref

    assert workspaces.save(pull.ref, curation(pull.ref, drift_pct=2.0)).status_code < 300

    listed = keys_in(workspaces.recents())
    assert listed[0] == pull.ref, (
        f"after re-saving {pull.ref} the order is {listed} — the workspace the user just "
        "worked in is not at the top"
    )


# ===========================================================================
# what a row has to carry for F1-S1 to render it
# ===========================================================================


def test_each_recents_row_identifies_its_workspace_and_its_age(
    workspaces, saved_workspace
) -> None:
    """The two columns nothing else can supply.

    The key, because clicking the row is the only way to open it; and a
    timestamp, because F1-S1's table has an `age` column and D5 forbids the
    frontend computing a value from nothing.
    """
    key, _ = saved_workspace
    rows = rows_by_key(workspaces.recents())

    assert key in rows
    assert row_timestamp(rows[key]) is not None


def test_a_recents_row_says_which_search_it_is_about(workspaces, saved_workspace) -> None:
    """The recents table renders "address, specs, radius" — and the address is
    not recoverable from anything else on disk.

    The cache key is a SHA256 of the search params and cannot be reversed; the
    manifest holds only `as_of`, the window and the per-query record; and
    `DeriveRequest.subject.address` is the SUBJECT's address, which is the
    right one to show but is only present if the subject is stored. So the
    search this workspace is about must be readable from the index.

    QA note for the PM — this is an open contract question, not a settled one:
    the story says only "an index over stored workspaces", and the epic's
    `anchor` column is a *derived statistic*, which the index cannot carry
    without deriving every workspace on Home's mount. Deliberately NOT pinned
    here; flagged to the PM instead.
    """
    key, state = saved_workspace
    rows = rows_by_key(workspaces.recents())
    row_text = json.dumps(rows[key])

    assert SEARCH_BODY["address"].split(",")[0] in row_text or (
        state["subject"]["address"].split(" Unit")[0] in row_text
    ), (
        f"the recents row carries no address: {rows[key]}. F1-S1's table renders "
        f"address/specs/radius per row, the cache key is a one-way hash of them, and nothing "
        "else on disk holds them."
    )


# ===========================================================================
# the >7d edge — NOT testable until F3-S2 exists (strict xfail, not a skip)
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason=(
        "F1 epic edge 'cache aged (>7d) -> route through the Cache Modal (F3) instead of "
        "loading silently' depends on F3-S2, which does not exist (QUEUE row 36, BLOCKED). "
        "There is no cache-modal contract to assert against yet. Marked strict so this FAILS "
        "the day the gap is closed and demands its own removal — the opposite decay curve "
        "from a skip or a note (pattern established by F2-S2's QA)."
    ),
)
def test_a_recent_over_evidence_older_than_seven_days_is_not_loaded_silently(
    workspaces, saved_workspace, pull
) -> None:
    """Opening a stale recent must route through the cache modal.

    The money argument is the whole reason the modal exists: refreshing costs
    real calls, so the choice is the user's — but silently serving week-old
    evidence as if it were current is the other half of the mistake, and it is
    the half with no visible symptom.

    What is asserted: the response to opening a stale workspace is
    *distinguishable* from opening a fresh one — either it is refused pending a
    decision, or it says the evidence is stale. Which of those F3-S2 chooses is
    F3-S2's to define.
    """
    from datetime import timedelta

    from rentcomp.storage.cache import read_manifest, write_manifest

    key, _ = saved_workspace
    fresh = workspaces.load(key)
    assert fresh.status_code == 200

    manifest = read_manifest(pull.ref)
    write_manifest(
        pull.ref,
        as_of=manifest.as_of - timedelta(days=8),
        window=manifest.window,
        planned=manifest.planned,
        fetchable=manifest.fetchable,
        queries=manifest.queries,
    )

    stale = workspaces.load(key)
    body = json.dumps(stale.json()) if stale.headers.get("content-type", "").startswith(
        "application/json"
    ) else stale.text

    assert stale.status_code != 200 or "stale" in body.lower(), (
        "opening a workspace over 8-day-old evidence is indistinguishable from opening a fresh "
        "one, so the client has no way to route it through the cache modal (F1 edge, F3-S2)"
    )
