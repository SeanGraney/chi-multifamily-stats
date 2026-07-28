"""F0-S4 — the assembled client: pagination, error mapping, the call ledger.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Pure functions and the D17 guard live in `tests/unit/`
(`test_rentcast_client.py`, `test_live_call_guard.py`); this file is the
workhorse layer — what the client *does* when it is allowed to send.

WHY LAYER 2, AND WHY THERE IS NO TestClient IN IT
-------------------------------------------------
AGENT_QA.md's step 2 is "given state X, the boundary returns Y". F0-S4 adds
no HTTP route — `POST /api/search` is F3-S2's, and the query plan behind it is
F4-S1's — so the boundary under test is the client's own public surface,
driven end to end with an `httpx.MockTransport`. These are logic assertions
about assembled behaviour across several calls, they would be a Python bug
when they fail (step 3), and they need no browser. Layer 2 it is; the
directory is where the *layer* lives, not where `TestClient` lives.

ZERO LIVE CALLS — HOW THIS FILE STAYS SAFE
------------------------------------------
42 of 50 monthly calls remain and they are the owner's. Every test here runs
through `live_client(...)`, which is the ONLY way this module sets
`RENTCOMP_LIVE=1` and which cannot be called without an `httpx.MockTransport`
— a mock transport answers inside the process and never opens a socket. Two
independent backstops sit under it: `tests/conftest.py`'s autouse `no_network`
fixture (sockets raise, suite-wide) and a fake key that is not a credential.
This is the same pattern `scripts/tests/test_gate.py` uses to test a script
whose whole job is spending money.

CONTRACT PINNED HERE (QA-PROPOSED — PM confirms; see
`tests/unit/test_rentcast_client.py` for the full table)
  ``RentCastClient(mode=None, *, transport=None, api_key=None, sink=None)``
  ``fetch_listings(params, *, max_calls=None) -> ListingsResult``
  ``rentcomp.storage.ledger`` — ``ledger_path()`` / ``load_ledger()`` /
      ``MONTHLY_CALL_CAP == 50``, file ``<RENTCOMP_HOME>/ledger.json``,
      gate-compatible shape ``{"calls_this_month": int, "history": [...]}``

EXPECTED RED STATE TODAY: two import-gate tests FAIL naming what is missing;
everything else SKIPS.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

try:
    from rentcomp.client.rentcast import (
        AuthError,
        CallBudgetExceededError,
        RateLimitError,
        RentCastClient,
        RentCastError,
        UpstreamError,
    )

    _CLIENT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _CLIENT_ERROR = _exc

try:
    from rentcomp.storage.ledger import MONTHLY_CALL_CAP, ledger_path, load_ledger

    _LEDGER_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _LEDGER_ERROR = _exc

needs_client = pytest.mark.skipif(
    _CLIENT_ERROR is not None, reason=f"rentcomp.client.rentcast missing (F0-S4 red): {_CLIENT_ERROR}"
)
needs_ledger = pytest.mark.skipif(
    _LEDGER_ERROR is not None, reason=f"rentcomp.storage.ledger missing (F0-S4 red): {_LEDGER_ERROR}"
)

#: Not a credential. The suite must never hold one.
FAKE_KEY = "qa-fake-key-not-a-real-credential"

#: A realistic broad query — the shape T-S3 actually sent (F4-S7 §3).
QUERY = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "propertyType": "Multi-Family|Apartment|Townhouse|Single Family|Condo",
    "status": "Inactive",
    "daysOld": "1:1095",
    "limit": 500,
    "includeTotalCount": "true",
}


def records(count: int, offset: int = 0) -> list[dict]:
    return [
        {
            "id": f"listing-{offset + i}",
            "formattedAddress": f"{offset + i} W Fake St, Chicago, IL 60609",
            "price": 2000 + i,
            "listedDate": "2025-08-01T00:00:00.000Z",
            "status": "Inactive",
        }
        for i in range(count)
    ]


class Recorder:
    """A `MockTransport` handler that logs every request it is asked to make.

    The log is the evidence for the assertions that matter most here: *how
    many* calls were sent (each is 1/50 of the month) and *with what*.
    """

    def __init__(self, responder):
        self.requests: list[httpx.Request] = []
        self._responder = responder

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responder(request, len(self.requests) - 1)

    @property
    def offsets(self) -> list[str | None]:
        return [r.url.params.get("offset") for r in self.requests]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def paged(total: int, page_size: int = 500):
    """A server that paginates exactly the way RentCast does: `limit`-capped
    pages, `X-Total-Count` on every response, `offset` selecting the page."""

    def responder(request: httpx.Request, index: int) -> httpx.Response:
        offset = int(request.url.params.get("offset") or 0)
        chunk = records(max(0, min(page_size, total - offset)), offset=offset)
        return httpx.Response(200, json=chunk, headers={"X-Total-Count": str(total)})

    return responder


def constant(status: int, body=None, headers: dict | None = None):
    def responder(request: httpx.Request, index: int) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else [], headers=headers or {})

    return responder


def sequence(*responders):
    """One responder per call, in order — for "page 1 works, page 2 429s"."""

    def responder(request: httpx.Request, index: int) -> httpx.Response:
        return responders[min(index, len(responders) - 1)](request, index)

    return responder


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    return root


@pytest.fixture
def live_client(home, monkeypatch):
    """The ONLY route to live mode in this file — and it demands a mock
    transport, so `RENTCOMP_LIVE=1` can never be set here without one."""

    def _make(responder, *, key: str | None = FAKE_KEY, **kwargs):
        recorder = Recorder(responder)
        monkeypatch.setenv("RENTCOMP_LIVE", "1")
        if key is not None:
            monkeypatch.setenv("RENTCAST_API_KEY", key)
        client = RentCastClient(transport=recorder.transport(), **kwargs)
        return client, recorder

    return _make


def seed_ledger(home: Path, calls: int) -> Path:
    """Pre-spend `calls` of the month's budget.

    PROPOSED shape, deliberately the one `scripts/gate.py` already writes to
    `fixtures/live-samples/ledger.json`: `{"calls_this_month": int,
    "history": [...]}`. One format for the only two things in this project
    that can spend money.
    """
    path = home / "ledger.json"
    path.write_text(json.dumps({"calls_this_month": calls, "history": []}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# existence — legible red
# ---------------------------------------------------------------------------


def test_client_is_importable() -> None:
    assert _CLIENT_ERROR is None, f"rentcomp.client.rentcast is not importable: {_CLIENT_ERROR}"


def test_call_ledger_is_importable() -> None:
    """WORKFLOW.md §6 + CLAUDE.md: "The monthly call cap is enforced in code."
    Enforced in code means there is code — a ledger the client consults before
    sending and increments when it sends."""
    assert _LEDGER_ERROR is None, (
        "Cannot import the call ledger from rentcomp.storage.ledger "
        f"(QA-proposed path): {_LEDGER_ERROR}"
    )


# ---------------------------------------------------------------------------
# auth (spec §3.1)
# ---------------------------------------------------------------------------


@needs_client
def test_the_key_travels_in_the_x_api_key_header(live_client) -> None:
    """Spec §3.1 and the committed schema's `securitySchemes` (apiKey, in:
    header, name: X-Api-Key). A key in the query string would also land in
    logs and in RentCast's access logs."""
    client, recorder = live_client(paged(3))
    client.fetch_listings(dict(QUERY))

    (request,) = recorder.requests
    assert request.headers.get("X-Api-Key") == FAKE_KEY
    assert "authorization" not in {k.lower() for k in request.headers}
    assert str(request.url).startswith("https://api.rentcast.io/v1/listings/rental/long-term")
    assert "api_key" not in str(request.url) and FAKE_KEY not in str(request.url), (
        "the key is in the URL — it would be logged by every proxy between here and RentCast"
    )


