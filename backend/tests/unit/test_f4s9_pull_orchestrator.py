"""F4-S9 [BE] Pull orchestrator — AC1, AC2, AC5, AC6 at Layer 1.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). There is no story text for F4-S9; the PM's six acceptance criteria
in the dispatch ARE the story, and each one appears exactly once in the
test-plan table handed back with this file.

WHAT THIS STORY IS
------------------
Nothing in the codebase calls `RentCastClient.fetch_listings`. F4-S1 produces
a plan, F0-S4 executes ONE query, F3-S1 stores raw bytes and resolves a
`pull_ref`, WS-1 shapes records. This story is the missing composition: run a
whole plan, write every response through to the cache, mint a `pull_ref`, hand
it to the existing loader.

Because it is composition, almost every assertion here is about a SEAM between
two things that already work, or about what survives when a pull fails partway.

WHY LAYER 1 (AGENT_QA.md decision procedure, run per-AC)
--------------------------------------------------------
Step 1 matches for AC1, AC2, AC5 and AC6: "can I import a function and call it
directly with plain values?" — yes, `run_pull(...)` with an injected
`httpx.MockTransport`. Nothing here needs an assembled request body, and none
of it is observable from a browser: "how many wire requests were issued",
"which bytes survived a mid-pull failure" and "was the ledger incremented" are
invisible above this layer. AC3 (a minted ref derives) and one representative
of AC5/AC6 live at Layer 2 in `tests/api/test_f4s9_search_route.py`; AC4's
structural guards live in `tests/unit/test_f4s9_live_guard.py`.

MONEY — HOW THIS FILE EXERCISES THE LIVE PATH WITHOUT SPENDING A CALL
----------------------------------------------------------------------
42 of 50 monthly calls remain and they are the owner's. Every test below that
sets `RENTCOMP_LIVE=1` also injects an `httpx.MockTransport`, which answers
inside the process and never opens a socket — the pattern F0-S4's QA ruled
mandatory and `scripts/tests/test_gate.py` established. `tests/conftest.py`'s
autouse `no_network` fixture is the backstop, and every live-path test here
asserts it recorded ZERO attempts, so a `run_pull` that ignored `transport=`
fails loudly on the *first* request instead of quietly dialling out.

THE CONTRACT PINNED HERE IS QA-PROPOSED (the PM confirms before it is locked)
-----------------------------------------------------------------------------
No module or function for this story exists anywhere in the repo or the docs,
so the names below are proposed, not inherited:

    rentcomp.client.pull.run_pull(...)          -> PullOutcome
    rentcomp.client.pull.pull_status(pull_ref)  -> PullOutcome   (no network)
    rentcomp.client.pull.PullOutcome            (frozen dataclass / model)
    rentcomp.client.pull.InsufficientCallBudgetError

`client/` rather than `storage/` because this is the code that decides to
spend money, and `client/` is the one package D17's AST guard already lets
near a network library (`tests/unit/test_live_call_guard.py`); `storage/`
importing `client/` would also invert the existing dependency direction
(`client/rentcast.py` already imports `storage.ledger` / `storage.secrets`).

`run_pull` takes `today` as an argument rather than reading the clock — the
F4-S1 planner precedent ("Time is data here, not ambient state"), and the only
way these tests can reach the year-boundary window the AC1 case needs. The API
edge supplies `date.today()`.

WHAT IS DELIBERATELY *NOT* PINNED
---------------------------------
* The per-call file/signature naming (`y2025-inactive-off000` is F3-S4's own
  [DEFAULT]). What is pinned is the observable consequence: N fetched
  responses ⇒ N distinct files, so no two calls can overwrite each other.
* Whether completing a partial pull mints a NEW ref or reuses the old one.
  ADR-001's erratum leaves both open; `test_completing_a_partial_pull_is_
  visible_to_the_loader` pins the OUTCOME (the completed evidence is what
  derives) and lets the developer choose the mechanism.
* Whether the orchestrator stops at the first failed query or continues. Both
  failure tests fail the LAST TWO queries, so the expected `missing` and the
  expected resume cost are identical either way.

EXPECTED RED STATE TODAY: `test_the_pull_orchestrator_module_exists` FAILS
naming exactly what is missing; every other test in the file SKIPS until
`rentcomp.client.pull` is importable, then runs for real.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.storage.cache import raw_response_paths
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import (
    MONTHLY_CALL_CAP,
    Ledger,
    current_month,
    load_ledger,
    save_ledger,
)
from rentcomp.storage.pulls import load_shaped_pull

try:
    from rentcomp.client.pull import (
        InsufficientCallBudgetError,
        PullOutcome,
        pull_status,
        run_pull,
    )

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F4-S9 lands
    _IMPORT_ERROR = _exc

needs_orchestrator = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.client.pull is not importable yet (F4-S9 red): {_IMPORT_ERROR}",
)


def test_the_pull_orchestrator_module_exists() -> None:
    """Legible red: ONE failure that names exactly what is missing.

    Everything else in this file skips behind it, so the red state stays
    readable to the PM rather than being 30 identical ImportErrors.
    """
    assert _IMPORT_ERROR is None, (
        "Cannot import the pull orchestrator (QA-proposed path "
        "rentcomp.client.pull, exporting run_pull / pull_status / PullOutcome / "
        f"InsufficientCallBudgetError — see the F4-S9 handoff): {_IMPORT_ERROR}"
    )


# ---------------------------------------------------------------------------
# the search this file pulls
# ---------------------------------------------------------------------------

#: A full, ordinary 3-year plan: 6 queries, none structurally empty (verified
#: against the real planner). `today` is frozen so the windows — and therefore
#: the fixture signatures and the cache key — are identical on every run.
TODAY = date(2026, 7, 27)

SEARCH = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}

#: The AC1 case. `today` + a New-Year-spanning window puts cohort 2027's
#: window wholly in the future: the planner emits `daysOld=1:-107`, which
#: cannot match a record whatever the market holds (F4-S1 AC4). 4 planned,
#: 2 fetchable — verified against the real planner, not assumed.
EMPTY_WINDOW_TODAY = date(2027, 6, 1)
EMPTY_WINDOW_SEARCH = {
    **SEARCH,
    "years_back": 2,
    "window_start_mmdd": "12-15",
    "window_end_mmdd": "01-15",
}
#: The exact wire value that must never be sent. One call out of 50 spent to
#: guarantee a zero result is one call of the owner's money burnt.
STRUCTURALLY_EMPTY_DAYS_OLD = "1:-107"


def plan_for(search: dict, today: date):
    """The real F4-S1 plan for a search — the same function the orchestrator
    must use, so these tests can never disagree with it about what a pull is."""
    return plan_pull_queries(today, **search)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

FAKE_KEY = "qa-f4s9-fake-key-not-a-real-credential"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An isolated storage root. Memos cleared either side, because these
    tests rewrite the store in place (ADR-001 §3)."""
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    load_shaped_pull.cache_clear()
    yield root
    load_shaped_pull.cache_clear()


