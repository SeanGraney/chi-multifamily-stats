"""F4-S9 — `POST /api/search`, the route a search form submits to. Layer 2.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol).

WHICH ACs LIVE HERE, AND WHY
-----------------------------
Run per-AC (AGENT_QA.md decision procedure):

* **AC3 — "mints a `pull_ref` resolvable by the existing storage layer, such
  that `POST /api/derive` with that ref returns a real `DerivedState`"** is a
  two-endpoint claim phrased exactly as step 2's "given state X, the API
  returns Y". It cannot be made at Layer 1 (it spans two routes and the
  request models) and needs no browser. **Layer 2, and this file owns it.**

* **AC5 / AC6 — one representative each.** The split rule: the logic is tested
  exhaustively at Layer 1 (`tests/unit/test_f4s9_pull_orchestrator.py` — five
  budget cases, five partial-pull cases); here we assert *once* that the wiring
  carries it to a caller, because "the orchestrator raised" and "the user was
  told something they can act on" are different facts, and a 500 with the
  reason only in a server log satisfies the first and fails the second.

* **AC4 — one representative.** The route in its default environment is where
  a leaked flag would actually cost money, since this is the surface a browser
  can reach. The exhaustive D17 work is at Layer 1
  (`tests/unit/test_f4s9_live_guard.py`).

* **AC1 / AC2 are NOT here.** "Exactly N wire requests" and "which bytes
  survived a mid-pull failure" are invisible over HTTP; asserting them through
  a route would be a slower, less precise version of a Layer-1 test.

**NO PLAYWRIGHT SPEC FOR THIS STORY.** Every AC is a Python fact, no UI exists
for a search form yet (F2-S1 is BLOCKED), and a browser test here would never
click, type or navigate — AGENT_QA.md's first "wrong layer" smell. When F2-S1
lands, the F2 flow spec covers the user journey and can assert this route's
wiring once from outside.

MONEY: this file never sets `RENTCOMP_LIVE`. It runs entirely in fixture mode
with `RENTCOMP_FIXTURES_DIR` pointed at a seeded temp dataset — which is also
the state the dev server and every Playwright run are in (WORKFLOW.md §6). The
partial-pull case is produced by seeding only 4 of the 6 windows, so a real
gap appears with no network and no injected transport at all.

CONTRACT PINNED HERE (QA-PROPOSED — the PM confirms before it is locked)
------------------------------------------------------------------------
    POST /api/search
    request : {address, radius, bedrooms, bathrooms?, property_types[],
               years_back, window_start, window_end, force_refresh?}
    response: {pull_ref, cache_status, estimated_calls, calls_spent,
               complete, missing[], calls_to_complete}

`cache_status` and `estimated_calls` keep ARCHITECTURE.md §4's own spelling;
`pull_ref`, `complete`, `missing` and `calls_to_complete` mirror the
`PullOutcome` fields pinned at Layer 1, so the number the user is shown and
the number the cap is enforced on are one number (F2-S3 is [INVARIANT] on
exactly that).

EXPECTED RED STATE TODAY: `test_the_search_route_exists` FAILS naming what is
missing; every other test here SKIPS behind it.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import (
    MONTHLY_CALL_CAP,
    Ledger,
    current_month,
    load_ledger,
    save_ledger,
)

SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"

LIVE_ENV = "RENTCOMP_LIVE"
KEY_ENV = "RENTCAST_API_KEY"
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

#: `date.today()` is what the route supplies as the pull's `today`/`as_of`
#: (Layer 1 pins that the orchestrator itself never reads the clock). The
#: full-calendar-year window below is fetchable on every one of its 3 cohort
#: years whatever today happens to be, so this suite is date-independent.
TODAY = date.today()

#: The wire body. `window_start`/`window_end` are the API-edge spelling of the
#: planner's `window_start_mmdd`/`window_end_mmdd`.
SEARCH_BODY: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start": "01-01",
    "window_end": "12-31",
}

#: The same search, in the planner's own argument names.
PLAN_ARGS = {
    "address": SEARCH_BODY["address"],
    "radius": SEARCH_BODY["radius"],
    "bedrooms": SEARCH_BODY["bedrooms"],
    "bathrooms": SEARCH_BODY["bathrooms"],
    "property_types": SEARCH_BODY["property_types"],
    "years_back": SEARCH_BODY["years_back"],
    "window_start_mmdd": SEARCH_BODY["window_start"],
    "window_end_mmdd": SEARCH_BODY["window_end"],
}

SUBJECT: dict[str, Any] = {
    "address": "3651 S Wood St Unit 2",
    "lat": 41.82,
    "lng": -87.67,
    "sqft": 1000.0,
    "beds": 3.0,
    "baths": 1.0,
}


def _record(n: int) -> dict:
    """One synthetic record, distinct per `n` so evidence lost between the
    route and the pipeline shows up as a missing comp rather than as nothing."""
    return {
        "id": f"f4s9-route-{n}",
        "formattedAddress": f"{300 + n} W Route St, Chicago, IL 60609",
        "addressLine1": f"{300 + n} W Route St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + 10 * n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{(TODAY - timedelta(days=150 + n)).isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


def route_present(app: Any, path: str, method: str = "POST") -> bool:
    """True when `app` exposes `method path`.

    Asked of the OpenAPI document, for the same reason `tests/api/conftest.py`
    does: FastAPI keeps an included router as a single nested entry in
    `app.routes`, so a naive scan reports "missing" for every router-registered
    endpoint. Reimplemented locally rather than imported from `conftest` so
    this module never depends on `conftest` being importable by name.
    """
    try:
        operations = (app.openapi().get("paths") or {}).get(path) or {}
    except Exception:  # pragma: no cover - defensive
        operations = {}
    if method.lower() in operations:
        return True
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) != path:
            continue
        if method in (getattr(route, "methods", None) or {method}):
            return True
    return False


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode, pointed at a temp dataset seeded per planned window.

    `seed(n)` writes saved responses for the FIRST `n` fetchable queries and
    returns the plan. Seeding fewer than all six is how this file produces a
    genuine partial pull with no network: `RentCastClient` answers a window it
    has no saved response for with `FixtureMissingError`, which is the same
    "this window never arrived" condition a 503 produces on the live path.
    """
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in (LIVE_ENV, KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed(count: int | None = None):
        plan = plan_pull_queries(TODAY, **PLAN_ARGS)
        queries = fetchable_queries(plan)
        assert len(queries) == 6, (
            f"the fixture search no longer plans 6 fetchable windows ({len(queries)}) — "
            "fix the fixture, not the assertion"
        )
        for index, query in enumerate(queries if count is None else queries[:count]):
            body = json.dumps([_record(index)]).encode("utf-8")
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)
        clear_caches()
        return queries

    return seed


