"""F1-S2 AC1 (Layer 2) — "open recent → full state restored".

    **AC (story text, verbatim):** open recent → full state restored
    (including candidate rent and filter settings) with zero API calls;
    corrupt entry → error row with refresh offer, never a crash.

This file owns the first clause. The zero-call clause is
`test_f1s2_zero_calls.py`; the corrupt clause is `test_f1s2_corrupt.py`.

WHY LAYER 2 (AGENT_QA.md decision procedure, step 2)
-----------------------------------------------------
Every assertion here is "given curation state X saved, the API returns X" —
phrasable as a field comparison over an HTTP round trip, with no layout, no
click and no component. The browser's job in this story is proving the recents
row is clickable and the view mounts (`e2e/tests/f1-s2-recents.spec.ts`); a
Playwright row asserting a restored *value* would be an L2 test in a browser
costume.

WHAT "EXACTLY AS LAST LEFT" IS PINNED BY
-----------------------------------------
Two complementary things, because either alone is escapable:

1.  **Field-by-field**, on the values most likely to be quietly dropped — a
    falsy `0.0` weight, a `None` candidate rent, the three filter settings, a
    negative drift.
2.  **Byte-identically through the derivation** — the restored state POSTed to
    `/api/derive` returns the same `DerivedState` as the state that was saved.
    This one cannot be satisfied by a store that keeps the right fields under
    the wrong meanings, and it does not pin the storage envelope at all.

`subject` is asserted to round-trip too, and this is a QA note for the PM: the
story's field list (`{selections, weights, filters, driftPct, candidateRent}`)
does not mention it, but the epic requires the **anchor** to be restored, the
anchor is a function of the subject's sqft, and `SearchRequest` has nowhere to
put sqft. The workspace is the only place it can live.
"""

from __future__ import annotations

import pytest
from f1s2_support import (
    WORKSPACES_PATH,
    as_derive_body,
    curation,
    curation_of,
    fully_curated,
    is_json_error,
    missing_routes,
)


# ===========================================================================
# the ungated precondition — this is what makes the red state non-zero
# ===========================================================================


def test_the_three_workspace_routes_exist(app) -> None:
    """ARCHITECTURE.md §4's workspace API, in one loud failure.

    Deliberately NOT gated: every other test in this directory skips while the
    story is unimplemented, and a suite of skips exits 0, which is
    indistinguishable from a pass (WORKFLOW.md §2 — the defect class that has
    recurred four times here). This test carries the exit code.
    """
    absent = missing_routes(app)
    assert not absent, (
        f"F1-S2's API is missing: {', '.join(absent)}. ARCHITECTURE.md §4 specifies "
        f"`GET {WORKSPACES_PATH}` (the recents index) and `GET/PUT {WORKSPACES_PATH}/{{key}}` "
        "(load/save curation state); every other F1-S2 test skips until they exist."
    )


def test_the_fixture_pull_is_adequate(pull, comp_keys) -> None:
    """The harness itself, ungated — so an inadequate fixture fails loudly
    rather than making every test in this directory silently vacuous.

    Runs today, before any F1-S2 code exists, because the fixtures it checks
    (a real `POST /api/search` in fixture mode, a real cache entry, real
    `comp_key`s off a real derive) are QA's to get right and the developer
    should never have to debug them.
    """
    assert len(pull.ref) == 64, (
        f"the pull ref {pull.ref!r} is not a cache key; workspaces are keyed on the cache key "
        "and this fixture would be testing something else"
    )
    assert pull.entry.is_dir(), f"no cache entry was written at {pull.entry}"
    assert pull.raw_snapshot(), (
        "the fixture pull filed no raw responses, so every 'the evidence is untouched' "
        "assertion downstream would be vacuous"
    )
    assert len(set(comp_keys)) >= 2, f"comp keys are not distinct: {comp_keys!r}"


# ===========================================================================
# AC1 — full state restored
# ===========================================================================


def test_a_complete_curation_state_round_trips_field_for_field(
    workspaces, saved_workspace
) -> None:
    """Every field of the saved state comes back with the value it was saved
    with.

    "Restored exactly as last left" is the whole point of the story: a
    workspace that comes back with anything defaulted has silently thrown away
    the user's curation, and the user has no way to notice until a number is
    wrong.
    """
    key, state = saved_workspace

    response = workspaces.load(key)
    assert response.status_code == 200, (
        f"GET {WORKSPACES_PATH}/{{key}} for a workspace just saved returned "
        f"{response.status_code}: {response.text[:400]}"
    )
    restored = curation_of(response.json())

    for field in ("weights", "include_overrides", "filters", "drift_pct", "candidate_rent"):
        assert restored.get(field) == state[field], (
            f"`{field}` did not survive the round trip: saved {state[field]!r}, restored "
            f"{restored.get(field)!r}"
        )


