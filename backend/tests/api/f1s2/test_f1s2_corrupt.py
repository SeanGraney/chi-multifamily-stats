"""F1-S2 AC3 (Layer 2) — "corrupt entry → error row with refresh offer, never
a crash", across every shape a workspace file can break in.

    **AC (story text, verbatim):** ... corrupt entry → error row with refresh
    offer, never a crash.

    **Epic F1 edge:** Corrupt/missing cache entry → row shows error state,
    offers refresh (full re-pull).

WHY NINE SHAPES AND NOT ONE
---------------------------
F4-S9's QA probed a corrupt *manifest* the same way and the single-case test
would have missed both defects it found: `run_pull` re-bought a whole pull on
four of six shapes, and two more escaped as anonymous 500s because
`except (OSError, ValueError, KeyError)` does not catch the `AttributeError`/
`TypeError` raised inside coercion — which made a docstring's promise false.
"Corrupt" is not one condition:

* the file is not JSON at all (truncated mid-write, or hand-edited)
* it is JSON of the wrong kind (a null, an array)
* it is an object with the right field names and the wrong value types — the
  shape that gets past a `json.loads` guard and dies in coercion
* it is empty (a crash between create and write)
* it is not a file at all (a directory), or cannot be read

FOUR THINGS MUST HOLD FOR EVERY ONE OF THEM
-------------------------------------------
1.  `GET /api/workspaces` is a 200 and **the healthy rows are still there** — a
    partial recents list, not a broken Home screen.
2.  The broken row is **still listed, marked as an error, offering refresh**.
    A row that silently vanishes is worse than a broken one: the user's
    curation is gone with no explanation and no way back.
3.  `GET /api/workspaces/{key}` answers with a **named** error, never an
    anonymous 500 (the F4-S9 escape).
4.  **Nothing is fetched and nothing is destroyed.** Reading a broken file must
    not spend a call to work around it (D24) and must not delete the evidence
    the refresh offer is about.
"""

from __future__ import annotations

import json

import pytest
from f1s2_support import (
    OTHER_SEARCH_BODY,
    THIRD_SEARCH_BODY,
    WORKSPACES_PATH,
    curation,
    is_json_error,
    keys_in,
    row_error,
    row_offers_refresh,
    rows_by_key,
)


def _text(body: str):
    def apply(path) -> None:
        path.write_text(body, encoding="utf-8")

    return apply


def _make_a_directory(path) -> None:
    """A directory standing where the workspace file belongs.

    Reaches `open()` as `IsADirectoryError` on POSIX and `PermissionError` on
    Windows — both `OSError`, neither a `ValueError`.
    """
    path.unlink()
    path.mkdir()
    (path / "inner.json").write_text("{}", encoding="utf-8")


def _make_unreadable(path) -> None:
    """Unreadable, by whatever means this platform actually honours.

    `chmod 0` does not remove read access for the owner on Windows, so a test
    that assumed it would silently assert nothing there (the same class of
    quiet no-op as the three known Windows permission failures on `main`).
    Verified, with a directory substituted when the platform refuses — that
    raises `OSError` from `open()` just the same, which is the condition under
    test.
    """
    import os

    try:
        os.chmod(path, 0)
        with open(path, "rb"):
            pass
    except OSError:
        return  # genuinely unreadable
    except Exception:  # pragma: no cover - defensive
        return
    os.chmod(path, 0o600)
    _make_a_directory(path)


#: Every way a stored workspace can be broken, by hand-editing, by a crash
#: mid-write, or by the filesystem.
CORRUPTIONS = {
    "truncated json": _text('{"pull_ref": "abc", "weights": {"x": 1.0}'),
    "unparseable": _text("{ not json"),
    "empty file": _text(""),
    "json but not an object": _text("null"),
    "a json array": _text('[{"weights": {}}]'),
    "right shape, wrong types": _text(
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
        )
    ),
    "an object with none of the fields": _text(json.dumps({"note": "hand-edited"})),
    "a directory where the file belongs": _make_a_directory,
    "unreadable": _make_unreadable,
}

#: The subset that leaves an ordinary file on disk, so its bytes can be
#: compared before and after a read.
CONTENT_CORRUPTIONS = [
    "truncated json",
    "unparseable",
    "empty file",
    "json but not an object",
    "a json array",
    "right shape, wrong types",
    "an object with none of the fields",
]