@pytest.fixture
def search_client(app, rentcomp_home):
    """A `TestClient` for the search route, or a legible skip.

    Layer 2 (D21): no server bound to a port, no network. Skipping rather than
    erroring while the route is unimplemented is what keeps the red state
    readable — one named failure, N skips.
    """
    if not route_present(app, SEARCH_PATH):
        pytest.skip(f"POST {SEARCH_PATH} not implemented yet (F4-S9 red)")
    from fastapi.testclient import TestClient

    return TestClient(app)


def derive_body(pull_ref: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pull_ref": pull_ref,
        "subject": dict(SUBJECT),
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 7.0,
        "candidate_rent": None,
    }
    payload.update(overrides)
    return payload


# ===========================================================================
# existence — the one loud failure
# ===========================================================================


def test_the_search_route_exists(app) -> None:
    """Legible red: everything else in this file skips behind this.

    ARCHITECTURE.md §4 already names `POST /api/search` as the surface a
    search submits to; F4-S9 is the story that finally makes it real, because
    nothing in the codebase calls `RentCastClient.fetch_listings` today.
    """
    assert route_present(app, SEARCH_PATH), (
        f"POST {SEARCH_PATH} is not in the app's OpenAPI document. ARCHITECTURE.md §4 "
        "names this route; F4-S9 owns it — it is what a search form submits to, and what "
        "mints the pull_ref /api/derive consumes."
    )


# ===========================================================================
# AC3 — a minted ref derives
# ===========================================================================