def test_candidate_rent_survives_the_round_trip(workspaces, saved_workspace) -> None:
    """The AC names this field explicitly, so it gets its own test.

    It is the single most droppable value in the state: it is the only one that
    is legitimately `None` most of the time (`DeriveRequest`: "None on the
    Results view — no price has been tested yet"), which is exactly the shape a
    `if value:`-style serializer discards.
    """
    key, state = saved_workspace
    restored = curation_of(workspaces.load(key).json())

    assert restored.get("candidate_rent") == state["candidate_rent"] == 2450.0, (
        f"candidate rent came back as {restored.get('candidate_rent')!r} instead of "
        "2450.0. The AC names it by name: a user who tested a price and came back "
        "tomorrow must find that price still tested."
    )


def test_a_never_tested_candidate_rent_stays_absent_rather_than_becoming_a_number(
    workspaces, pull, comp_keys
) -> None:
    """`None` is a real value here and must not be laundered into a default.

    The mirror of the test above: a workspace saved before any price was tested
    must not come back with a price in the box, because F11's price test would
    then render a result the user never asked for.
    """
    state = curation(pull.ref, weights={comp_keys[0]: 2.0}, candidate_rent=None)
    assert workspaces.save(pull.ref, state).status_code < 300

    restored = curation_of(workspaces.load(pull.ref).json())
    assert restored.get("candidate_rent") is None, (
        f"a workspace saved with no candidate rent came back with "
        f"{restored.get('candidate_rent')!r}"
    )


def test_every_filter_setting_survives_the_round_trip(workspaces, saved_workspace) -> None:
    """The AC's other named field, one assertion per setting.

    A filter that silently resets changes `included + excluded + filtered`
    (F7-S1) and therefore every weighted statistic downstream — a restored
    workspace showing different numbers than it did yesterday, for no reason
    the user can see.
    """
    key, state = saved_workspace
    restored = curation_of(workspaces.load(key).json())
    filters = restored.get("filters")

    assert isinstance(filters, dict), f"filters came back as {filters!r}"
    for field, expected in state["filters"].items():
        assert filters.get(field) == expected, (
            f"filter `{field}` came back as {filters.get(field)!r} instead of {expected!r}"
        )


def test_a_zero_weight_survives_the_round_trip(workspaces, saved_workspace, comp_keys) -> None:
    """Weight 0 is a deselection the user made, not an absent value.

    "Selection IS the weight" (F5-S2 [INVARIANT], `models/requests.py`), so a
    store that drops falsy values silently re-includes every comp the user
    turned off — and every statistic moves.
    """
    key, _ = saved_workspace
    restored = curation_of(workspaces.load(key).json())
    weights = restored.get("weights") or {}

    assert comp_keys[1] in weights, (
        f"the comp deselected with weight 0 ({comp_keys[1]!r}) is absent from the restored "
        f"weights {weights!r}. Toggle-off IS weight 0; dropping it silently re-includes a "
        "comp the user excluded."
    )
    assert weights[comp_keys[1]] == 0.0
    assert weights.get(comp_keys[0]) == 3.5, "the non-default weight did not survive either"


def test_a_restored_workspace_derives_byte_identically(workspaces, saved_workspace) -> None:
    """The strongest form of "exactly as last left", and the one that cannot be
    faked by a store that keeps the right field names with the wrong meanings.

    Deliberately not a field comparison: it pins the OUTCOME (the same evidence,
    curated the same way, produces the same numbers) rather than any storage
    shape, so a developer is free to change the envelope and still pass.
    """
    key, state = saved_workspace

    before = workspaces.derive(state)
    assert before.status_code == 200, f"deriving the saved state failed: {before.text[:400]}"

    after = workspaces.derive(as_derive_body(workspaces.load(key).json(), key))
    assert after.status_code == 200, (
        f"the RESTORED state failed to derive ({after.status_code}): {after.text[:400]}. A "
        "workspace that cannot be fed back to /api/derive has not been restored."
    )
    assert after.json() == before.json(), (
        "the restored workspace derives different numbers than the workspace that was saved. "
        "Something in the curation state was dropped, defaulted or coerced on the way through "
        "disk — the user would open a recent search and see a different answer than they left."
    )


def test_the_subject_unit_survives_the_round_trip(workspaces, saved_workspace) -> None:
    """The subject is part of the restored workspace, sqft above all.

    QA note for the PM (see the module docstring): the story's field list omits
    `subject`, but the epic requires the ANCHOR to come back as last left, the
    anchor is `weighted median $/sqft x subject sqft`, and `SearchRequest` has
    no sqft field — so the workspace is the only place the subject's sqft can
    survive a restart. If the PM rules otherwise, this test is the thing to
    change, not the story.
    """
    key, state = saved_workspace
    restored = curation_of(workspaces.load(key).json())

    assert restored.get("subject") == state["subject"], (
        f"the subject unit came back as {restored.get('subject')!r} instead of "
        f"{state['subject']!r}. Without its sqft the anchor cannot be reproduced."
    )


