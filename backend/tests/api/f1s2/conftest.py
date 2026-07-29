"""F1-S2 [BE] workspace persistence — shared Layer-2 fixtures.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol): `storage/workspace.py` does not exist and none of
`GET /api/workspaces`, `GET/PUT /api/workspaces/{key}` is in the route table on
`main` at 86a9286.

WHY A SUBDIRECTORY
------------------
These fixtures are F1-S2's alone and are useless to the rest of the Layer-2
suite, and `backend/tests/api/conftest.py` belongs to F0-S2. A directory
conftest inherits every parent fixture (`app`, `rentcomp_home`, `clear_caches`,
and the autouse `no_network` socket kill switch) without editing a file another
story owns. Constants and helpers live in `f1s2_support.py` beside this file,
importable by name — `import conftest` would be ambiguous between the two
conftest modules in play.

RED-STATE SHAPE
---------------
`test_f1s2_contract.py::test_the_three_workspace_routes_exist` is UNGATED and
fails loudly, naming every missing route; everything else is gated behind the
`workspaces` fixture (a skip) so the red state stays readable. The run
therefore **exits non-zero** while the story is unimplemented — a skip-only red
state is the false green WORKFLOW.md §2 forbids, so the ungated failure is what
carries the exit code, deliberately.

MONEY (WORKFLOW.md §6, D17)
---------------------------
Fixture mode throughout: `RENTCOMP_LIVE` and `RENTCAST_API_KEY` are removed
from the environment, saved responses live in a temp directory, and the
suite-wide socket kill switch is autouse above this file. Nothing here can
spend a call; the ledger is at 8/50 and these tests cannot move it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from f1s2_support import (
    DERIVE_PATH,
    PLAN_ARGS,
    SEARCH_BODY,
    SEARCH_PATH,
    TODAY,
    WORKSPACES_PATH,
    Pull,
    curation,
    missing_routes,
    record,
)


@pytest.fixture
def fixture_mode(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode, pointed at a temp directory, with a zeroed ledger."""
    from rentcomp.storage.ledger import Ledger, current_month, save_ledger

    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return fixtures


@pytest.fixture
def api(app, rentcomp_home):
    """A `TestClient` that turns an unhandled exception into a 500 *response*
    rather than re-raising it.

    Load-bearing for "never a crash": with `raise_server_exceptions=False` an
    unhandled `AttributeError`/`TypeError` comes back as a plain-text 500 with
    no `detail`, while a deliberate `HTTPException` comes back as JSON. That is
    exactly the distinction two corruption shapes escaped through in F4-S9, so
    these tests assert it rather than trusting a docstring.
    """
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def _seed_and_pull(api, fixture_mode, rentcomp_home, clear_caches, body: dict) -> Pull:
    """Seed one saved response per fetchable window, then run the real search."""
    from rentcomp.client.planner import fetchable_queries, plan_pull_queries
    from rentcomp.client.rentcast import fixture_signature

    plan_args = {
        **PLAN_ARGS,
        "address": body["address"],
        "radius": body["radius"],
        "bedrooms": body["bedrooms"],
        "bathrooms": body["bathrooms"],
        "property_types": body["property_types"],
        "years_back": body["years_back"],
        "window_start_mmdd": body["window_start"],
        "window_end_mmdd": body["window_end"],
    }
    queries = fetchable_queries(plan_pull_queries(TODAY, **plan_args))
    assert queries, (
        "the F1-S2 fixture search has no fetchable window today, so every test built on it "
        "would be vacuous"
    )
    for index, query in enumerate(queries):
        payload = json.dumps([record(index)]).encode("utf-8")
        (fixture_mode / f"{fixture_signature(query.params)}.json").write_bytes(payload)
    clear_caches()

    response = api.post(SEARCH_PATH, json=body)
    assert response.status_code == 200, (
        f"the F1-S2 fixture pull failed ({response.status_code}): {response.text[:400]}. Every "
        "workspace test needs a real cache entry to key a workspace on."
    )
    return Pull(ref=response.json()["pull_ref"], fixtures_dir=fixture_mode, home=rentcomp_home)


@pytest.fixture
def pull(api, fixture_mode, rentcomp_home, clear_caches) -> Pull:
    """A completed fixture-mode pull → a real cache entry, manifest and ref.

    Runs the real `POST /api/search` rather than hand-building a lookalike, so
    the immutability and zero-call assertions are made against the same bytes
    the product writes. Zero network throughout.
    """
    return _seed_and_pull(api, fixture_mode, rentcomp_home, clear_caches, SEARCH_BODY)


@pytest.fixture
def another_pull(api, fixture_mode, rentcomp_home, clear_caches):
    """`another_pull(body) -> Pull` — a second/third cache entry on demand, so
    the recents index has more than one thing to be an index over."""

    def _make(body: dict) -> Pull:
        return _seed_and_pull(api, fixture_mode, rentcomp_home, clear_caches, body)

    return _make


@pytest.fixture
def partial_pull(api, fixture_mode, rentcomp_home, clear_caches) -> Pull:
    """A pull with a real gap in it — one window that never arrived.

    Seeds every fetchable window except the first, so `run_pull` records that
    window as missing (§5a). Reopening a workspace over an incomplete pull must
    still cost nothing: completing it is the user's decision in F3-S2's modal,
    never a side effect of clicking a recent row.
    """
    from rentcomp.client.planner import fetchable_queries, plan_pull_queries
    from rentcomp.client.rentcast import fixture_signature

    queries = fetchable_queries(plan_pull_queries(TODAY, **PLAN_ARGS))
    assert len(queries) >= 2, "need at least two fetchable windows to leave a gap in one"
    for index, query in enumerate(queries[1:], start=1):
        payload = json.dumps([record(index)]).encode("utf-8")
        (fixture_mode / f"{fixture_signature(query.params)}.json").write_bytes(payload)
    clear_caches()

    response = api.post(SEARCH_PATH, json=SEARCH_BODY)
    assert response.status_code == 200, f"the partial fixture pull failed: {response.text[:400]}"
    body = response.json()
    assert body["complete"] is False and body["missing"], (
        "the partial-pull fixture came back complete, so nothing it is used to test would be "
        f"testing a gap: {body}"
    )
    return Pull(ref=body["pull_ref"], fixtures_dir=fixture_mode, home=rentcomp_home)