def test_a_search_mints_a_ref_that_derive_turns_into_a_real_state(
    search_client, seeded
) -> None:
    """AC3, whole. The one assertion that proves the four modules are actually
    connected: plan -> fetch -> cache -> ref -> loader -> pipeline -> response.

    "Real" is asserted, not assumed: `/api/derive` must come back 200 with the
    evidence this pull put on disk, with provenance pointing back at the ref,
    and with `as_of` equal to the pull's own fetch date — not the wall clock
    at derive time (owner ruling 1: censored means still active *as of the
    pull*).
    """
    seeded()
    response = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY))
    assert response.status_code == 200, (
        f"POST {SEARCH_PATH} returned {response.status_code}: {response.text[:600]}"
    )
    payload = response.json()
    ref = payload.get("pull_ref")
    assert isinstance(ref, str) and ref, (
        f"the search response carries no usable `pull_ref`: {payload!r}"
    )

    derived = search_client.post(DERIVE_PATH, json=derive_body(ref))
    assert derived.status_code == 200, (
        f"POST {DERIVE_PATH} with the freshly minted ref {ref!r} returned "
        f"{derived.status_code}: {derived.text[:600]}. AC3: the ref a search mints must be "
        "resolvable by the storage layer that already exists."
    )
    state = derived.json()

    assert len(state["comps"]) == 6, (
        f"6 windows each returned 1 distinct in-window record; /api/derive sees "
        f"{len(state['comps'])} comps. Evidence was lost between the pull and the pipeline."
    )
    assert state["meta"]["pull_ref"] == ref
    assert state["meta"]["as_of"] == TODAY.isoformat(), (
        f"meta.as_of is {state['meta']['as_of']}, expected the pull's own fetch date "
        f"{TODAY.isoformat()}"
    )
    assert state["meta"]["pull_digest"], "provenance must carry the pull's content digest"
    assert state["anchor"] is not None, (
        "six comps with sqft and a price produced no anchor — the pulled evidence is not "
        "reaching the derivation"
    )


def test_an_identical_search_returns_the_same_ref(search_client, seeded) -> None:
    """AC3's other half: the ref is a function of the search, not of the clock
    or of insertion order. F1's recents list and F14's workspace files are both
    keyed on it, so a ref that drifted between two identical searches would
    silently orphan the user's curation."""
    seeded()
    first = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]
    second = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]
    assert first == second


def test_a_different_search_returns_a_different_ref(search_client, seeded) -> None:
    """The complement — otherwise "the same ref" above is satisfied by
    returning one constant, and two different searches would share (and
    overwrite) one another's evidence."""
    seeded()
    first = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]
    other = search_client.post(
        SEARCH_PATH, json={**SEARCH_BODY, "radius": 5.0}
    ).json()["pull_ref"]
    assert first != other, (
        "two searches differing in radius minted the same pull_ref — one search's comps "
        "would be served for the other"
    )


# ===========================================================================
# AC4 — one representative: the route, in the default environment
# ===========================================================================


def test_the_route_in_its_default_environment_spends_nothing(
    search_client, seeded, monkeypatch
) -> None:
    """AC4 [INVARIANT], representative. This is the surface a browser reaches,
    so it is where a leaked flag would cost money unattended — six calls per
    submit, one submit per keystroke away from a debounce bug.

    `httpx.Client` is booby-trapped for the duration: not "no socket was
    opened" but "nothing even decided to build a transport", which is the
    decision D17 governs.
    """
    attempts: list[str] = []

    def _boom(*args, **kwargs):
        attempts.append("httpx.Client")
        raise AssertionError(
            f"POST {SEARCH_PATH} built an HTTP transport with no {LIVE_ENV}=1 set"
        )

    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    seeded()

    response = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY))

    assert attempts == [], f"a transport was built in fixture mode: {attempts}"
    assert response.status_code == 200
    assert response.json()["calls_spent"] == 0, (
        "fixture mode reported spending calls. A saved response costs nothing and must "
        "never be counted against the 50/month cap."
    )
    assert load_ledger().calls_this_month == 0, "a fixture-mode search moved the ledger"


# ===========================================================================
# AC5 — one representative: the caller can act on the refusal
# ===========================================================================