@needs_client
def test_every_param_reaches_the_wire_unmangled(live_client) -> None:
    """The colon ranges and pipe-joined multi-values must survive URL
    encoding as VALUES — percent-encoding is fine, silently splitting on `|`
    or dropping `:` is not (that was the round-1 defect that returned an empty
    market)."""
    client, recorder = live_client(paged(1))
    client.fetch_listings(dict(QUERY))

    params = recorder.requests[0].url.params
    assert params.get("daysOld") == "1:1095"
    assert params.get("bedrooms") == "3:4"
    assert params.get("propertyType") == "Multi-Family|Apartment|Townhouse|Single Family|Condo"
    assert params.get("status") == "Inactive"


# ---------------------------------------------------------------------------
# pagination + X-Total-Count (F4-S7 §3 — evidence-required, not speculative)
# ---------------------------------------------------------------------------


@needs_client
def test_pagination_follows_offset_until_the_set_is_complete(live_client) -> None:
    """The real numbers from the gate: 690 matching Inactive records, a 500
    ceiling. The client must read `X-Total-Count` and fetch the remainder —
    "silent truncation is not an option: a truncated pull skews every
    downstream count" (F4-S7 §3)."""
    client, recorder = live_client(paged(690))
    result = client.fetch_listings(dict(QUERY))

    assert len(recorder.requests) == 2, f"expected 2 calls (500 + 190), got {len(recorder.requests)}"
    assert recorder.offsets[1] == "500", f"second page asked for offset {recorder.offsets[1]!r}"
    assert result.fetched == 690 and len(result.records) == 690
    assert result.total_count == 690
    assert result.complete is True and not result.missing
    assert result.calls_spent == 2, "both pages cost a call and both must be counted"
    ids = [r["id"] for r in result.records]
    assert len(set(ids)) == 690, "pages were concatenated with overlap or a gap"