@pytest.fixture
def no_fetch_allowed(monkeypatch):
    """Arm a tripwire on the only way this process can fetch anything.

    `client/pull.py` reaches the source through exactly one door —
    `RentCastClient.fetch_listings` — in fixture mode and on the live path
    alike. Replacing it with a raiser asserts the ABSENCE OF THE MECHANISM
    rather than the value of a counter: `calls_this_month` cannot move in
    fixture mode, so a ledger assertion here would pass against a route that
    re-pulled everything (the tests-that-cannot-fail shape this project has now
    caught four times).

    Returns the list of attempts, so a test can also assert one was made.
    """
    from rentcomp.client import rentcast as rentcast_module

    attempts: list[Any] = []

    def _boom(self, params, *args, **kwargs):  # noqa: ANN001
        attempts.append(dict(params))
        raise AssertionError(
            "a workspace operation called RentCastClient.fetch_listings. Opening a recent "
            "search is free by construction (F1 'Success: zero API calls'); anything that "
            f"reaches the source here would spend real money on the live path. params={params}"
        )

    monkeypatch.setattr(rentcast_module.RentCastClient, "fetch_listings", _boom, raising=True)
    return attempts


@pytest.fixture
def comp_keys(api, pull) -> list[str]:
    """Real `comp_key`s from the fixture pull — what curation weights are keyed
    on (F13-S1 [INVARIANT])."""
    response = api.post(DERIVE_PATH, json=curation(pull.ref))
    assert response.status_code == 200, (
        f"POST {DERIVE_PATH} against the fixture pull returned {response.status_code}: "
        f"{response.text[:400]}"
    )
    keys = [comp["key"] for comp in response.json()["comps"]]
    assert len(keys) >= 2, (
        f"the fixture pull shaped {len(keys)} comps; these tests need at least two distinct "
        "comp keys to prove a weight map round-trips, so the fixture would be inadequate"
    )
    return keys


@pytest.fixture
def workspaces(app, api):
    """The workspace API, or a legible skip while it does not exist.

    Skipping here (rather than failing N times over) keeps the red state
    readable; `test_the_three_workspace_routes_exist` is ungated and carries
    the exit code, so the suite can never be green while this skips.
    """
    absent = missing_routes(app)
    if absent:
        pytest.skip(f"F1-S2 not implemented yet — missing {', '.join(absent)}")

    class Workspaces:
        """The three routes, plus derive, over one TestClient."""

        def save(self, key: str, state: dict) -> Any:
            return api.put(f"{WORKSPACES_PATH}/{key}", json=state)

        def load(self, key: str) -> Any:
            return api.get(f"{WORKSPACES_PATH}/{key}")

        def recents(self) -> Any:
            return api.get(WORKSPACES_PATH)

        def derive(self, state: dict) -> Any:
            return api.post(DERIVE_PATH, json=state)

    return Workspaces()


@pytest.fixture
def saved_workspace(workspaces, pull, comp_keys):
    """A fully-curated workspace already saved under the fixture pull's key.

    Returns `(key, state)`. Fails rather than skips if the save is rejected —
    every restoration test downstream would otherwise be testing nothing.
    """
    from f1s2_support import fully_curated

    state = fully_curated(pull.ref, comp_keys)
    response = workspaces.save(pull.ref, state)
    assert response.status_code < 300, (
        f"PUT {WORKSPACES_PATH}/{{key}} rejected a complete curation state "
        f"({response.status_code}): {response.text[:400]}"
    )
    return pull.ref, state


@pytest.fixture
def workspace_file(rentcomp_home):
    """`workspace_file(key) -> Path` — the file holding one saved workspace.

    Searched for rather than assumed, so the corruption tests do not depend on
    the [DEFAULT]'s exact filename; `test_f1s2_zero_calls.py` is the one place
    the `workspaces/<cache-key>.json` location itself is pinned.
    """

    def _find(key: str):
        candidates = [
            p
            for p in sorted(rentcomp_home.rglob("*"))
            if p.is_file()
            and "cache" not in p.relative_to(rentcomp_home).parts
            and key in p.name
        ]
        assert candidates, (
            f"no file under {rentcomp_home} (outside cache/) is named for the workspace {key}; "
            "the corruption tests need to be able to corrupt it"
        )
        assert len(candidates) == 1, (
            f"more than one file claims to hold workspace {key}: {candidates}. A second store "
            "that can drift from the first is exactly what 'the recents list is an index over "
            "stored workspaces' forbids."
        )
        return candidates[0]

    return _find


@pytest.fixture
def workspace_files(rentcomp_home):
    """`workspace_files() -> list[Path]` — whatever the store wrote, wherever.

    Deliberately a search of the whole storage root minus `cache/`: the
    [DEFAULT] names `workspaces/<cache-key>.json`, and one test pins that
    location on ARCHITECTURE.md §5's authority, but the corruption tests only
    need "the file that holds this workspace" and must not break if the
    developer files it under a different name.
    """

    def _files() -> list:
        root = rentcomp_home
        return [
            p
            for p in sorted(root.rglob("*"))
            if p.is_file() and "cache" not in p.relative_to(root).parts
        ]

    return _files