def test_an_unaffordable_search_is_refused_with_something_the_caller_can_use(
    search_client, seeded, monkeypatch
) -> None:
    """AC5, representative (the five budget cases are exhaustive at Layer 1).

    Two separate failures are forbidden here. A 500 is one: the reason lands
    only in a server log the user is not reading, and spec §7 requires the
    error surfaced plainly. A 200 that quietly did half the pull is the other,
    and is worse — the calls are spent and the missing cohort skews every
    number computed from what came back (D24 §5a).

    **THE ONE TEST IN THIS REPO THAT SETS `RENTCOMP_LIVE=1` WITHOUT INJECTING A
    TRANSPORT — flagged to the PM, and here is the safety argument.** It has to:
    a budget refusal is a LIVE-path fact (fixture mode spends nothing, so
    refusing there would be a bug — see
    `test_f4s9_live_guard.py::test_a_spent_budget_does_not_block_a_fixture_mode_pull`),
    and a Layer-2 test reaches the route over HTTP with nowhere to inject a
    transport. The whole point of the assertion is that the pull is refused
    *before anything is sent*, so on a correct implementation no transport is
    ever constructed. Three independent guards make a wrong implementation fail
    loudly instead of spending: `httpx.Client`/`httpx.request`/`httpx.get` are
    booby-trapped below, the key is fake, and `tests/conftest.py`'s autouse
    socket kill switch is underneath all of it.
    """
    seeded()
    attempts: list[str] = []

    def _boom(*args, **kwargs):
        attempts.append("transport")
        raise AssertionError(
            "an unaffordable search built a transport instead of refusing up front — "
            "the budget check must precede the first send (AC5)"
        )

    monkeypatch.setattr(httpx.Client, "__init__", _boom)
    monkeypatch.setattr(httpx, "request", _boom, raising=False)
    monkeypatch.setattr(httpx, "get", _boom, raising=False)
    monkeypatch.setenv(LIVE_ENV, "1")
    monkeypatch.setenv(KEY_ENV, "qa-f4s9-route-fake-key-not-a-real-credential")

    save_ledger(
        Ledger(month=current_month(), calls_this_month=MONTHLY_CALL_CAP - 5, history=())
    )

    response = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY))

    assert attempts == [], f"a transport was built before the budget was checked: {attempts}"

    assert response.status_code != 500, (
        f"an unaffordable search returned 500: {response.text[:400]}. Being out of calls is "
        "a known, expected condition with a specific user action attached, not a crash."
    )
    assert 400 <= response.status_code < 500, (
        f"expected a 4xx naming the shortfall, got {response.status_code}: "
        f"{response.text[:400]}"
    )
    body = response.text
    assert "6" in body and "5" in body, (
        "the refusal does not state what the pull would have cost (6) against what the "
        f"month has left (5), so the user cannot decide whether to narrow the search: {body[:400]}"
    )
    assert load_ledger().calls_this_month == MONTHLY_CALL_CAP - 5, (
        "the ledger moved on a search that was refused"
    )


# ===========================================================================
# AC6 — one representative: the gap reaches the caller
# ===========================================================================


def test_a_partial_pull_names_its_gap_in_the_response(search_client, seeded) -> None:
    """AC6, representative (the five partial-pull cases are exhaustive at
    Layer 1). D24/§5a: "a missing cohort skews every downstream number and
    must never be silently absent."

    Four of six windows have a saved response; the other two have nothing,
    which is the same "never arrived" condition a 503 produces on the live
    path. The response must say so — F4-S6 renders this verbatim ("2025
    inactive missing · 1 call to complete") and has nothing else to render
    from.
    """
    queries = seeded(4)
    response = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY))

    assert response.status_code == 200, (
        f"a partial pull returned {response.status_code}: {response.text[:400]}. §5a: an "
        "incomplete first pull is USABLE with the gap named loudly — with a 50-call cap, "
        "refusing to show anything until the set is perfect would strand the user."
    )
    payload = response.json()
    assert payload["complete"] is False
    missing = payload["missing"]
    assert len(missing) == 2, (
        f"2 of 6 windows never arrived but the response reports {missing!r}"
    )
    assert payload["calls_to_complete"] == 2

    blob = " ".join(str(m) for m in missing).lower()
    for query in queries[4:]:
        assert str(query.year) in blob and query.status.lower() in blob, (
            f"the missing window ({query.year}, {query.status}) is not identifiable in "
            f"{missing!r} — a gap the user cannot name is a gap they cannot fix"
        )


def test_a_partial_pull_still_derives(search_client, seeded) -> None:
    """AC6 x §5a: the gap is a label on the evidence, not a reason to withhold
    it. The four windows that did arrive must still price the unit."""
    seeded(4)
    ref = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]

    derived = search_client.post(DERIVE_PATH, json=derive_body(ref))
    assert derived.status_code == 200, (
        f"a partial pull's ref would not derive ({derived.status_code}): "
        f"{derived.text[:400]}"
    )
    assert len(derived.json()["comps"]) == 4