@needs_client
def test_a_single_complete_page_costs_exactly_one_call(live_client) -> None:
    """39 of 39 (the gate's real Active response). Paging "just in case" would
    spend a call to learn nothing — at 42 calls left that is 2.4% of the
    remaining budget per pull."""
    client, recorder = live_client(paged(39))
    result = client.fetch_listings(dict(QUERY))
    assert len(recorder.requests) == 1
    assert result.complete is True and result.calls_spent == 1


@needs_client
def test_a_short_page_without_a_total_count_ends_the_pull(live_client) -> None:
    """Defensive: if the server does not echo `X-Total-Count`, a page shorter
    than `limit` is the end of the set (spec §3.2's "pagination until short
    page"). The failure this prevents is an unbounded paging loop billing one
    call per iteration."""
    client, recorder = live_client(constant(200, records(12)))
    result = client.fetch_listings(dict(QUERY))
    assert len(recorder.requests) == 1
    assert result.fetched == 12
    assert result.complete is True


@needs_client
def test_a_capped_pull_names_the_gap_instead_of_pretending_to_be_whole(live_client) -> None:
    """D24 / §5a's "incomplete first pull" rule: usable, with the gap named
    loudly ("Inactive: 500/690 — 1 call to complete"), because with a 50-call
    cap refusing to show anything until the set is perfect would strand the
    user. What is forbidden is `complete=True` on a 500-of-690 pull.
    """
    client, recorder = live_client(paged(690))
    result = client.fetch_listings(dict(QUERY), max_calls=1)

    assert len(recorder.requests) == 1, "max_calls=1 must stop after one call"
    assert result.fetched == 500
    assert result.total_count == 690
    assert result.complete is False
    assert result.missing == 190, f"the gap must be countable, got {result.missing!r}"
    assert len(result.records) == 500, "the records already paid for are kept, not discarded"


def unbounded(page_size: int = 500):
    """A server that returns full pages forever and never echoes
    `X-Total-Count` — the state in which the size of the gap is unknowable."""

    def responder(request: httpx.Request, index: int) -> httpx.Response:
        offset = int(request.url.params.get("offset") or 0)
        return httpx.Response(200, json=records(page_size, offset=offset))

    return responder


