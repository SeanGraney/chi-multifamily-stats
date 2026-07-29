"""F1-S2 (Layer 2, developer-authored) — the gap in QA's corruption coverage.

`backend/tests/api/f1s2/test_f1s2_corrupt.py` is exhaustive about one half of
F1's edge — "**corrupt**/missing cache entry" where the corrupt thing is the
*workspace file*. This file covers the other half, which nothing else reaches:
the workspace file is perfectly readable and the **cache entry behind it** is
broken.

That is not a hypothetical gap. The first implementation of
`storage/pulls.py::pull_exists` enumerated `(CacheMissError, OSError,
ValueError, KeyError)`, which does not catch the `TypeError` a right-shape-
wrong-types manifest raises inside coercion — the identical escape F4-S9 found
twice. `pull_exists` has exactly one caller and that caller builds the recents
index, so the escape was not a bad row: it was an anonymous 500 for the whole
Home screen. Fixed by importing `CORRUPT_MANIFEST_ERRORS`; pinned here at the
edge, because the edge is where the AC's "never a crash" is actually claimed.

Zero API calls throughout, structurally: nothing in this file (or in the module
under test) can reach the source, and the suite-wide `no_network` kill switch
is autouse above it.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

WORKSPACES_PATH = "/api/workspaces"
KEY = "a" * 64
OTHER_KEY = "b" * 64

#: Every way the manifest behind a healthy workspace can be broken. The first
#: is the one that escaped; the rest are the F4-S9 shapes, so a future narrowing
#: of the handler fails here rather than in production.
BROKEN_MANIFESTS = {
    "wrong types": json.dumps(
        {"as_of": 17, "window": None, "planned": "lots", "fetchable": "some", "queries": 5}
    ),
    "as_of is a number": json.dumps(
        {"as_of": 20260701, "window": ["01-01", "12-31"], "planned": 1, "fetchable": 1,
         "queries": []}
    ),
    "queries hold the wrong thing": json.dumps(
        {"as_of": "2026-07-01", "window": ["01-01", "12-31"], "planned": 1, "fetchable": 1,
         "queries": ["nope"]}
    ),
    "unparseable": "{ not json",
    "empty": "",
    "null": "null",
    "an array": "[]",
    "none of the fields": json.dumps({"note": "hand-edited"}),
}


@pytest.fixture
def api(app, rentcomp_home):
    """A client that renders an unhandled exception as a 500 *response*.

    Load-bearing: with `raise_server_exceptions=False` a crash comes back as
    bare plain text with no `detail`, while a deliberate `HTTPException` comes
    back as JSON. That difference is the whole discriminator below.
    """
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


def _curation(pull_ref: str, **overrides) -> dict:
    payload = {
        "pull_ref": pull_ref,
        "subject": {
            "address": "3651 S Wood St Unit 2",
            "lat": 41.8286,
            "lng": -87.6716,
            "sqft": 1000.0,
            "beds": 3.0,
            "baths": 1.0,
        },
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 0.0,
        "candidate_rent": None,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def entry(rentcomp_home):
    """`entry(key) -> Path` — a complete cache entry, filed the way a pull is."""
    from rentcomp.storage.cache import write_manifest, write_raw_response

    def _make(key: str):
        write_raw_response(key, "sig0-off000", b"[]", meta={"sig": "sig0"})
        write_manifest(key, as_of=date(2026, 7, 1), window=("01-01", "12-31"),
                       planned=1, fetchable=1)
        return rentcomp_home / "cache" / key

    return _make


def _rows(response) -> dict:
    body = response.json()
    rows = body if isinstance(body, list) else body.get("workspaces", body.get("items", []))
    return {row["key"]: row for row in rows}


# ===========================================================================
# the recents list survives a broken cache entry, in every shape
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(BROKEN_MANIFESTS))
def test_a_broken_manifest_does_not_take_down_the_recents_list(api, entry, shape) -> None:
    """Home is the app's front door and it renders on launch.

    A 500 here is not a degraded row, it is a user who cannot open the tool at
    all — and their curation is intact on disk the whole time.
    """
    broken = entry(KEY)
    healthy = entry(OTHER_KEY)
    assert api.put(f"{WORKSPACES_PATH}/{KEY}", json=_curation(KEY)).status_code < 300
    assert api.put(f"{WORKSPACES_PATH}/{OTHER_KEY}", json=_curation(OTHER_KEY)).status_code < 300
    (broken / "manifest.json").write_text(BROKEN_MANIFESTS[shape], encoding="utf-8")

    response = api.get(WORKSPACES_PATH)

    assert response.status_code == 200, (
        f"a {shape} manifest made GET {WORKSPACES_PATH} return {response.status_code}: "
        f"{response.text[:300]}"
    )
    rows = _rows(response)
    assert set(rows) == {KEY, OTHER_KEY}, f"a row disappeared: {sorted(rows)}"
    assert rows[KEY]["error"], (
        f"the row over a {shape} manifest is not marked as an error: {rows[KEY]}. It renders "
        "identically to a healthy row, so the user clicks it and lands on an empty Results "
        "view, which reads as 'there are no comps in this market'."
    )
    assert rows[KEY]["offer_refresh"] is True, (
        f"no way out of the error row: {rows[KEY]}. A full re-pull is the only thing that can "
        "rebuild the evidence."
    )
    assert rows[OTHER_KEY]["error"] is None, (
        f"a broken neighbour made the healthy row report an error too: {rows[OTHER_KEY]}"
    )
    assert healthy.is_dir()


@pytest.mark.parametrize("shape", sorted(BROKEN_MANIFESTS))
def test_a_broken_manifest_is_never_worked_around_by_re_pulling(
    api, entry, monkeypatch, shape
) -> None:
    """D24: a corrupt file is not a reason to buy the evidence again.

    On the live path that is six of fifty monthly calls spent to route around a
    file the user could delete for free. Asserted by bricking up the one door
    `client/pull.py` reaches the source through, in fixture mode and live alike.
    """
    from rentcomp.client import rentcast as rentcast_module

    broken = entry(KEY)
    assert api.put(f"{WORKSPACES_PATH}/{KEY}", json=_curation(KEY)).status_code < 300
    (broken / "manifest.json").write_text(BROKEN_MANIFESTS[shape], encoding="utf-8")
    raw_before = {p.name: p.read_bytes() for p in (broken / "raw").glob("*")}

    attempts: list = []

    def _boom(self, params, *args, **kwargs):  # noqa: ANN001
        attempts.append(dict(params))
        raise AssertionError("a broken manifest sent the app back to RentCast")

    monkeypatch.setattr(rentcast_module.RentCastClient, "fetch_listings", _boom, raising=True)

    assert api.get(WORKSPACES_PATH).status_code == 200
    api.get(f"{WORKSPACES_PATH}/{KEY}")

    assert attempts == [], f"a {shape} manifest triggered a fetch: {attempts}"
    assert {p.name: p.read_bytes() for p in (broken / "raw").glob("*")} == raw_before


@pytest.mark.parametrize("shape", sorted(BROKEN_MANIFESTS))
def test_reading_a_broken_manifest_neither_repairs_nor_deletes_it(api, entry, shape) -> None:
    """A read is never a repair. The user's route out is refresh or delete, and
    both need the entry to still be there when they get to it."""
    broken = entry(KEY)
    assert api.put(f"{WORKSPACES_PATH}/{KEY}", json=_curation(KEY)).status_code < 300
    path = broken / "manifest.json"
    path.write_text(BROKEN_MANIFESTS[shape], encoding="utf-8")
    before = path.read_bytes()

    api.get(WORKSPACES_PATH)
    api.get(f"{WORKSPACES_PATH}/{KEY}")

    assert path.exists() and path.read_bytes() == before


# ===========================================================================
# loading is about the curation, not about the evidence
# ===========================================================================


def test_a_workspace_still_loads_when_the_evidence_behind_it_is_broken(api, entry) -> None:
    """The curation state is the thing being restored, and it is fine.

    Refusing the load would destroy the reversibility the whole store exists
    for: the user's weights survive a refresh precisely because they are not
    stored inside the pull they curate (§5). The recents row already says the
    evidence is unusable; the load has nothing to add.
    """
    broken = entry(KEY)
    assert api.put(
        f"{WORKSPACES_PATH}/{KEY}", json=_curation(KEY, candidate_rent=2450.0)
    ).status_code < 300
    (broken / "manifest.json").write_text("{ not json", encoding="utf-8")

    response = api.get(f"{WORKSPACES_PATH}/{KEY}")

    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
    assert response.json()["state"]["candidate_rent"] == 2450.0


def test_saving_is_never_refused_for_evidence_that_is_gone(api) -> None:
    """Curation outlives the pull it curates.

    F14-S2 autosaves on every mutation; a save that validated the evidence
    would start failing mid-session the moment a cache entry was deleted in
    another window, and the user's work would stop being written with no
    visible cause.
    """
    response = api.put(f"{WORKSPACES_PATH}/{KEY}", json=_curation(KEY, drift_pct=4.0))

    assert response.status_code < 300, f"{response.status_code}: {response.text[:300]}"
    assert api.get(f"{WORKSPACES_PATH}/{KEY}").json()["state"]["drift_pct"] == 4.0


# ===========================================================================
# zero API calls, asserted over the import graph rather than over one request
# ===========================================================================


def test_the_workspace_route_cannot_reach_the_source_at_all() -> None:
    """"Zero API calls" as a property of the code, not of a request that
    happened to be observed.

    Importing `api/workspaces` must not drag in anything that can fetch. This
    is total over every code path, including ones no test exercises, and it is
    the assertion that keeps holding when someone later adds a "helpfully
    complete the partial pull" convenience to a load — the most expensive
    possible reading of "restore the workspace" (§5a: completing a pull costs
    the remainder, and that is F3-S2's modal decision, never a side effect of
    clicking a row).

    `client.beds_baths` and `client.query` are allowed: they are pure string
    parsers reached through `models/requests.py`, and neither owns a transport.
    """
    import subprocess
    import sys

    probe = (
        "import sys; import rentcomp.api.workspaces; "
        "print(','.join(sorted(m for m in sys.modules if m.startswith('rentcomp.client'))))"
    )
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    ).stdout.strip()
    modules = set(loaded.split(",")) if loaded else set()

    forbidden = {"rentcomp.client.rentcast", "rentcomp.client.pull", "rentcomp.client.planner"}
    assert not (modules & forbidden), (
        f"importing the workspace route pulls in {sorted(modules & forbidden)}. Opening a recent "
        "search must be free BY CONSTRUCTION — there is no code path from here to the source — "
        "rather than by a counter that happens to read zero (it cannot move in fixture mode)."
    )