def test_a_complete_search_reports_no_gap(search_client, seeded) -> None:
    """AC6's complement. Without it, "always report a gap" passes everything
    above — and F4-S6 shows a permanent, false "evidence incomplete" warning,
    which trains the user to ignore the one warning that matters."""
    seeded()
    payload = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()
    assert payload["complete"] is True
    assert payload["missing"] == []
    assert payload["calls_to_complete"] == 0


# ===========================================================================
# ARCHITECTURE.md §4 — flagged to the PM, not one of the six ACs
# ===========================================================================


def test_a_cached_search_does_not_refetch_without_force_refresh(
    search_client, seeded, tmp_path
) -> None:
    """ARCHITECTURE §4: "`/api/search` never hits the network unless
    `force_refresh: true`" — listed in §8 as a Layer-2 case. NOT one of the
    PM's six ACs, so it is flagged in the handoff rather than assumed: F4-S9
    is the story that creates this route, so it is the story that either
    honours the rule or leaves it to F3-S2/F13.

    The literal, uncontroversial core of the rule is asserted — a repeat of a
    search already satisfied on disk re-fetches nothing — and the ambiguous
    half is deliberately left alone (whether a cache MISS may pull without an
    explicit `force_refresh`; F2's flow says it does, §4's sentence read
    literally says it does not — see the handoff's ambiguity note).

    Proven by removing the saved responses before the second call: if it
    re-fetched, every window would come back missing.
    """
    seeded()
    first = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()
    assert first["complete"] is True

    for saved in (tmp_path / "live-samples").glob("*.json"):
        saved.unlink()

    second = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()

    assert second["pull_ref"] == first["pull_ref"]
    assert second["complete"] is True, (
        "a repeat of an already-satisfied search went back to the source instead of "
        "serving what is already on disk. `cache/` is immutable raw responses precisely "
        "so this is free (ARCHITECTURE §5); on the live path it would be 6 of 50 calls."
    )
    assert second["calls_spent"] == 0
    assert second["cache_status"] == "hit", (
        f"cache_status is {second.get('cache_status')!r} for a search served entirely "
        "from disk (ARCHITECTURE §4 names this field, and F3-S2's modal branches on it)"
    )


# ===========================================================================
# AC6, end-to-end reading — PM DECISION REQUESTED, see the handoff
# ===========================================================================


def test_derive_reports_the_partial_pull_in_its_provenance(search_client, seeded) -> None:
    """AC6 read end-to-end rather than at the manifest.

    **PM: this test asks whether F4-S9 also wires the gap through to
    `/api/derive`, or whether that belongs to F4-S6 / a later story.** The AC's
    own words set the bar at "the manifest says which", and the two tests above
    plus five at Layer 1 meet that bar without this one. If you rule this out
    of scope, this single test moves with the story that owns it and nothing
    else in the F4-S9 suite changes.

    The argument for it being here: `models/responses.py` already carries
    `DeriveMeta.partial_pull: PartialPullInfo | None` with exactly the two
    fields this needs (`missing`, `calls_to_complete`), and WS-1a logged it as
    one of three placeholders "always None" awaiting the story that could fill
    it in honestly. F4-S9 is the first story that knows the answer. Until
    something sets it, a partial pull renders in the UI identically to a
    complete one, which is the silence D24 §5a forbids.
    """
    seeded(4)
    ref = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]

    state = search_client.post(DERIVE_PATH, json=derive_body(ref)).json()
    partial = state["meta"]["partial_pull"]

    assert partial is not None, (
        "meta.partial_pull is null for a pull that is missing 2 of 6 windows. A missing "
        "cohort skews every number in this response and must never be silently absent "
        "(D24 §5a) — see this test's docstring for the scope question."
    )
    assert len(partial["missing"]) == 2
    assert partial["calls_to_complete"] == 2


def test_derive_reports_no_gap_for_a_complete_pull(search_client, seeded) -> None:
    """The complement of the above, and the reason it is safe: a complete pull
    must keep reporting `partial_pull: null`, so the warning means something
    when it does appear."""
    seeded()
    ref = search_client.post(SEARCH_PATH, json=dict(SEARCH_BODY)).json()["pull_ref"]
    state = search_client.post(DERIVE_PATH, json=derive_body(ref)).json()
    assert state["meta"]["partial_pull"] is None
