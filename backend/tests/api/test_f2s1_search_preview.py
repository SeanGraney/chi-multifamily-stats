"""F2-S3 (call estimator) + F2-S1's submit path — Layer 2 contract tests.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Nothing here touches a pipeline internal: every assertion is made
over HTTP against the app, so these keep working when the implementation
behind the route changes.

WHY A SEPARATE PREVIEW ROUTE EXISTS AT ALL (the ambiguity this file resolves,
QA-PROPOSED — needs PM confirmation, see the handoff)
--------------------------------------------------------------------------
F2 step 3 requires the cohort plan and "est. N API calls" to be shown **live,
before submit**. Two constraints make that impossible over `POST /api/search`:

* that route *pulls* on a cache miss (`api/search.py` module docstring), so
  driving it from a debounced form would spend money on every keystroke; and
* D5/D13 forbid the frontend computing the number itself — the estimate is a
  backend fact the SPA renders.

So a second, structurally non-spending route is needed. This file pins it as
``POST /api/search/plan`` with the same `SearchRequest` body, overridable via
``RENTCOMP_TEST_PLAN_PATH`` so a PM ruling on the path is a one-env-var change
rather than a test edit.

WHAT THE F2-S3 [INVARIANT] ACTUALLY REQUIRES, AND WHERE IT IS PINNED
---------------------------------------------------------------------
"One function, two consumers, no drift between displayed and actual counts."
The function already exists and is already Layer-1 exhaustive on main:
``client/planner.py::fetchable_queries`` — `test_query_planner_dev.py`
covers the definition ("the number F2-S3 shows the user is
`len(fetchable_queries(...))`", the all-empty plan, and the drop-exactly-the-
empty-ones filter) against a FROZEN today, which is the only way the
structurally-empty case is calendar-independent. Nothing here duplicates that.

What is NOT yet pinned anywhere, and is this file's job, is the *consumer*
half:

    preview.estimated_calls == search.estimated_calls == len(fetchable_queries(plan))

The middle equality is the invariant (two consumers, one number); the outer
equality is what stops either consumer quietly counting `len(plan)` instead —
F4-S1 AC4 and the PM's 4th ruling on this story, and the difference is real
money out of a 50-call month.

ZERO LIVE CALLS (D17, WORKFLOW.md §6): `RENTCOMP_LIVE` is never set here, the
suite-wide `no_network` switch in `backend/tests/conftest.py` is autouse, and
the preview route must not fetch even in fixture mode.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from typing import Any

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger

#: The live-preview route (QA-proposed, see the module docstring).
PLAN_PATH = os.environ.get("RENTCOMP_TEST_PLAN_PATH", "/api/search/plan")

#: The submit route F4-S9 already built (ARCHITECTURE.md §4).
SEARCH_PATH = "/api/search"

LIVE_ENV = "RENTCOMP_LIVE"
KEY_ENV = "RENTCAST_API_KEY"
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

#: A full-calendar-year window, fetchable on every cohort year whatever today
#: happens to be — so this suite is date-independent (the F4-S9 suite's own
#: device, kept deliberately identical).
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


def plan_args(body: dict[str, Any]) -> dict[str, Any]:
    """The wire body in the planner's own argument names."""
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


def fetchable_count(body: dict[str, Any]) -> set[int]:
    """`len(fetchable_queries(plan))` for `body`, as a set over today/tomorrow.

    The route supplies `date.today()` as the pull's `today`. A test that read
    the clock once could disagree with the server across a midnight boundary,
    so both readings are accepted — the assertion is still exact for every
    instant that is not the tick itself.
    """
    today = date.today()
    return {
        len(fetchable_queries(plan_pull_queries(when, **plan_args(body))))
        for when in (today, today + timedelta(days=1))
    }


def body(**overrides: Any) -> dict[str, Any]:
    payload = dict(SEARCH_BODY)
    payload.update(overrides)
    return payload