@needs_client
@needs_ledger
@pytest.mark.parametrize("stop", ["max-calls", "monthly-cap"])
def test_a_pull_stopped_early_with_no_total_count_is_never_reported_complete(
    live_client, home: Path, stop: str
) -> None:
    """`complete` is authoritative over `missing` — the case where they
    disagree.

    A full page came back and the server echoed no `X-Total-Count`, so the pull
    was stopped by the budget short by an amount nobody can name. `missing`
    has no honest value to report here and is 0; if `complete` were derived
    from `missing` (or from "did we reach the total we know about"), the result
    would read as "this is the whole market" — the one thing spec §7 forbids,
    and the exact reading every downstream count in F4/F5 would be computed on.

    Both stop causes are asserted because they are different branches that must
    reach the same conclusion: the caller's `max_calls` and the month's cap.

    Regression origin: QA verify pass on F0-S4 (2026-07-27). Removing the
    `not stopped_early` term from the `complete` expression passed all 568
    existing tests — this is the spec that holds it.
    """
    if stop == "max-calls":
        client, recorder = live_client(unbounded())
        result = client.fetch_listings(dict(QUERY), max_calls=1)
    else:
        seed_ledger(home, MONTHLY_CALL_CAP - 1)
        client, recorder = live_client(unbounded())
        result = client.fetch_listings(dict(QUERY))

    assert len(recorder.requests) == 1
    assert result.fetched == 500, "the page already paid for is kept"
    assert result.total_count is None, "the server named no total — nothing may invent one"
    assert result.complete is False, (
        "a budget-stopped pull with no X-Total-Count reported itself COMPLETE. It is short by "
        "an unnameable amount; `complete` is the authoritative signal precisely because "
        "`missing` cannot be computed here"
    )
    assert result.missing == 0, (
        "missing must not guess — an invented gap size is worse than a named unknown"
    )


# ---------------------------------------------------------------------------
# 401 vs 429 (spec §7 — distinct, plain, never partial)
# ---------------------------------------------------------------------------


@needs_client
def test_a_401_surfaces_as_its_own_error(live_client) -> None:
    client, recorder = live_client(constant(401, {"error": "Unauthorized"}))
    with pytest.raises(AuthError) as excinfo:
        client.fetch_listings(dict(QUERY))
    assert str(excinfo.value).strip(), "AuthError carries no message to show the user"
    assert FAKE_KEY not in str(excinfo.value), "the error message quotes the API key"


@needs_client
def test_a_429_surfaces_as_its_own_error(live_client) -> None:
    client, recorder = live_client(constant(429, {"error": "Too Many Requests"}))
    with pytest.raises(RateLimitError) as excinfo:
        client.fetch_listings(dict(QUERY))
    assert str(excinfo.value).strip()


@needs_client
def test_the_two_are_distinguishable_and_say_different_things(live_client) -> None:
    """The AC's word is "distinct". They demand opposite user actions — fix
    your key vs stop calling — so a shared message would be worse than no
    message."""
    client_a, _ = live_client(constant(401))
    with pytest.raises(RentCastError) as unauthorized:
        client_a.fetch_listings(dict(QUERY))
    client_b, _ = live_client(constant(429))
    with pytest.raises(RentCastError) as limited:
        client_b.fetch_listings(dict(QUERY))

    assert type(unauthorized.value) is not type(limited.value)
    assert str(unauthorized.value) != str(limited.value)
    for error, code in ((unauthorized.value, 401), (limited.value, 429)):
        assert getattr(error, "status_code", None) == code, (
            f"{type(error).__name__} does not carry status_code={code} — the API layer needs it "
            "to choose a response code without string-matching a message"
        )


@needs_client
def test_a_429_is_not_retried_automatically(live_client) -> None:
    """A retry loop against a metered API spends the budget it is reacting to.
    One call in, one call counted, one error out."""
    client, recorder = live_client(constant(429))
    with pytest.raises(RateLimitError):
        client.fetch_listings(dict(QUERY))
    assert len(recorder.requests) == 1, (
        f"the client retried a 429 {len(recorder.requests) - 1} time(s) — every retry is a real call"
    )


@needs_client
@pytest.mark.parametrize("status", [400, 403, 404, 500, 502, 503])
def test_other_failures_are_typed_too_and_never_look_like_an_empty_market(
    live_client, status: int
) -> None:
    """An unhandled status returning `[]` would render as "0 comps in radius"
    — the F4 empty state — and send the user off widening a radius to fix a
    server outage."""
    client, _ = live_client(constant(status, {"error": "nope"}))
    with pytest.raises(RentCastError) as excinfo:
        client.fetch_listings(dict(QUERY))
    assert isinstance(excinfo.value, (UpstreamError, AuthError, RateLimitError))


