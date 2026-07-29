"""F4-S6 — the *data* half of "names the gap precisely" and "names the binding
constraint". Layer 2 (pytest API contract, FastAPI ``TestClient``).

QA-authored, written before any implementation exists (AGENT_QA.md protocol).

WHY A [FE] STORY HAS A LAYER-2 FILE AT ALL
-------------------------------------------
Because D5/D13 say the view renders what the API returns and computes no
statistic — so every string F4-S6's copy is required to name has to *be* on
the wire first. "2025 Inactive missing · 1 call to complete" is two facts the
backend owns (which window, how many calls) and one rendering the view owns.
This file pins the two facts; ``e2e/tests/f4s6-pipeline-states.spec.ts`` pins
the rendering, once, per the split rule.

Three of these tests PASS on the day they were written. That is deliberate and
it is the finding the PM asked for: F3-S4/F4-S9 already ship
``meta.partial_pull`` with per-window labels and an owed-call count, so the
"names the gap precisely" AC needs **no new backend work** — only a surface.
Pinning it here means the datum the copy depends on cannot quietly regress
while F4-S6 is built on top of it.

The two that FAIL are the boundary the developer must not be asked to guess
at:

* **the resume action has no wire affordance.** ``POST /api/search`` fetches
  on a cache MISS or on ``force_refresh``, and ``force_refresh`` re-fetches
  *every* window (``client/pull.py``: ``known = {} if force_refresh``). So the
  only lever a browser has today turns a 1-call gap into a full re-pull — on
  the live path that is real money, and it is the opposite of §5a's "a resume
  costs exactly the remainder". ``run_pull`` already resumes correctly; what
  is missing is a *request* that asks it to.
* **nothing on the wire says why a comp set is empty.** ``breakdown.pulled``
  is 0 whether RentCast returned nothing at all or returned records that the
  date window then dropped — two different user actions (widen the radius vs
  widen the window). F4-S4 already computes exactly this distinction in
  ``pipeline/shape.py::ShapingSummary`` and ``shape_raw_pull`` throws it away.

MONEY: fixture mode throughout. ``RENTCOMP_LIVE`` is never set, no key is
configured, and the partial pull is produced by seeding fewer saved responses
than the plan asks for — the technique ``test_f4s9_search_route.py``
established. Zero live RentCast calls (D17, WORKFLOW.md §6).
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import Ledger, current_month, save_ledger

SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"

LIVE_ENV = "RENTCOMP_LIVE"
KEY_ENV = "RENTCAST_API_KEY"
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

TODAY = date.today()

#: A full-calendar-year window, so every cohort year is fetchable whatever
#: today happens to be — this suite must not go red in January.
SEARCH_BODY: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 0.5,
    "bedrooms": "3",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start": "01-01",
    "window_end": "12-31",
}

#: The same search with a NARROW window, used only by the empty-state pair:
#: it is what makes "records arrived and the window dropped them all" a
#: reachable state rather than a hypothetical one.
NARROW_WINDOW = {"window_start": "06-15", "window_end": "06-30"}

SUBJECT: dict[str, Any] = {
    "address": "3651 S Wood St Unit 2",
    "lat": 41.82,
    "lng": -87.67,
    "sqft": 1000.0,
    "beds": 3.0,
    "baths": 1.0,
}


def plan_args(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "address": body["address"],
        "radius": body["radius"],
        "bedrooms": body["bedrooms"],
        "bathrooms": body["bathrooms"],
        "property_types": body["property_types"],
        "years_back": body["years_back"],
        "window_start_mmdd": body["window_start"],
        "window_end_mmdd": body["window_end"],
    }


def _record(n: int, listed: date) -> dict:
    """One synthetic listing, distinct per ``n``."""
    return {
        "id": f"f4s6-{n}",
        "formattedAddress": f"{500 + n} W Wood St, Chicago, IL 60609",
        "addressLine1": f"{500 + n} W Wood St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 1900 + 40 * n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": listed.isoformat() + "T00:00:00.000Z",
        "removedDate": (listed + timedelta(days=40)).isoformat() + "T00:00:00.000Z",
        "status": "Inactive",
    }


def route_present(app: Any, path: str, method: str = "POST") -> bool:
    """True when ``app`` exposes ``method path`` (asked of the OpenAPI
    document, for the reason ``tests/api/conftest.py`` gives)."""
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


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(app, rentcomp_home):
    """A ``TestClient``, or a legible skip while the routes are absent.

    Both routes already exist on ``main`` (F4-S9), so this skip should never
    fire — it is here so that a *future* regression reads as one named failure
    rather than as eight confusing ones.
    """
    if not (route_present(app, SEARCH_PATH) and route_present(app, DERIVE_PATH)):
        pytest.skip(f"POST {SEARCH_PATH} / {DERIVE_PATH} not available")
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def seeded(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode pointed at a temp dataset, seeded per planned window.

    ``seed(body, count=None, listed=...)`` writes saved responses for the
    first ``count`` fetchable windows of ``body``'s plan. Seeding fewer than
    all of them is how a genuine partial pull appears with no network at all:
    ``RentCastClient`` answers an unseeded window with ``FixtureMissingError``,
    which is the same "this window never arrived" condition a 503 produces on
    the live path.
    """
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in (LIVE_ENV, KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed(
        body: dict[str, Any],
        count: int | None = None,
        *,
        listed: date | None = None,
        records_per_window: int = 2,
    ):
        # Always start from an empty dataset: a leftover response from an
        # earlier call in the same test would silently satisfy a window this
        # one meant to leave missing.
        for stale in fixtures.glob("*.json"):
            stale.unlink()
        queries = fetchable_queries(plan_pull_queries(TODAY, **plan_args(body)))
        assert queries, "the fixture search plans no fetchable window — fix the fixture"
        for index, query in enumerate(queries if count is None else queries[:count]):
            when = listed if listed is not None else TODAY - timedelta(days=200 + index)
            payload = [
                _record(index * 10 + n, when - timedelta(days=n))
                for n in range(records_per_window)
            ]
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(
                json.dumps(payload).encode("utf-8")
            )
        clear_caches()
        return queries

    return seed


def raw_files(home: Path, pull_ref: str) -> dict[str, tuple[int, bytes]]:
    """Every stored response for ``pull_ref``: name -> (size, bytes).

    Content, not mtime: a filesystem whose timestamps have 1-second resolution
    would make an mtime comparison pass for the wrong reason.
    """
    raw = home / "cache" / pull_ref / "raw"
    if not raw.is_dir():
        return {}
    return {
        path.name: (path.stat().st_size, path.read_bytes())
        for path in sorted(raw.glob("*.json"))
        if not path.name.endswith(".meta.json")
    }


# ===========================================================================
# harness precondition — fails loudly, never skips
# ===========================================================================


def test_the_source_under_test_is_this_checkouts() -> None:
    """WORKFLOW.md §2: in a worktree the editable install's ``.pth`` resolves
    to the MAIN checkout, so a run can look entirely correct while answering
    about the wrong code.

    A real test rather than a note, because the failure mode it guards is
    invisible: no error, no skip, just an answer about a different tree. It is
    a no-op on the main checkout.
    """
    import rentcomp

    loaded = Path(rentcomp.__file__).resolve()
    expected = (Path(__file__).resolve().parents[3] / "backend" / "src" / "rentcomp").resolve()
    assert loaded.parent == expected, (
        f"tests are importing rentcomp from {loaded.parent}, not from this checkout's "
        f"{expected}. Set PYTHONPATH to this checkout's backend/src (on Windows the "
        "separator is ';', not ':') and run pytest BARE from the repo root."
    )


# ===========================================================================
# ALREADY ON THE WIRE — the datum F4-S6's copy renders (regression pins)
# ===========================================================================


def test_partial_pull_names_the_missing_windows_and_the_call_count(client, seeded):
    """[AC5-data] ``meta.partial_pull`` names WHICH windows and HOW MANY calls.

    This is the whole of "names the gap precisely" on the data side, and it
    already works: the labels are the manifest's own (``"2025 Inactive"``), so
    the view renders them verbatim and never reconstructs a year from a clock
    (``client/pull.py::_label``, whose docstring says F4-S6 renders ``missing``
    verbatim).
    """
    queries = seeded(SEARCH_BODY, 0)
    assert len(queries) >= 2, "need at least two windows to leave one missing"
    seeded(SEARCH_BODY, len(queries) - 1)

    search = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert search.status_code == 200, search.text[:400]
    result = search.json()
    assert result["complete"] is False, result
    assert result["missing"], "a pull short one window reported no gap"
    assert result["calls_to_complete"] >= 1, result

    derived = client.post(DERIVE_PATH, json=derive_body(result["pull_ref"]))
    assert derived.status_code == 200, derived.text[:400]
    partial = derived.json()["meta"]["partial_pull"]

    assert partial is not None, (
        "a pull with a missing window derived with meta.partial_pull = null. A missing "
        "cohort skews every number in the payload it travels with (D24 §5a) — without "
        "this field a partial workspace renders identically to a whole one."
    )
    assert partial["missing"] == result["missing"], (
        "the gap named by /api/search and the gap named by /api/derive disagree; the "
        "view would show a different answer depending on which screen you are on"
    )
    assert partial["calls_to_complete"] == result["calls_to_complete"]
    for label in partial["missing"]:
        assert isinstance(label, str) and label.strip(), partial
        year, _, status = label.partition(" ")
        assert year.isdigit() and status, (
            f"window label {label!r} is not the '<year> <Status>' string the copy "
            "'2025 Inactive missing' renders verbatim"
        )


def test_a_whole_pull_carries_no_gap_marker(client, seeded):
    """[AC5-data] ``partial_pull`` is null when nothing is absent.

    The complement of the test above, and the one that makes the banner mean
    something: a marker that is always present is decoration.
    """
    seeded(SEARCH_BODY)
    search = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert search.status_code == 200, search.text[:400]
    result = search.json()
    assert result["complete"] is True, result
    assert result["missing"] == []

    derived = client.post(DERIVE_PATH, json=derive_body(result["pull_ref"]))
    assert derived.status_code == 200, derived.text[:400]
    assert derived.json()["meta"]["partial_pull"] is None


def test_the_call_count_is_not_the_length_of_the_missing_list(client, seeded, rentcomp_home):
    """[AC7-data] ``calls_to_complete`` and ``len(missing)`` are two numbers.

    They diverge for a real reason (``storage/cache.py::QueryStatus.owed``): a
    window holding a 2xx the parser cannot use is *missing* — its evidence is
    unusable — and is *not owed*, because the bytes are already paid for and a
    second call returns the same ones. Pinned here because the browser spec's
    D5 assertion ("the view prints the server's number, not ``missing.length``")
    is only meaningful if the two can genuinely differ.
    """
    from rentcomp.storage.cache import QueryStatus, read_manifest, write_manifest

    queries = seeded(SEARCH_BODY, 0)
    assert len(queries) >= 3, "need three windows to leave two missing and one owed"
    seeded(SEARCH_BODY, len(queries) - 2)

    search = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert search.status_code == 200, search.text[:400]
    ref = search.json()["pull_ref"]

    manifest = read_manifest(ref)
    absent = [q for q in manifest.queries if not q.satisfied]
    assert len(absent) == 2, [q.label for q in manifest.queries]

    # One of the two absent windows holds bytes we paid for and cannot parse:
    # still missing, no longer owed.
    rewritten = tuple(
        QueryStatus(
            label=q.label,
            sig=q.sig,
            satisfied=q.satisfied,
            error=q.error,
            owed=False if q.sig == absent[0].sig else q.is_owed,
        )
        for q in manifest.queries
    )
    write_manifest(
        ref,
        as_of=manifest.as_of,
        window=manifest.window,
        planned=manifest.planned,
        fetchable=manifest.fetchable,
        queries=list(rewritten),
    )

    again = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert again.status_code == 200, again.text[:400]
    result = again.json()
    assert len(result["missing"]) == 2, result
    assert result["calls_to_complete"] == 1, (
        "a window that is missing but not owed was still quoted as a call. "
        "calls_to_complete may only count calls a resume would actually make."
    )

    derived = client.post(DERIVE_PATH, json=derive_body(ref))
    assert derived.status_code == 200, derived.text[:400]
    partial = derived.json()["meta"]["partial_pull"]
    assert len(partial["missing"]) == 2 and partial["calls_to_complete"] == 1, partial


def test_a_pull_that_found_nothing_still_derives(client, seeded, rentcomp_home):
    """[AC2-data] Zero comps is a 200 with an empty evidence set, not an error.

    The empty state is a *render*, so the payload behind it has to exist:
    ``comps: []``, every bucket present with nulls (never an estimate), no
    anchor. Asserted here so the browser spec can be about the words on the
    screen rather than about whether the request survived.
    """
    fixtures_seed = seeded(SEARCH_BODY, 0)  # plan the windows, seed none of them
    assert fixtures_seed, "no fetchable windows planned"

    # Every window answers with an empty list: the API was asked and had
    # nothing — a COMPLETE pull holding no evidence.
    import os

    fixtures = Path(os.environ[FIXTURES_DIR_ENV])
    for query in fixtures_seed:
        (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(b"[]")

    search = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert search.status_code == 200, search.text[:400]
    result = search.json()
    assert result["complete"] is True, result

    derived = client.post(DERIVE_PATH, json=derive_body(result["pull_ref"]))
    assert derived.status_code == 200, derived.text[:400]
    state = derived.json()
    assert state["comps"] == []
    assert state["breakdown"]["pulled"] == 0
    assert state["anchor"] is None
    assert len(state["buckets"]) == 3
    assert all(bucket["count"] == 0 for bucket in state["buckets"])
    assert all(bucket["leased_dom_median"] is None for bucket in state["buckets"])
    assert state["meta"]["partial_pull"] is None, (
        "a complete pull that found nothing must not be reported as a partial pull — "
        "'we looked and there is nothing' and 'we never looked' are different answers "
        "and lead to different user actions"
    )


# ===========================================================================
# NOT ON THE WIRE YET — the two boundaries the developer must be told about
# ===========================================================================


def test_an_empty_result_says_which_constraint_emptied_it(client, seeded, rentcomp_home):
    """[AC2] RED. "0 comps" must distinguish *nothing came back* from
    *everything fell outside the window*.

    Two searches, both ending at ``breakdown.pulled == 0``, that ask the user
    for opposite actions: one wants a wider radius (or different property
    types), the other wants a wider date window. Today the two payloads are
    indistinguishable, so any copy naming a binding constraint would be
    guessing — and the epic's own edge is explicit that the empty state
    "names the binding constraint", not that it says "no comps".

    THE DATUM ALREADY EXISTS AND IS DISCARDED. ``pipeline/shape.py``'s
    ``shape_raw_pull_with_summary`` returns ``ShapingSummary(kept,
    dropped_outside_window)`` — F4-S4's "counted in a pipeline debug summary"
    — and ``shape_raw_pull`` drops the summary on the floor. Surfacing it (a
    raw-record count and a dropped-by-window count on ``breakdown``) is the
    smallest change that makes this AC honest; the exact spelling is the
    developer's and the PM's, and this test accepts any field pair that
    carries the distinction.

    THE EPIC'S EXAMPLE COPY IS NOT PURCHASABLE — flagged to the PM. "0 in
    radius; nearest match at 1.3mi" needs a listing *outside* the radius we
    paid to search, i.e. a second RentCast call against a 50-call monthly cap.
    Naming the constraints we actually applied, with the counts we already
    have, is the affordable and honest version of the same sentence.
    """
    import os

    fixtures = Path(os.environ[FIXTURES_DIR_ENV])
    narrow = dict(SEARCH_BODY, **NARROW_WINDOW)

    def payload_for(body: dict[str, Any], records: list[dict]) -> dict:
        queries = seeded(body, 0)
        for query in queries:
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(
                json.dumps(records).encode("utf-8")
            )
        search = client.post(SEARCH_PATH, json=body)
        assert search.status_code == 200, search.text[:400]
        assert search.json()["complete"] is True, search.json()
        derived = client.post(DERIVE_PATH, json=derive_body(search.json()["pull_ref"]))
        assert derived.status_code == 200, derived.text[:400]
        return derived.json()

    # (a) the source had nothing for these constraints at all.
    nothing = payload_for(dict(SEARCH_BODY, address="9990 S Nowhere Ave, Chicago, IL 60609"), [])

    # (b) the source had records; the date window dropped every one of them.
    #     Listed 1 Feb — outside a 06-15..06-30 window, inside the padding the
    #     pull actually bought.
    january = date(TODAY.year - 1, 2, 1)
    dropped = payload_for(narrow, [_record(n, january) for n in range(3)])

    assert nothing["breakdown"]["pulled"] == 0, nothing["breakdown"]
    assert dropped["breakdown"]["pulled"] == 0, (
        "the window-dropped scenario kept comps, so this test is not exercising the "
        "state it claims — fix the fixture, not the assertion"
    )

    def constraint_facts(state: dict) -> dict:
        """Whatever this payload says about *why* it is empty."""
        breakdown = state["breakdown"]
        meta = state["meta"]
        candidates = {
            key: value
            for key, value in breakdown.items()
            if key in {"raw_records", "dropped_outside_window", "empty_reason"}
        }
        if "empty_reason" in meta:
            candidates["meta.empty_reason"] = meta["empty_reason"]
        codes = sorted(
            w["code"] for w in state["warnings"] if w["code"] in {"no_comps", "empty_result"}
        )
        messages = sorted(
            w["message"]
            for w in state["warnings"]
            if w["code"] in {"no_comps", "empty_result"}
        )
        if codes:
            candidates["warnings"] = messages
        return candidates

    left, right = constraint_facts(nothing), constraint_facts(dropped)
    assert left and right, (
        "nothing in DerivedState says why the comp set is empty. F4-S6's empty state "
        "must 'name the binding constraint' and D5 forbids the view inventing it. "
        "Needed: the raw-record count and the dropped-by-window count that "
        "pipeline/shape.py::ShapingSummary already computes (F4-S4) and "
        "shape_raw_pull currently discards — or an equivalent field naming the "
        "reason. Observed breakdown keys: " + ", ".join(sorted(nothing["breakdown"]))
    )
    assert left != right, (
        "a pull that returned nothing and a pull whose records were all dropped by the "
        f"date window produce the same evidence-empty payload ({left!r}). They ask the "
        "user for opposite actions — widen the radius vs widen the window — so an "
        "empty state built on this can only guess."
    )


def test_a_resume_finishes_a_partial_pull_without_re_buying_what_is_on_disk(
    client, seeded, rentcomp_home
):
    """[AC6] RED. The resume action needs a request that costs the remainder.

    §5a: "a pull interrupted at 3/4 costs exactly 1 call to finish". The
    orchestrator honours that already (``run_pull`` diffs the manifest and
    sends only what is owed) — but no HTTP request reaches it in that mode.
    ``POST /api/search`` fetches only on a cache MISS or on ``force_refresh``,
    and ``force_refresh`` explicitly discards the diff (``known = {} if
    force_refresh``). So the one lever F4-S6's resume button could pull today
    re-buys every window: a 1-call gap billed as a full re-pull.

    QA-PROPOSED CONTRACT (the PM confirms before it is locked; precedent:
    F4-S9's QA proposed the search contract, F2-S1's the plan route) —

        POST /api/search {..., "resume": true}
          → sends only the windows this pull does not already hold
          → 200 with complete=true, missing=[], calls_to_complete=0
          → never rewrites a response already on disk

    ``resume`` and ``force_refresh`` are different consents and must stay
    different fields: one says "finish what I paid for", the other says "go
    buy it again".

    The assertion is on the stored bytes rather than on ``calls_spent``
    because ``calls_spent`` is 0 in fixture mode by design — a cost assertion
    that cannot fail in the only mode the suite runs in would be theatre.
    """
    queries = seeded(SEARCH_BODY, 0)
    assert len(queries) >= 2
    seeded(SEARCH_BODY, len(queries) - 1)

    first = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert first.status_code == 200, first.text[:400]
    partial = first.json()
    assert partial["complete"] is False and partial["calls_to_complete"] >= 1, partial
    ref = partial["pull_ref"]

    before = raw_files(rentcomp_home, ref)
    assert before, "the partial pull stored no responses at all"

    # The missing window becomes available — the state a retry is for.
    seeded(SEARCH_BODY)

    resumed = client.post(SEARCH_PATH, json={**SEARCH_BODY, "resume": True})
    assert resumed.status_code == 200, (
        f"POST {SEARCH_PATH} refused a resume request ({resumed.status_code}): "
        f"{resumed.text[:300]}. F4-S6's partial-pull state requires a resume action, "
        "and D5 forbids the view improvising one out of force_refresh."
    )
    outcome = resumed.json()
    assert outcome["complete"] is True and outcome["missing"] == [], (
        "a resume left the pull incomplete: " + json.dumps(outcome)
    )

    after = raw_files(rentcomp_home, ref)
    for name, content in before.items():
        assert after.get(name) == content, (
            f"the resume rewrote {name}, a response that was already on disk. §5a: a "
            "resume costs exactly the remainder — on the live path this is money."
        )
    assert set(after) > set(before), "the resume fetched nothing new"


def test_force_refresh_is_not_a_resume(client, seeded, rentcomp_home):
    """[AC6] Guard rail. ``force_refresh`` must not be wired to the resume
    button as a shortcut.

    It re-fetches every window by design (F3-S2's REFRESH click) and this test
    pins that difference, so "the resume works" can never be satisfied by
    pointing it at the one flag that already exists. Expected to PASS today
    and to keep passing: it describes current, correct behaviour whose only
    danger is being mistaken for something else.
    """
    queries = seeded(SEARCH_BODY, 0)
    assert len(queries) >= 2
    seeded(SEARCH_BODY, len(queries) - 1)

    first = client.post(SEARCH_PATH, json=SEARCH_BODY)
    assert first.status_code == 200, first.text[:400]
    ref = first.json()["pull_ref"]
    before = raw_files(rentcomp_home, ref)
    assert before

    # Different bytes for the same windows, so a re-fetch is visible.
    seeded(SEARCH_BODY, records_per_window=3)

    refreshed = client.post(SEARCH_PATH, json={**SEARCH_BODY, "force_refresh": True})
    assert refreshed.status_code == 200, refreshed.text[:400]
    after = raw_files(rentcomp_home, ref)

    rewritten = [name for name, content in before.items() if after.get(name) != content]
    assert rewritten, (
        "force_refresh left already-held responses untouched. If it has become a "
        "resume, then F3-S2's REFRESH no longer re-buys evidence and 'never mix fresh "
        "and stale' has lost its trigger — that is a semantic change, not a fix."
    )