def test_a_workspace_survives_a_fresh_client(app, workspaces, saved_workspace) -> None:
    """Persistence, not memory — D2's whole point.

    A dict on the app object would pass every test above and lose everything
    the moment the user closes the tool, which is the exact scenario the story
    exists for ("...weeks later: re-open cached search").
    """
    from fastapi.testclient import TestClient

    key, state = saved_workspace
    fresh = TestClient(app, raise_server_exceptions=False)

    response = fresh.get(f"{WORKSPACES_PATH}/{key}")
    assert response.status_code == 200, (
        f"a second client could not read the workspace ({response.status_code}) — the state is "
        "being held in process memory rather than on disk (D2)"
    )
    assert curation_of(response.json()).get("candidate_rent") == state["candidate_rent"]


def test_saving_again_replaces_rather_than_merges(
    workspaces, pull, comp_keys, saved_workspace
) -> None:
    """The second save is the truth. F14-S2 autosaves on every mutation, so a
    store that merged would make a removed weight impossible to remove."""
    key, _ = saved_workspace
    lighter = curation(pull.ref, weights={comp_keys[0]: 1.0}, drift_pct=6.0, candidate_rent=1990.0)
    assert workspaces.save(key, lighter).status_code < 300

    restored = curation_of(workspaces.load(key).json())
    assert restored.get("weights") == {comp_keys[0]: 1.0}, (
        f"weights after the second save are {restored.get('weights')!r} — the first save's "
        "entries survived, so saves are merging instead of replacing"
    )
    assert restored.get("drift_pct") == 6.0
    assert restored.get("candidate_rent") == 1990.0


# ===========================================================================
# the ordinary failure modes — a load must never be a crash either
# ===========================================================================


def test_an_unknown_key_is_a_clean_404(workspaces) -> None:
    """A recents row the user deleted, or a link from an old session."""
    response = workspaces.load("0" * 64)

    assert response.status_code == 404, (
        f"loading a workspace that was never saved returned {response.status_code}, expected "
        f"404: {response.text[:300]}"
    )
    assert is_json_error(response), (
        "the 404 carries no `detail` — an unhandled exception rendered as a status code is "
        "still a crash"
    )


@pytest.mark.parametrize(
    "key",
    [
        "..",
        "../secrets.json",
        "..%2Fsecrets.json",
        "%2e%2e%2f%2e%2e%2fsecrets.json",
        ".hidden",
        "a\x00b",
    ],
)
def test_a_traversing_key_cannot_escape_the_workspaces_directory(
    workspaces, rentcomp_home, key
) -> None:
    """A workspace key arrives from a URL, so `../` is a thing a client can ask
    for — the same reasoning as `storage/pulls.py::_safe_ref`.

    Two things must hold: the read is refused (never a crash), and the write
    lands nowhere. `secrets.json` is named on purpose: it is the file in
    `RENTCOMP_HOME` that must never be reachable, and the API key must never
    be read, printed or logged (CLAUDE.md).
    """
    before = {p for p in rentcomp_home.rglob("*") if p.is_file()}

    read = workspaces.load(key)
    assert read.status_code >= 400, (
        f"GET {WORKSPACES_PATH}/{key!r} returned {read.status_code} — a traversing key must "
        "never resolve to a file"
    )
    assert is_json_error(read) or read.status_code in (400, 404, 422), (
        f"GET {WORKSPACES_PATH}/{key!r} crashed anonymously ({read.status_code})"
    )

    written = workspaces.save(key, curation("0" * 64))
    assert written.status_code >= 400 or before == {
        p for p in rentcomp_home.rglob("*") if p.is_file()
    }, f"PUT {WORKSPACES_PATH}/{key!r} was accepted and wrote a file outside the store"


def test_a_workspace_body_that_is_not_a_curation_state_is_a_422_not_a_500(
    workspaces, pull
) -> None:
    """A malformed save is the client's bug, and it must be named as one.

    `DeriveRequest` already forbids extra fields and negative weights precisely
    so curation that would fail silently fails loudly instead; the workspace
    store is where that state is persisted and must not be a hole in it.
    """
    response = workspaces.save(pull.ref, {"weights": {"x": -1.0}, "nonsense": True})

    assert 400 <= response.status_code < 500, (
        f"a nonsense workspace body returned {response.status_code}: {response.text[:300]}. A "
        "negative weight would corrupt every weighted statistic downstream, and an unknown "
        "field is curation that silently fails to apply."
    )
    assert is_json_error(response)


def test_a_saved_workspace_is_not_required_to_have_been_derived_first(
    workspaces, pull, comp_keys
) -> None:
    """Saving is not a derivation, and must not silently trigger one.

    `POST /api/derive` writes nothing (`api/derive.py`) and `PUT` computes
    nothing; keeping them independent is what makes F14-S2's debounced autosave
    cheap and what keeps D5's "one derivation pass" honest.
    """
    response = workspaces.save(pull.ref, fully_curated(pull.ref, comp_keys))

    assert response.status_code < 300
    body = response.text
    assert "anchor" not in body and "buckets" not in body, (
        "the save response carries derived state — PUT is a write, and a client that got its "
        "numbers from a save response would have two sources of truth for them (D5)"
    )