@needs_client
def test_a_failure_on_page_two_never_reports_a_complete_pull(live_client) -> None:
    """Spec §7's "never partial-render", at the seam where it is decided.
    Page 1 succeeded and cost a call; page 2 was rate-limited. The two honest
    answers are "raise" or "return 500 of 690, incomplete". The forbidden one
    is a result that claims to be the whole market.
    """
    client, recorder = live_client(sequence(paged(690), constant(429)))
    try:
        result = client.fetch_listings(dict(QUERY))
    except RateLimitError:
        pass  # honest answer #1
    else:  # honest answer #2
        assert result.complete is False, (
            "a pull whose second page 429'd reported itself complete — every downstream count "
            "would be computed on 500 of 690 records with no signal"
        )
        assert result.missing == 190
    assert len(recorder.requests) == 2


# ---------------------------------------------------------------------------
# the ledger (WORKFLOW.md §6 — the cap is enforced in code)
# ---------------------------------------------------------------------------


@needs_client
@needs_ledger
def test_the_cap_is_fifty(home: Path) -> None:
    assert MONTHLY_CALL_CAP == 50, "the plan is 50 calls/month (WORKFLOW.md §6)"
    assert Path(ledger_path()) == home / "ledger.json"


@needs_client
@needs_ledger
def test_every_sent_request_is_counted_at_send_time(live_client, home: Path) -> None:
    """D24/§5a: "the ledger increments when a request is *sent*, not when a
    batch completes — the quota is spent either way".

    `seed_ledger(home, 0)` (F3-S1 note): a truly untouched `home` now seeds
    itself from the gate's spend on first read (F3-S1's lazy-seed AC) — a
    real behavioural change to `load_ledger()`, not a test bug. Writing an
    explicit zero-call ledger keeps this test's own invariant (sent requests
    increment the count 1:1) isolated from that unrelated feature.
    """
    seed_ledger(home, 0)
    client, recorder = live_client(paged(690))
    client.fetch_listings(dict(QUERY))
    assert load_ledger().calls_this_month == 2 == len(recorder.requests)


@needs_client
@needs_ledger
def test_a_failed_call_still_counts_against_the_month(live_client, home: Path) -> None:
    """RentCast charged for it. A ledger that only counts successes drifts
    below the truth and the cap stops protecting anything.

    `seed_ledger(home, 0)` — see the F3-S1 note on
    `test_every_sent_request_is_counted_at_send_time`.
    """
    seed_ledger(home, 0)
    client, _ = live_client(constant(429))
    with pytest.raises(RateLimitError):
        client.fetch_listings(dict(QUERY))
    assert load_ledger().calls_this_month == 1


@needs_client
@needs_ledger
def test_the_cap_blocks_the_call_before_it_is_sent(live_client, home: Path) -> None:
    """The whole point: at 50/50 the client refuses, and refuses *without
    sending*. A cap checked after the request has already spent the thing it
    was protecting."""
    seed_ledger(home, MONTHLY_CALL_CAP)
    client, recorder = live_client(paged(39))
    with pytest.raises(CallBudgetExceededError):
        client.fetch_listings(dict(QUERY))
    assert recorder.requests == [], "a request was sent with the monthly budget already exhausted"
    assert load_ledger().calls_this_month == MONTHLY_CALL_CAP, "a blocked call was still counted"


@needs_client
@needs_ledger
def test_the_cap_stops_pagination_without_discarding_what_was_paid_for(
    live_client, home: Path
) -> None:
    """The nastiest budget case: one call left, a two-page pull. Page 1 is
    fetched and kept; page 2 is refused; the gap is named. Throwing an
    exception that loses page 1 would burn the last call of the month for
    nothing (D24: "a paid-for response is never lost")."""
    seed_ledger(home, MONTHLY_CALL_CAP - 1)
    client, recorder = live_client(paged(690))

    try:
        result = client.fetch_listings(dict(QUERY))
    except CallBudgetExceededError:
        pytest.fail(
            "the client discarded a page it had already paid for rather than returning an "
            "incomplete result (D24 §5a: incomplete is usable, lost is not)"
        )
    assert len(recorder.requests) == 1
    assert result.fetched == 500 and result.complete is False and result.missing == 190
    assert load_ledger().calls_this_month == MONTHLY_CALL_CAP