def route_present(app: Any, path: str, method: str = "POST") -> bool:
    """True when `app` exposes `method path`.

    Asked of the OpenAPI document for the reason `tests/api/conftest.py` gives:
    FastAPI keeps an included router as one nested entry in `app.routes`, so a
    naive scan reports "missing" for every router-registered endpoint. Repeated
    locally rather than imported so this module never depends on `conftest`
    being importable by name.
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
def fixture_mode(rentcomp_home, tmp_path, monkeypatch, clear_caches):
    """Fixture mode pointed at an empty temp dataset, ledger zeroed.

    Empty on purpose for most of this file: the preview must never fetch, so
    it must not care whether a saved response exists. `seed()` fills it in for
    the one test that needs a *complete* pull to compare against.
    """
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in (LIVE_ENV, KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    def seed(request_body: dict[str, Any] | None = None) -> int:
        queries = fetchable_queries(
            plan_pull_queries(date.today(), **plan_args(request_body or SEARCH_BODY))
        )
        for index, query in enumerate(queries):
            record = {
                "id": f"f2s1-preview-{index}",
                "formattedAddress": f"{400 + index} W Preview St, Chicago, IL 60609",
                "addressLine1": f"{400 + index} W Preview St",
                "latitude": 41.82,
                "longitude": -87.67,
                "price": 2000 + index,
                "bedrooms": 3,
                "bathrooms": 1,
                "squareFootage": 900 + index,
                "listedDate": (
                    (date.today() - timedelta(days=200 + index)).isoformat() + "T00:00:00.000Z"
                ),
                "status": "Active",
            }
            (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(
                json.dumps([record]).encode("utf-8")
            )
        clear_caches()
        return len(queries)

    return seed


@pytest.fixture
def client(app, fixture_mode):
    """A `TestClient`, or a legible skip while the preview route is absent.

    Layer 2 (D21): no port, no browser, no network. One named failure
    (`test_the_search_plan_preview_route_exists`) and N skips is the red shape
    the PM asked for.
    """
    if not route_present(app, PLAN_PATH):
        pytest.skip(f"POST {PLAN_PATH} not implemented yet (F2-S3 red)")
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def seed(fixture_mode):
    return fixture_mode


# ===========================================================================
# existence — the one loud failure everything else skips behind
# ===========================================================================


def test_the_search_plan_preview_route_exists(app) -> None:
    """F2 step 3: the cohort plan and call estimate are shown *before* submit.

    `POST /api/search` cannot serve that line — it pulls on a cache miss, so a
    live-updating form driven by it would spend the month's calls while the
    user was still typing. And D5/D13 forbid the SPA computing the number
    itself. Hence a separate, structurally non-spending route.
    """
    assert route_present(app, PLAN_PATH), (
        f"POST {PLAN_PATH} is not in the app's OpenAPI document. F2 step 3 requires a live "
        "'Jun 15-30 · 2026, 2025 (2 cohorts) · est. N API calls' preview BEFORE submit, and "
        "neither of the two obvious alternatives is available: POST /api/search fetches on a "
        "cache miss (money), and computing the count in TypeScript violates D5/D13. If the PM "
        "rules a different path, set RENTCOMP_TEST_PLAN_PATH."
    )


# ===========================================================================
# F2-S3 [INVARIANT] — one number, two consumers, no drift
# ===========================================================================


def test_preview_reports_the_fetchable_call_count_not_the_planned_one(client) -> None:
    """The number shown to the user is `len(fetchable_queries(plan))`.

    F4-S1 AC4 / PM ruling 4: a structurally-empty window is planned so the user
    can see it was considered, and never sent, so it must never be billed in an
    estimate either. Recomputed here from the planner rather than hardcoded, so
    this stays exact if the search body ever changes.
    """
    response = client.post(PLAN_PATH, json=body())
    assert response.status_code == 200, (
        f"POST {PLAN_PATH} returned {response.status_code}: {response.text[:600]}"
    )
    payload = response.json()
    assert "estimated_calls" in payload, (
        f"the preview must carry `estimated_calls` — it is the whole point of the route "
        f"(F2 step 3). Got: {sorted(payload)}"
    )
    assert payload["estimated_calls"] in fetchable_count(body()), (
        f"estimated_calls={payload['estimated_calls']} but "
        f"len(fetchable_queries(plan)) is {sorted(fetchable_count(body()))}. F2-S3 is "
        "[INVARIANT] on this being the SAME number the pull spends — see "
        "client/planner.py::fetchable_queries, which exists precisely so the estimate and "
        "the pull cannot disagree."
    )


def test_preview_and_search_report_the_same_call_count(client, seed) -> None:
    """The F2-S3 [INVARIANT] itself: two consumers, one number.

    The preview line (this route) and the cache modal's "REFRESH (~N API
    calls)" (`POST /api/search`'s `estimated_calls`, F3-S2) must never disagree
    — a user who consents to 4 calls and is charged 6 has been lied to by the
    UI, and no amount of correct arithmetic inside either consumer fixes that.
    """
    seed()
    preview = client.post(PLAN_PATH, json=body())
    assert preview.status_code == 200, preview.text[:600]
    search = client.post(SEARCH_PATH, json=body())
    assert search.status_code == 200, search.text[:600]

    assert preview.json()["estimated_calls"] == search.json()["estimated_calls"], (
        "the preview and the submit route report different call counts "
        f"({preview.json()['estimated_calls']} vs {search.json()['estimated_calls']}). "
        "F2-S3 is [INVARIANT]: ONE function, two consumers, no drift between the displayed "
        "and the actual count."
    )


def test_preview_lists_one_cohort_per_year_back_newest_first(client) -> None:
    """"...· 2026, 2025 (2 cohorts) · ..." — the years come from the backend.

    D5/D13: the SPA renders what the API returns and never derives a statistic;
    the cohort set is a fact about the plan, not a string the frontend can
    safely reconstruct by subtracting from `new Date()`. A structurally-empty
    cohort is *listed* (planner docstring: "silently absent" and "planned but
    empty" are different things to a user checking their date window) but must
    carry a flag distinguishing it, since it costs nothing.
    """
    payload = client.post(PLAN_PATH, json=body(years_back=3)).json()
    cohorts = payload.get("cohorts")
    assert isinstance(cohorts, list) and len(cohorts) == 3, (
        f"expected one cohort entry per years_back (3), got {cohorts!r}. The preview line "
        "names the cohort YEARS, so the backend has to supply them (D5/D13)."
    )
    years = [entry.get("year") for entry in cohorts]
    assert years == sorted(years, reverse=True), (
        f"cohort years must be newest-first, matching the spec's '2026, 2025' preview "
        f"wording — got {years}"
    )
    assert years[0] in {date.today().year, (date.today() + timedelta(days=1)).year}, (
        f"the newest cohort must be the current year (the planner's `today.year - 0`) — got "
        f"{years[0]}"
    )
    assert all("fetchable" in entry for entry in cohorts), (
        "each cohort must say whether it is fetchable: a structurally-empty window is planned "
        "and never sent (F4-S1 AC4), and the user checking their date window needs to see the "
        f"difference between 'absent' and 'planned but empty'. Got {cohorts!r}"
    )


# ===========================================================================
# the preview must be structurally incapable of spending money
# ===========================================================================


def test_preview_creates_no_cache_entry_and_moves_no_ledger(client) -> None:
    """A live preview fires on every field edit. It must never buy anything.

    Asserted three ways because "it didn't spend" has three separate proofs:
    the ledger did not move, no raw response reached disk, and the pull is
    still a cache MISS afterwards. `POST /api/search` for the same params is
    then run as the contrast — if that also leaves no entry, the assertions
    above prove nothing.
    """
    from rentcomp.client.pull import pull_ref_for, pull_status
    from rentcomp.storage.cache import CacheMissError

    before = load_ledger().calls_this_month
    response = client.post(PLAN_PATH, json=body())
    assert response.status_code == 200, response.text[:600]

    assert load_ledger().calls_this_month == before, (
        "the preview moved the call ledger. It must not fetch at all — F2 step 3 happens "
        "before the user has consented to any spend."
    )
    ref = pull_ref_for(**plan_args(body()))
    with pytest.raises(CacheMissError):
        pull_status(ref)

    # Contrast: the submit route DOES create the entry, so the absence above is
    # a real property of the preview rather than of the test environment.
    assert client.post(SEARCH_PATH, json=body()).status_code == 200
    pull_status(ref)


def test_preview_ignores_force_refresh_and_still_fetches_nothing(client) -> None:
    """`force_refresh` is the REFRESH click's flag (F3-S2) and belongs to the
    submit route only. Whether the preview accepts the field or rejects it is
    the developer's call; what it may never do is *act* on it — a preview that
    re-pulls because a stale flag rode along on the body would spend the whole
    month's remaining calls from inside a debounced keystroke handler.
    """
    from rentcomp.client.pull import pull_ref_for, pull_status
    from rentcomp.storage.cache import CacheMissError

    before = load_ledger().calls_this_month
    response = client.post(PLAN_PATH, json=body(force_refresh=True))
    assert response.status_code in {200, 422}, (
        f"expected the preview to either ignore or reject `force_refresh`, got "
        f"{response.status_code}: {response.text[:400]}"
    )
    assert load_ledger().calls_this_month == before
    with pytest.raises(CacheMissError):
        pull_status(pull_ref_for(**plan_args(body())))


def test_preview_reports_cache_status_so_the_form_can_route_to_the_cache_modal(
    client, seed
) -> None:
    """F2 step 4: "if a cache exists for identical params -> F3 [cache modal]".

    The form can only make that branch if the preview already told it a cache
    exists — asking `POST /api/search` would be asking the route that pulls.
    """
    first = client.post(PLAN_PATH, json=body()).json()
    assert first.get("cache_status") == "miss", (
        f"a never-run search must preview as a cache MISS, got {first.get('cache_status')!r}"
    )

    seed()
    assert client.post(SEARCH_PATH, json=body()).status_code == 200

    second = client.post(PLAN_PATH, json=body()).json()
    assert second.get("cache_status") == "hit", (
        "after a complete pull the same params must preview as a cache HIT, so the form can "
        f"route to F3's modal instead of silently re-pulling. Got {second.get('cache_status')!r}"
    )
    assert second.get("pull_ref") == first.get("pull_ref"), (
        "the preview's pull_ref must be a function of the search params alone (F3-S1) — it is "
        "what the form hands to the cache modal and, after submit, to /api/derive"
    )


# ===========================================================================
# PM ruling 3 — trim the user's input, do NOT weaken the planner
# ===========================================================================


#: What a real form yields when a user tabs out of a field: leading/trailing
#: whitespace on values the planner and the cache key both take literally.
UNTRIMMED = {
    "address": "  3651 S Wood St, Chicago, IL 60609  ",
    "bedrooms": " 3:4 ",
    "window_start": " 01-01",
    "window_end": "12-31 ",
    "property_types": [" Multi-Family", "Apartment ", " Townhouse "],
}


def test_the_planner_still_refuses_untrimmed_month_days() -> None:
    """The regression pin, asserted in the same file as the fix so the pairing
    is legible: `plan_pull_queries` rejects `"06-01 "` and must KEEP rejecting
    it (F4-S1 QA). PM ruling 3 fixes the *input* side, never the planner.

    Deliberately ungated by the `client` fixture — this one is GREEN today and
    must stay green; it is the half of PM ruling 3 that says what NOT to touch.
    """
    with pytest.raises(ValueError):
        plan_pull_queries(
            date.today(), **{**plan_args(body()), "window_start_mmdd": "01-01 "}
        )


def test_preview_trims_user_input_before_planning(client) -> None:
    """PM ruling 3. A trailing space is not a different search."""
    trimmed = client.post(PLAN_PATH, json=body())
    untrimmed = client.post(PLAN_PATH, json=body(**UNTRIMMED))

    assert untrimmed.status_code == 200, (
        f"POST {PLAN_PATH} rejected whitespace the user cannot see "
        f"({untrimmed.status_code}: {untrimmed.text[:400]}). PM ruling 3: this story trims "
        "user input before it reaches plan_pull_queries."
    )
    assert untrimmed.json()["estimated_calls"] == trimmed.json()["estimated_calls"]
    assert untrimmed.json().get("pull_ref") == trimmed.json().get("pull_ref"), (
        "an untrimmed address must hash to the SAME pull_ref — otherwise the user pays twice "
        "for one search because a space rode along from the form (F3-S1)"
    )


def test_search_submit_trims_user_input_too(client) -> None:
    """The same rule on the money route: preview and submit must resolve one
    search to one ref, or the free preview describes a pull the paid submit
    never performs.
    """
    preview = client.post(PLAN_PATH, json=body(**UNTRIMMED))
    submit = client.post(SEARCH_PATH, json=body(**UNTRIMMED))
    assert submit.status_code == 200, (
        f"POST {SEARCH_PATH} rejected untrimmed input ({submit.status_code}): "
        f"{submit.text[:400]}"
    )
    assert submit.json()["pull_ref"] == preview.json()["pull_ref"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("window_start", "6-1x"),
        ("window_start", "13-01"),
        ("window_end", "02-30"),
        ("window_end", ""),
    ],
)
def test_trimming_is_not_laundering_genuinely_malformed_input_is_refused(
    client, field: str, value: str
) -> None:
    """Trimming must remove whitespace, not repair mistakes.

    A month-day the user actually got wrong has to come back as a 422 the form
    can show inline — silently coercing it would move the window the user
    asked for, which is the same failure mode `_parse_mmdd` refuses `02-30`
    for.
    """
    response = client.post(PLAN_PATH, json=body(**{field: value}))
    assert response.status_code == 422, (
        f"{field}={value!r} must be refused with 422 so the form can show it inline; got "
        f"{response.status_code}: {response.text[:400]}"
    )


@pytest.mark.parametrize("years_back", [0, 6, -1])
def test_years_back_outside_one_to_five_is_refused(client, years_back: int) -> None:
    """§2.1: "Years back | 1-5, default 2". The bound is on the wire, not only
    in the stepper widget — a form is not a validation layer.
    """
    response = client.post(PLAN_PATH, json=body(years_back=years_back))
    assert response.status_code == 422, (
        f"years_back={years_back} must be refused (§2.1 bounds it at 1-5); got "
        f"{response.status_code}"
    )