@pytest.fixture
def live_env(home, monkeypatch) -> Path:
    """The live path, with no way out to the network.

    `RENTCOMP_LIVE=1` + a fake key is the ONLY combination that reaches the
    code AC1/AC2/AC5 are about — a fixture-mode pull never counts a call, so
    "exactly N queries" and "the ledger was not incremented" are not even
    expressible there. Every test using this fixture also injects a
    `MockTransport` and asserts `no_network` saw nothing.
    """
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    return home


class Wire:
    """A recording `httpx.MockTransport`. Answers a script; never opens a socket.

    Requests are recorded before the script runs, so a test can assert on what
    was *sent* even when the answer is an error.
    """

    def __init__(self, answer) -> None:
        self._answer = answer
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request, len(self.requests))

    @property
    def sent(self) -> list[tuple[str | None, str | None, str | None]]:
        """`(status, daysOld, offset)` per request — the identity of a query."""
        return [
            (
                r.url.params.get("status"),
                r.url.params.get("daysOld"),
                r.url.params.get("offset"),
            )
            for r in self.requests
        ]

    @property
    def days_old_sent(self) -> list[str | None]:
        return [r.url.params.get("daysOld") for r in self.requests]


def listing(n: int, *, listed: date, removed: date | None = None) -> dict:
    """One synthetic RentCast record.

    Deliberately in the shape `pipeline/shape.py` reads (verified against the
    committed `fixtures/live-samples/`), with a DISTINCT address per `n` so
    dedupe/grouping cannot silently collapse two queries' evidence into one
    comp and make a lost response invisible.
    """
    record = {
        "id": f"f4s9-listing-{n}",
        "formattedAddress": f"{100 + n} W Orchestrator Ave, Chicago, IL 60609",
        "addressLine1": f"{100 + n} W Orchestrator Ave",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 1900 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "status": "Active" if removed is None else "Inactive",
    }
    if removed is not None:
        record["removedDate"] = f"{removed.isoformat()}T00:00:00.000Z"
    return record