@needs_client
@needs_ledger
def test_the_ledger_never_records_the_api_key(live_client, home: Path) -> None:
    """The ledger is a params-and-status record and it is the kind of file a
    developer pastes into a report."""
    client, _ = live_client(paged(39))
    client.fetch_listings(dict(QUERY))
    assert FAKE_KEY not in (home / "ledger.json").read_text(encoding="utf-8")


@needs_client
@needs_ledger
def test_a_fixture_mode_read_never_touches_the_ledger(home: Path) -> None:
    """Fixture mode is free. If it wrote a ledger entry, the month's budget
    would drain by running the test suite."""
    ledger_file = seed_ledger(home, 3)
    before = ledger_file.read_bytes()
    try:
        RentCastClient().fetch_listings(dict(QUERY))
    except RentCastError:
        pass
    assert ledger_file.read_bytes() == before


# ---------------------------------------------------------------------------
# D24's write-through seam (what F3-S4 plugs the cache into)
# ---------------------------------------------------------------------------


class TestProposedContract:
    """QA-proposed behaviour the story text does not state. The PM confirms
    (or overrides) before these count as locked."""

    @needs_client
    def test_raw_bytes_reach_the_sink_before_anything_is_parsed(self, live_client) -> None:
        """PROPOSED — the `sink` callback, and D24's reason for it: "persist
        the response body to disk, *then* run Pydantic. A validation bug or a
        schema surprise must never cost an API call."

        F0-S4 has no cache to write to (F3-S1/F3-S4 own that), so what this
        story owes them is the seam. The test proves ordering the only way it
        can be proven: the body is unparseable, and the sink still sees the
        exact bytes.
        """
        sunk: list[bytes] = []

        def responder(request: httpx.Request, index: int) -> httpx.Response:
            return httpx.Response(200, content=b"{not json at all", headers={"X-Total-Count": "1"})

        client, _ = live_client(responder, sink=lambda params, raw, meta: sunk.append(raw))
        with pytest.raises(Exception):
            client.fetch_listings(dict(QUERY))
        assert sunk == [b"{not json at all"], (
            "the sink never saw the bytes of a response that failed to parse — that call is "
            "money spent and nothing kept (D24)"
        )

    @needs_client
    def test_the_sink_sees_every_page_of_a_paginated_pull(self, live_client) -> None:
        """PROPOSED: per CALL, not per pull (§5a's per-call file granularity —
        `y2025-inactive-off000.json` — is what makes a partial set meaningful
        and resumable)."""
        sunk: list[bytes] = []
        client, _ = live_client(paged(690), sink=lambda params, raw, meta: sunk.append(raw))
        client.fetch_listings(dict(QUERY))
        assert len(sunk) == 2
        assert json.loads(sunk[0])[0]["id"] == "listing-0"
        assert json.loads(sunk[1])[0]["id"] == "listing-500"

    @needs_client
    def test_records_come_back_as_raw_records_not_validated_models(self, live_client) -> None:
        """PROPOSED scope line: `models/dto.py` is empty and its own docstring
        says F4-S2 populates it "against fixtures/live-samples/ ground truth".
        The client hands back what the wire said; a validation error here would
        discard a usable comp AND a paid-for call."""
        client, _ = live_client(paged(2))
        result = client.fetch_listings(dict(QUERY))
        assert all(isinstance(record, dict) for record in result.records)

    @needs_client
    @needs_ledger
    def test_a_new_month_starts_from_zero(self, live_client, home: Path) -> None:
        """PROPOSED, and flagged to the PM as possibly F3-S4's: a ledger that
        counts calls without recording WHICH month never resets, so the tool
        bricks itself permanently at 50 lifetime calls. A stamped month is the
        smallest thing that makes "calls this month" true.
        """
        (home / "ledger.json").write_text(
            json.dumps({"month": "2020-01", "calls_this_month": MONTHLY_CALL_CAP, "history": []}),
            encoding="utf-8",
        )
        client, recorder = live_client(paged(1))
        result = client.fetch_listings(dict(QUERY))
        assert len(recorder.requests) == 1, "a cap recorded for a past month blocked a fresh month"
        assert result.calls_spent == 1
        assert load_ledger().calls_this_month == 1