@pytest.fixture
def two_healthy_and_one_broken(workspaces, pull, another_pull, workspace_file, comp_keys):
    """Three saved workspaces, one of which gets corrupted on demand.

    Three rather than two because "the other rows survive" is the assertion
    that a naive `for row in rows: parse(row)` fails — one broken entry must
    not take the list down with it, and must not truncate it either.
    """
    from f1s2_support import fully_curated

    healthy_a = pull
    healthy_b = another_pull(OTHER_SEARCH_BODY)
    broken = another_pull(THIRD_SEARCH_BODY)

    assert workspaces.save(healthy_a.ref, fully_curated(healthy_a.ref, comp_keys)).status_code < 300
    assert workspaces.save(healthy_b.ref, curation(healthy_b.ref, drift_pct=5.0)).status_code < 300
    assert workspaces.save(broken.ref, curation(broken.ref, candidate_rent=2200.0)).status_code < 300

    def corrupt(shape: str):
        CORRUPTIONS[shape](workspace_file(broken.ref))
        return broken.ref

    corrupt.healthy = (healthy_a.ref, healthy_b.ref)
    corrupt.broken_pull = broken
    return corrupt


# ===========================================================================
# 1 + 2 — the recents list survives, and says which row is broken
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_no_shape_of_corrupt_workspace_breaks_the_recents_list(
    workspaces, two_healthy_and_one_broken, shape
) -> None:
    """Home must render. One unreadable file cannot cost the user every other
    search they have ever run."""
    broken_key = two_healthy_and_one_broken(shape)

    response = workspaces.recents()

    assert response.status_code == 200, (
        f"a {shape} workspace made GET {WORKSPACES_PATH} return {response.status_code}: "
        f"{response.text[:400]}. The recents list is the app's front door — 'never a crash' "
        "means it renders whatever it can."
    )
    listed = keys_in(response)
    for healthy in two_healthy_and_one_broken.healthy:
        assert healthy in listed, (
            f"a {shape} workspace took a HEALTHY row ({healthy}) out of the recents list. A "
            f"partial list is the requirement; got {listed}"
        )
    assert broken_key in listed, (
        f"the {shape} workspace vanished from recents instead of showing an error row. "
        "Silently dropping it loses the user's curation with no explanation and no way back — "
        "the AC asks for a row that offers refresh."
    )


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_a_corrupt_row_is_marked_as_an_error_and_offers_refresh(
    workspaces, two_healthy_and_one_broken, shape
) -> None:
    """The AC's own words: "error row with refresh offer".

    Both halves matter. A row marked broken with no route out is a dead end;
    F1's edge says the offer is a full re-pull, and only the server knows the
    row is unreadable, so only the server can say so (D5 — the frontend renders
    what it is told).
    """
    broken_key = two_healthy_and_one_broken(shape)
    rows = rows_by_key(workspaces.recents())

    row = rows.get(broken_key)
    assert row is not None, f"the {shape} row is not in the list at all: {list(rows)}"
    assert row_error(row), (
        f"the {shape} row is not marked as an error: {row}. It renders identically to a "
        "healthy row, so the user clicks it and finds their curation silently reset."
    )
    assert row_offers_refresh(row), (
        f"the {shape} row is marked broken but offers no way out: {row}. F1's edge is "
        "'row shows error state, offers refresh (full re-pull)'."
    )
    for healthy in two_healthy_and_one_broken.healthy:
        assert not row_error(rows[healthy]), (
            f"a {shape} neighbour made the healthy row {healthy} report an error too: "
            f"{rows[healthy]}"
        )


# ===========================================================================
# 3 — a named error, never an anonymous 500
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_no_shape_of_corrupt_workspace_escapes_as_an_anonymous_500(
    workspaces, two_healthy_and_one_broken, shape
) -> None:
    """Opening the broken row answers with something the user can act on.

    F4-S9's QA found two corruption shapes escaping as unhandled exceptions —
    `AttributeError`/`TypeError` raised inside coercion, past an
    `except (OSError, ValueError, KeyError)` — which made "never a crash" false
    while every status-code assertion still passed. The discriminator is the
    body: a deliberate `HTTPException` carries a `detail`; an unhandled
    exception rendered by Starlette is bare plain text.
    """
    broken_key = two_healthy_and_one_broken(shape)

    response = workspaces.load(broken_key)

    assert response.status_code >= 400, (
        f"a {shape} workspace loaded 'successfully' ({response.status_code}): "
        f"{response.text[:300]}. Restoring nothing, quietly, is the failure the AC's "
        "'full state restored' forbids."
    )
    assert is_json_error(response), (
        f"a {shape} workspace crashed anonymously: {response.status_code} "
        f"{response.headers.get('content-type')!r} {response.text[:200]!r}. An unhandled "
        "exception rendered as a status code is still a crash."
    )
    assert broken_key in json.dumps(response.json()), (
        f"the error for a {shape} workspace does not name the entry: {response.json()}. The "
        "user has to know which file to delete or refresh (spec §7)."
    )


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_a_healthy_workspace_still_opens_while_another_is_corrupt(
    workspaces, two_healthy_and_one_broken, shape
) -> None:
    """Blast radius: one bad file, one bad row."""
    two_healthy_and_one_broken(shape)
    healthy = two_healthy_and_one_broken.healthy[0]

    response = workspaces.load(healthy)

    assert response.status_code == 200, (
        f"a {shape} workspace elsewhere in the store made a healthy one unopenable "
        f"({response.status_code}): {response.text[:300]}"
    )