def ok(records: list[dict], *, total: int | None = None) -> httpx.Response:
    """A 200 carrying `records`, with `X-Total-Count` when pagination is under
    test (the client only paginates when the header says there is more)."""
    headers = {} if total is None else {"X-Total-Count": str(total)}
    return httpx.Response(200, json=records, headers=headers)


def one_record_per_query(request: httpx.Request, n: int) -> httpx.Response:
    """Every query answers with a single, unique, in-window record."""
    return ok([listing(n, listed=TODAY - timedelta(days=120 + n))])


def set_ledger(*, spent: int) -> None:
    """Put the month's ledger at a known count.

    Written rather than nudged: `load_ledger()` on a fresh home lazily seeds
    itself from the gate's 8 committed calls (F3-S1), so "remaining" is 42 by
    default and every budget assertion has to start from a number it chose.
    """
    save_ledger(Ledger(month=current_month(), calls_this_month=spent, history=()))


def assert_no_socket(no_network) -> None:
    """The money assertion, repeated everywhere the live path is exercised."""
    assert no_network == [], (
        "a live-path test reached for a real socket instead of the injected "
        f"MockTransport: {no_network}. `run_pull` must thread `transport=` "
        "through to RentCastClient — WORKFLOW.md §6, zero live API calls."
    )


# ===========================================================================
# AC1 — exactly `len(fetchable_queries(plan))` queries, never `len(plan)`
# ===========================================================================