# ===========================================================================
# 4 — a corrupt entry is never re-bought, and never destroyed
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CORRUPTIONS))
def test_no_shape_of_corrupt_workspace_sends_the_app_back_to_the_source(
    workspaces, two_healthy_and_one_broken, no_fetch_allowed, shape
) -> None:
    """Curation is broken; the evidence is not. Nothing may be re-fetched.

    On the live path, "recover from a broken file by re-pulling" is the whole
    pull bought a second time — six of fifty monthly calls — to work around a
    file the user could have deleted for free (D24). Asserted structurally: the
    fetch door is bricked up and the raw bytes are compared, not the ledger,
    which cannot move in fixture mode.
    """
    broken_key = two_healthy_and_one_broken(shape)
    broken_pull = two_healthy_and_one_broken.broken_pull
    bytes_before = broken_pull.raw_snapshot()
    entries_before = sorted(p.name for p in (broken_pull.home / "cache").iterdir())

    workspaces.recents()
    workspaces.load(broken_key)

    assert no_fetch_allowed == [], (
        f"a {shape} workspace sent the app back to RentCast: {no_fetch_allowed}"
    )
    assert broken_pull.raw_snapshot() == bytes_before, (
        f"a {shape} workspace changed the raw evidence under cache/{broken_key}"
    )
    assert sorted(p.name for p in (broken_pull.home / "cache").iterdir()) == entries_before, (
        f"a {shape} workspace caused a new cache entry to appear — the corruption was worked "
        "around by starting a fresh pull somewhere else"
    )


@pytest.mark.parametrize("shape", CONTENT_CORRUPTIONS)
def test_reading_a_corrupt_workspace_does_not_rewrite_or_delete_it(
    workspaces, two_healthy_and_one_broken, workspace_file, shape
) -> None:
    """A read is not a repair, and certainly not a deletion.

    The user's only chance of recovering hand-edited state is that the file is
    still there; a store that "cleans up" what it cannot parse destroys it on
    the first render of Home, before the user has seen the error row.
    """
    broken_key = two_healthy_and_one_broken(shape)
    path = workspace_file(broken_key)
    before = path.read_bytes()

    workspaces.recents()
    workspaces.load(broken_key)

    assert path.exists(), f"reading a {shape} workspace deleted it ({path})"
    assert path.read_bytes() == before, f"reading a {shape} workspace rewrote it ({path})"


def test_a_corrupt_workspace_can_be_replaced_by_saving_over_it(
    workspaces, two_healthy_and_one_broken, comp_keys
) -> None:
    """Refusing must be a response to the corruption, not a permanent state.

    The complement of every test above: without this, "always fail for that
    key" satisfies all of them while making the row unrecoverable — and the
    refresh the AC offers would land right back on the broken file. (F4-S9's
    `..._after_a_corrupt_one_is_repaired` is the same argument for manifests.)
    """
    broken_key = two_healthy_and_one_broken("unparseable")
    assert workspaces.load(broken_key).status_code >= 400

    rewritten = curation(broken_key, drift_pct=8.0, candidate_rent=1875.0)
    assert workspaces.save(broken_key, rewritten).status_code < 300, (
        "a workspace whose file is corrupt cannot be saved over, so the user is stuck with a "
        "permanently broken row"
    )

    response = workspaces.load(broken_key)
    assert response.status_code == 200, f"still broken after being rewritten: {response.text[:300]}"

    rows = rows_by_key(workspaces.recents())
    assert not row_error(rows[broken_key]), (
        f"the row is still flagged as an error after being repaired: {rows[broken_key]}"
    )


# ===========================================================================
# the epic's other half of this edge — a MISSING cache entry
# ===========================================================================


def test_a_workspace_whose_evidence_is_missing_is_an_error_row_offering_refresh(
    workspaces, saved_workspace, pull, no_fetch_allowed
) -> None:
    """"Corrupt/**missing** cache entry → row shows error state, offers
    refresh" — the pull is gone, the curation is intact.

    This is the case the refresh offer was invented for: the curation state is
    perfectly readable and completely unusable, because the evidence it curates
    no longer exists. The row must say so rather than open into an empty
    Results view, which would read as "no comps in this market".
    """
    import shutil

    key, _ = saved_workspace
    shutil.rmtree(pull.entry)

    response = workspaces.recents()
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"

    rows = rows_by_key(response)
    assert key in rows, f"the workspace disappeared when its evidence did: {list(rows)}"
    assert row_error(rows[key]), (
        f"the row for a workspace with no evidence is not marked as an error: {rows[key]}. "
        "Opening it derives nothing, which renders as a market with no comps in it."
    )
    assert row_offers_refresh(rows[key]), (
        f"no refresh offer on a row whose evidence is gone: {rows[key]}. A full re-pull is the "
        "only thing that can bring it back."
    )
    assert no_fetch_allowed == [], "listing recents re-pulled the missing evidence by itself"