@needs_orchestrator
def test_a_structurally_empty_window_is_planned_but_never_sent(live_env, no_network) -> None:
    """AC1, the money assertion.

    The plan holds 4 queries; 2 of them ask for a window that has not opened
    yet, so they cannot match a record whatever the market holds (F4-S1 AC4).
    Sending one spends 1 of 50 monthly calls to guarantee an empty answer.

    Asserted two ways because a bare count would pass if the orchestrator sent
    the WRONG two: the number of requests, and the absence of the specific
    wire value that identifies the empty window.
    """
    set_ledger(spent=0)
    plan = plan_for(EMPTY_WINDOW_SEARCH, EMPTY_WINDOW_TODAY)
    assert len(plan) == 4 and len(fetchable_queries(plan)) == 2, (
        "the fixture search no longer produces the planned-vs-fetchable split this "
        "test is about — fix the fixture, not the assertion"
    )

    wire = Wire(one_record_per_query)
    run_pull(**EMPTY_WINDOW_SEARCH, today=EMPTY_WINDOW_TODAY, transport=wire.transport)

    assert len(wire.requests) == len(fetchable_queries(plan)), (
        f"sent {len(wire.requests)} requests for a plan of {len(plan)} whose fetchable "
        f"subset is {len(fetchable_queries(plan))}. A structurally-empty window must be "
        "planned and never billed (F4-S1 AC4); at 42 calls remaining, each one is real money."
    )
    assert STRUCTURALLY_EMPTY_DAYS_OLD not in wire.days_old_sent, (
        f"the structurally-empty window went on the wire as daysOld="
        f"{STRUCTURALLY_EMPTY_DAYS_OLD!r}. Nothing can have been listed in the future."
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_the_ledger_counts_exactly_the_fetchable_queries(live_env, no_network) -> None:
    """AC1's other half: the *spend* matches the fetchable count, not the plan.

    The ledger is the record that governs the cap, so a pull that sent 2 and
    recorded 4 (or sent 4 and recorded 2) is a budget bug even if the request
    count looks right.
    """
    set_ledger(spent=0)
    plan = plan_for(EMPTY_WINDOW_SEARCH, EMPTY_WINDOW_TODAY)
    wire = Wire(one_record_per_query)

    outcome = run_pull(
        **EMPTY_WINDOW_SEARCH, today=EMPTY_WINDOW_TODAY, transport=wire.transport
    )

    expected = len(fetchable_queries(plan))
    assert load_ledger().calls_this_month == expected
    assert outcome.calls_spent == expected, (
        f"PullOutcome.calls_spent={outcome.calls_spent}, ledger says {expected}. The "
        "number a caller is shown and the number the cap is enforced on must be one number."
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_a_full_plan_with_no_empty_windows_sends_every_query(live_env, no_network) -> None:
    """The complement, so the AC1 tests above cannot be satisfied by an
    orchestrator that simply under-fetches: when nothing is structurally
    empty, fetchable == planned and all six go."""
    set_ledger(spent=0)
    plan = plan_for(SEARCH, TODAY)
    assert len(plan) == len(fetchable_queries(plan)) == 6

    wire = Wire(one_record_per_query)
    run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert len(wire.requests) == 6, (
        f"sent {len(wire.requests)} of 6 fetchable queries — a silently under-fetched pull "
        "is a missing cohort, which skews every downstream number (D24 §5a)"
    )
    assert len({q[:2] for q in wire.sent}) == 6, (
        f"the six queries were not six distinct (status, daysOld) pairs: {wire.sent}"
    )
    assert_no_socket(no_network)


# ===========================================================================
# AC2 [INVARIANT] — write-through before parsing; a failure never loses a
# response already paid for; a resume costs only the remainder
# ===========================================================================


@needs_orchestrator
def test_a_failure_partway_never_loses_the_responses_already_paid_for(
    live_env, no_network
) -> None:
    """AC2 [INVARIANT], the headline case (D24 / ARCHITECTURE §5a).

    Queries 5 and 6 fail. The four responses already bought must be on disk,
    untouched — "naive atomic batch would roll back 3 good responses because
    the 4th 429'd" is the exact mistake D24 exists to forbid.

    Both trailing queries fail so the expectation is identical whether the
    orchestrator stops at the first failure or carries on.
    """
    set_ledger(spent=0)

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(500, json={"error": "upstream is having a day"})

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    key = outcome.pull_ref
    stored = raw_response_paths(key)
    assert len(stored) == 4, (
        f"{len(stored)} raw responses on disk after 4 successful calls of 6 "
        f"(files: {[p.name for p in stored]}). Every 2xx is written through per call, "
        "immediately, and is never rolled back by a later failure (D24)."
    )
    assert outcome.complete is False, "a 4-of-6 pull must not report itself complete"
    assert_no_socket(no_network)


@needs_orchestrator
def test_a_response_is_written_through_before_anything_parses_it(
    live_env, no_network
) -> None:
    """AC2 [INVARIANT], the sharpest form: bytes first, parse second.

    ARCHITECTURE §5a: "Persist the response body to disk, THEN run Pydantic. A
    validation bug or a schema surprise must never cost an API call — the
    bytes are already safe and the parse can be re-run for free forever."

    Query 3 answers 200 with a body that is valid JSON but not the shape the
    client accepts, so parsing raises AFTER the response arrived. If the
    orchestrator persists from the returned result object instead of through
    F0-S4's `sink` seam, those paid-for bytes are gone and this fails.
    """
    set_ledger(spent=0)
    poison = b'{"listings": "this is not a list"}'

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n == 3:
            return httpx.Response(200, content=poison)
        return one_record_per_query(request, n)

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    bodies = {p.read_bytes() for p in raw_response_paths(outcome.pull_ref)}
    assert poison in bodies, (
        "the response that failed to parse was not kept. It was a 200, it was billed, and "
        "D24 requires the raw bytes on disk BEFORE anything validates them — otherwise a "
        "parsing bug costs a call out of 50. Write through F0-S4's `sink` seam, which "
        "fires per call before parsing, not from the returned ListingsResult."
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_stored_bytes_are_byte_identical_to_what_arrived(live_env, no_network) -> None:
    """AC2: "raw" means raw.

    Re-serializing through `json.dumps` would re-space and re-order nothing
    visible today and something invisible later — and `cache/` is the
    immutable evidence every number traces back to (NORTH_STAR).
    """
    set_ledger(spent=0)
    body = b'[{"id":"byte-exact","formattedAddress":"9 Raw St","price":2000,' \
           b'"listedDate":"2026-03-01T00:00:00.000Z","bedrooms":3,"squareFootage":900}]'

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(200, content=body if n == 1 else b"[]")

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert body in {p.read_bytes() for p in raw_response_paths(outcome.pull_ref)}, (
        "the stored response is not byte-identical to the body that arrived"
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_every_call_gets_its_own_file_so_none_can_overwrite_another(
    live_env, no_network
) -> None:
    """AC2: N calls paid for ⇒ N responses kept.

    Per-call granularity is what makes a partial set meaningful and a resume
    cost exactly the remainder (§5a). A signature scheme that collided — two
    pages of one query, or two queries of one year — would silently destroy
    evidence that was paid for, and every count downstream would still look
    plausible.

    Query 1 paginates (2 pages via `X-Total-Count`); the other five are single
    pages. 7 calls, therefore 7 files.
    """
    set_ledger(spent=0)
    page_0 = [listing(n, listed=TODAY - timedelta(days=200 + n)) for n in (1, 2)]
    page_1 = [listing(3, listed=TODAY - timedelta(days=203))]

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return ok(page_0, total=3)
        if n == 2:
            return ok(page_1, total=3)
        return one_record_per_query(request, n + 10)

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert len(wire.requests) == 7, (
        f"expected 6 queries with one of them paginating into 2 calls; sent {wire.sent}"
    )
    stored = raw_response_paths(outcome.pull_ref)
    assert len(stored) == 7, (
        f"7 calls were paid for but {len(stored)} responses are on disk "
        f"({[p.name for p in stored]}) — two calls collided on one filename and one "
        "paid-for response was destroyed"
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_resuming_an_interrupted_pull_costs_only_the_remainder(
    live_env, no_network
) -> None:
    """AC2 [INVARIANT], the clause the API budget actually depends on.

    §5a: "A pull interrupted at 3/4 costs exactly 1 call to finish, whenever
    it is retried." Interrupted at 4 of 6 here, so the retry must send exactly
    2 — not 6, and not 0.
    """
    set_ledger(spent=0)

    def first_attempt(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    wire_1 = Wire(first_attempt)
    run_pull(**SEARCH, today=TODAY, transport=wire_1.transport)
    spent_after_first = load_ledger().calls_this_month

    wire_2 = Wire(lambda request, n: one_record_per_query(request, 100 + n))
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire_2.transport)

    assert len(wire_2.requests) == 2, (
        f"the resume sent {len(wire_2.requests)} requests ({wire_2.sent}); 4 of 6 windows "
        "were already on disk, so exactly 2 were owed. Re-fetching a response already "
        "paid for is paying twice (D24)."
    )
    assert load_ledger().calls_this_month == spent_after_first + 2
    assert outcome.complete is True, "after the remainder is fetched the pull is whole"
    assert_no_socket(no_network)


@needs_orchestrator
def test_rerunning_a_complete_pull_issues_zero_calls(live_env, no_network) -> None:
    """AC2's floor case: the remainder of a complete pull is nothing.

    A pull that re-fetched what it already holds would burn 6 of 50 calls
    every time a user re-opened the same search.
    """
    set_ledger(spent=0)
    wire_1 = Wire(one_record_per_query)
    run_pull(**SEARCH, today=TODAY, transport=wire_1.transport)
    spent = load_ledger().calls_this_month

    def must_not_be_called(request: httpx.Request, n: int) -> httpx.Response:
        raise AssertionError(
            "an already-satisfied query was re-sent — a complete pull re-run must cost "
            "zero calls (F3-S4 AC / D24)"
        )

    wire_2 = Wire(must_not_be_called)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire_2.transport)

    assert wire_2.requests == []
    assert outcome.calls_spent == 0
    assert load_ledger().calls_this_month == spent
    assert outcome.complete is True
    assert_no_socket(no_network)


# ===========================================================================
# AC3 (Layer-1 half) — the ref the loader already understands
# ===========================================================================


@needs_orchestrator
def test_the_minted_ref_resolves_through_the_existing_storage_layer(
    live_env, no_network
) -> None:
    """AC3, at the seam rather than over HTTP.

    `tests/api/test_f4s9_search_route.py` owns the end-to-end
    "`/api/derive` returns a real DerivedState" assertion; this is the same
    claim asserted where the debugging happens, and it is what makes the
    Layer-2 failure diagnosable when it comes.

    Nothing about *how* the loader recognises the ref is pinned (F3-S1's QA
    deliberately left that free) — only that the existing `load_shaped_pull`
    resolves it, with `as_of` coming from the manifest the pull wrote.
    """
    set_ledger(spent=0)
    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    pull = load_shaped_pull(outcome.pull_ref, Config())
    assert pull.ref == outcome.pull_ref
    assert len(pull.comps) == 6, (
        f"6 queries each returned 1 distinct in-window record; the loader sees "
        f"{len(pull.comps)} comps. Evidence was lost between the wire and the pipeline."
    )
    assert pull.as_of == TODAY, (
        f"as_of is {pull.as_of}, expected the pull's own fetch date {TODAY}. F3-S1's "
        "manifest is the only place as_of lives for a ref; deriving it from the wall "
        "clock would make a re-opened workspace re-classify removals with no new evidence."
    )
    assert pull.digest, "the digest must be a function of the raw bytes at this ref"
    assert_no_socket(no_network)


@needs_orchestrator
def test_two_searches_that_differ_only_by_window_mint_different_refs(
    live_env, no_network
) -> None:
    """AC3 + the F3-S1 trap relayed at dispatch.

    `storage/pulls.py`'s memo key is `(root, pull_ref, config)` — the search
    window is NOT in it. That is only safe if the window is a function of the
    ref itself. Two pulls differing only in their date window must therefore
    be different refs; if they collide, the memo serves comps shaped under a
    window the caller never asked for, and no test downstream would notice.
    """
    set_ledger(spent=0)
    wire_a = Wire(one_record_per_query)
    ref_a = run_pull(**SEARCH, today=TODAY, transport=wire_a.transport).pull_ref

    narrow = {**SEARCH, "window_start_mmdd": "06-01", "window_end_mmdd": "06-30"}
    wire_b = Wire(one_record_per_query)
    ref_b = run_pull(**narrow, today=TODAY, transport=wire_b.transport).pull_ref

    assert ref_a != ref_b, (
        "two searches with different date windows minted the SAME pull_ref. The window "
        "is not part of storage/pulls.py's memo key, so it must be part of the ref."
    )
    assert load_shaped_pull(ref_b, Config()).as_of == TODAY
    assert_no_socket(no_network)


@needs_orchestrator
def test_completing_a_partial_pull_is_visible_to_the_loader(live_env, no_network) -> None:
    """The ADR-001 erratum trap, pinned as an outcome (dispatch, known traps).

    ADR-001 dropped `pull_digest` from the memo key on the premise "a refresh
    writes a new ref, it never rewrites one". Completing a partial pull breaks
    that premise if completion appends to the existing ref: the memo would
    keep serving the PRE-completion comps forever.

    Deliberately mechanism-free — mint a new ref on completion, or put the
    digest back in the key; either passes. What may not happen is a caller
    finishing a pull and still being shown the incomplete evidence.

    NOTE TO ANYONE EDITING THIS TEST: it must NOT call `cache_clear()` between
    the two loads. The staleness it exists to catch only exists while the memo
    is warm, and clearing it would make the test pass against the bug.
    """
    set_ledger(spent=0)

    def partial(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    wire_1 = Wire(partial)
    first = run_pull(**SEARCH, today=TODAY, transport=wire_1.transport)
    before = load_shaped_pull(first.pull_ref, Config())
    assert len(before.comps) == 4, (
        f"expected the 4 fetched windows' evidence, got {len(before.comps)} comps"
    )

    wire_2 = Wire(lambda request, n: one_record_per_query(request, 200 + n))
    second = run_pull(**SEARCH, today=TODAY, transport=wire_2.transport)
    assert second.complete is True

    after = load_shaped_pull(second.pull_ref, Config())
    assert len(after.comps) == 6, (
        f"after completing the pull the loader still sees {len(after.comps)} comps, not 6. "
        "The record-shaping memo is serving pre-completion evidence (ADR-001's erratum: "
        "either mint a new ref on completion, or restore pull_digest to the memo key). "
        "A missing cohort skews every downstream number (D24 §5a)."
    )
    assert_no_socket(no_network)


# ===========================================================================
# AC5 — refuse to START a pull the budget cannot afford
# ===========================================================================


@needs_orchestrator
def test_a_pull_that_cannot_be_afforded_is_refused_before_anything_is_sent(
    live_env, no_network
) -> None:
    """AC5. The failure mode this forbids is discovering the cap MID-pull.

    F0-S4 already fixed the same bug one level down (the budget check sat
    inside the paging loop). Half a pull is worse than none: the calls are
    spent, and a missing cohort skews every number computed from what came
    back (D24 §5a).
    """
    set_ledger(spent=MONTHLY_CALL_CAP - 5)  # 5 left, 6 needed
    wire = Wire(one_record_per_query)

    with pytest.raises(InsufficientCallBudgetError):
        run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert wire.requests == [], (
        f"{len(wire.requests)} requests went out on a pull that could not be afforded. "
        "The check belongs before the first send, not inside the loop."
    )
    assert load_ledger().calls_this_month == MONTHLY_CALL_CAP - 5, (
        "the ledger moved on a pull that was refused"
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_the_refusal_tells_the_caller_what_it_would_have_cost(live_env, no_network) -> None:
    """AC5's second clause: "says so in a way a caller can act on".

    Structured, not prose: the F2/F3 modal has to show "needs 6, 5 left" and
    the user has to be able to decide whether to narrow the search. A caller
    that has to regex an error message to find that out will not do it.
    """
    set_ledger(spent=MONTHLY_CALL_CAP - 5)
    wire = Wire(one_record_per_query)

    with pytest.raises(InsufficientCallBudgetError) as excinfo:
        run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    error = excinfo.value
    assert getattr(error, "required", None) == 6, (
        f"InsufficientCallBudgetError.required={getattr(error, 'required', None)!r}, "
        "expected 6 — the fetchable-query count this pull needs"
    )
    assert getattr(error, "remaining", None) == 5, (
        f"InsufficientCallBudgetError.remaining={getattr(error, 'remaining', None)!r}, "
        "expected 5 — what the month has left"
    )
    assert str(error).strip(), "the refusal carries no message at all"
    assert FAKE_KEY not in str(error), "the API key reached an error message (D16)"
    assert_no_socket(no_network)


@needs_orchestrator
def test_a_budget_exactly_equal_to_the_cost_is_affordable(live_env, no_network) -> None:
    """AC5's boundary — the off-by-one that either blocks a legal pull or
    overspends by one call. 6 fetchable queries, exactly 6 remaining."""
    set_ledger(spent=MONTHLY_CALL_CAP - 6)
    wire = Wire(one_record_per_query)

    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert len(wire.requests) == 6
    assert outcome.complete is True
    assert load_ledger().calls_this_month == MONTHLY_CALL_CAP
    assert_no_socket(no_network)


@needs_orchestrator
def test_affordability_is_judged_on_the_fetchable_subset_not_the_whole_plan(
    live_env, no_network
) -> None:
    """AC1 x AC5. A plan of 4 whose fetchable subset is 2 costs 2.

    Refusing it with 3 calls left would strand a user over calls the pull was
    never going to make — the same drift F2-S3 is [INVARIANT] against
    ("one function, two consumers, no drift between displayed and actual").
    """
    set_ledger(spent=MONTHLY_CALL_CAP - 3)  # 3 left; plan=4, fetchable=2
    wire = Wire(one_record_per_query)

    outcome = run_pull(
        **EMPTY_WINDOW_SEARCH, today=EMPTY_WINDOW_TODAY, transport=wire.transport
    )

    assert len(wire.requests) == 2, (
        "a pull costing 2 calls was refused (or mis-sent) against a 3-call remainder "
        "because affordability was judged on len(plan) instead of "
        "len(fetchable_queries(plan))"
    )
    assert outcome.calls_spent == 2
    assert_no_socket(no_network)


# ===========================================================================
# AC6 — a partial pull is represented, never silently absent
# ===========================================================================


@needs_orchestrator
def test_a_partial_pull_names_the_windows_it_is_missing(live_env, no_network) -> None:
    """AC6. D24/§5a: "a missing cohort skews every downstream number and must
    never be silently absent."

    The bar is that the gap is NAMED, not how it is spelled: this asserts a
    count and that the identifying facts of each missing window (its cohort
    year and its status) appear in the entry, leaving the exact string free.
    """
    set_ledger(spent=0)
    plan = plan_for(SEARCH, TODAY)
    fetchable = fetchable_queries(plan)
    last_two = fetchable[4:]

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert outcome.complete is False
    missing = list(outcome.missing)
    assert len(missing) == 2, (
        f"2 of 6 windows never arrived but PullOutcome.missing holds {missing!r}. "
        "Absence is honestly showable; silence is not."
    )
    assert outcome.calls_to_complete == 2, (
        f"calls_to_complete={outcome.calls_to_complete}, expected 2 — F4-S6 renders this "
        'verbatim ("2025 inactive missing · 1 call to complete") and a wrong number here '
        "is a wrong number shown to the user before they consent to spend"
    )
    blob = " ".join(str(m) for m in missing).lower()
    for query in last_two:
        assert str(query.year) in blob and query.status.lower() in blob, (
            f"the missing window ({query.year}, {query.status}) is not identifiable in "
            f"{missing!r}. A gap the user cannot name is a gap they cannot fix."
        )
    assert_no_socket(no_network)


@needs_orchestrator
def test_the_gap_survives_on_disk_and_is_readable_without_a_pull(
    live_env, no_network
) -> None:
    """AC6's durability half — "the MANIFEST says which".

    An in-memory `PullOutcome` is gone the moment the process ends. A user
    who reopens the workspace tomorrow must still be told what is missing,
    and finding out must not require spending a call to discover it.
    """
    set_ledger(spent=0)

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    spent_before = load_ledger().calls_this_month
    status = pull_status(outcome.pull_ref)

    assert status.complete is False
    assert len(list(status.missing)) == 2, (
        f"re-read from disk, the pull reports {list(status.missing)!r} missing — the "
        "manifest is not recording which windows are absent"
    )
    assert status.calls_to_complete == 2
    assert load_ledger().calls_this_month == spent_before, (
        "reading a pull's status spent a call. Reading must never be a mutation, and it "
        "must certainly never be a purchase."
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_a_complete_pull_reports_no_gap_at_all(live_env, no_network) -> None:
    """AC6's complement: `missing` must be empty when nothing is missing.

    Without this, "always report two missing windows" would pass every test
    above — and F4-S6 would show a permanent, false "evidence incomplete"
    warning that trains the user to ignore it.
    """
    set_ledger(spent=0)
    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert outcome.complete is True
    assert list(outcome.missing) == []
    assert outcome.calls_to_complete == 0
    assert list(pull_status(outcome.pull_ref).missing) == []
    assert_no_socket(no_network)


@needs_orchestrator
def test_a_partial_pull_is_still_usable(live_env, no_network) -> None:
    """AC6 x §5a: "Incomplete first pull -> usable, with the gap named loudly.
    With a 50-call cap, refusing to show anything until the set is perfect
    would strand you."

    So a partial pull must still resolve and still carry the evidence that
    did arrive — the gap is a label on it, not a reason to withhold it.
    """
    set_ledger(spent=0)

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 4:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    pull = load_shaped_pull(outcome.pull_ref, Config())
    assert len(pull.comps) == 4, (
        f"a partial pull resolved to {len(pull.comps)} comps; the 4 windows that did "
        "arrive must all be there"
    )
    assert_no_socket(no_network)


@needs_orchestrator
def test_an_empty_market_is_not_the_same_as_a_missing_window(live_env, no_network) -> None:
    """AC6's honesty invariant, and the distinction F0-S4 called out by name:
    a real, paid-for `[]` means "no comps here"; an absent response means "we
    do not know". Six 200s carrying `[]` is a COMPLETE pull of an empty
    market — reporting it as partial would send the user off re-spending
    calls to fill a gap that does not exist.
    """
    set_ledger(spent=0)
    wire = Wire(lambda request, n: ok([]))
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert outcome.complete is True, (
        "a pull where every window answered 200 with an empty result is complete, not "
        "partial — 'zero comps in radius' and 'the response never arrived' are opposite "
        "facts and must never share a state"
    )
    assert list(outcome.missing) == []
    assert len(raw_response_paths(outcome.pull_ref)) == 6, (
        "an empty-but-real result is a paid-for answer and is kept like any other "
        "(F3-S1: b'[]' is written, b'' is refused)"
    )
    assert_no_socket(no_network)


# ===========================================================================
# shape of the returned object — kept minimal on purpose
# ===========================================================================


@needs_orchestrator
def test_the_outcome_reports_the_plan_and_what_it_cost(live_env, no_network) -> None:
    """The fields the F2/F3/F4-S6 UI stories consume. Asserted once, here, so
    the higher layers do not each have to re-discover the shape."""
    set_ledger(spent=0)
    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert isinstance(outcome, PullOutcome)
    assert outcome.planned == 6 and outcome.fetchable == 6
    assert outcome.calls_spent == 6
    assert outcome.calls_to_complete == 0
    assert outcome.complete is True
    assert isinstance(outcome.pull_ref, str) and outcome.pull_ref
    assert_no_socket(no_network)


@needs_orchestrator
def test_the_api_key_never_reaches_the_cache_the_ledger_or_a_message(
    live_env, no_network
) -> None:
    """D16 / CLAUDE.md: "Don't read, print, or log it." This story writes more
    files than any other — a manifest, N raw responses, N meta sidecars and a
    ledger — and the params it persists are the ones the key travels beside.
    """
    set_ledger(spent=0)
    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)
    assert outcome.pull_ref

    for path in live_env.rglob("*"):
        if path.is_file() and path.name != "secrets.json":
            assert FAKE_KEY not in path.read_text(encoding="utf-8", errors="ignore"), (
                f"the API key was written to {path}"
            )
    assert_no_socket(no_network)


@needs_orchestrator
def test_the_manifest_is_json_a_human_can_read(live_env, no_network) -> None:
    """Not an AC — a cheap guard on the one artefact a user is told to go look
    at when a pull goes wrong. A manifest that is unreadable (or is not JSON)
    turns "2025 inactive missing" into a question nobody can answer.
    """
    set_ledger(spent=0)
    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    entry = live_env / "cache" / outcome.pull_ref
    manifests = [p for p in entry.glob("*.json") if p.is_file()]
    assert manifests, f"no manifest written under {entry}"
    for path in manifests:
        json.loads(path.read_text(encoding="utf-8"))
