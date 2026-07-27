"""F0-S4 — developer-authored Layer 2: the live path's seams.

QA owns the assembled behaviour (`test_rentcast_client_surface.py`): pagination,
the 401/429 taxonomy, the ledger, and the headline sink ordering assertion.
This file pins the parts of the **sink contract** and the **failure paths** that
QA's tests do not reach — most of them implementation judgment calls that F3-S4
will build on, so they need to be visible if a later story changes them.

ZERO LIVE CALLS. Every test goes through `live_client`, which is the only thing
in this file that sets `RENTCOMP_LIVE=1` and which cannot be called without an
`httpx.MockTransport` — a mock answers inside the process and never opens a
socket. `tests/conftest.py`'s autouse kill switch sits under it, and the key is
a fake.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rentcomp.client.rentcast import (
    AuthError,
    CallBudgetExceededError,
    RentCastClient,
    UpstreamError,
    fixture_signature,
)
from rentcomp.storage.ledger import MONTHLY_CALL_CAP, current_month, load_ledger

FAKE_KEY = "dev-fake-key-not-a-real-credential"

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
    return [{"id": f"listing-{offset + i}", "price": 2000 + i} for i in range(count)]


def paged(total: int, page_size: int = 500):
    def responder(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset") or 0)
        chunk = records(max(0, min(page_size, total - offset)), offset=offset)
        return httpx.Response(200, json=chunk, headers={"X-Total-Count": str(total)})

    return responder


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    return root


@pytest.fixture
def live_client(home, monkeypatch):
    """The ONLY route to live mode here, and it demands a mock transport."""

    def _make(responder, **kwargs):
        sent: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            return responder(request)

        monkeypatch.setenv("RENTCOMP_LIVE", "1")
        monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
        client = RentCastClient(transport=httpx.MockTransport(handler), **kwargs)
        return client, sent

    return _make


# ---------------------------------------------------------------------------
# the sink — D24's write-through seam, and what it must NOT see
# ---------------------------------------------------------------------------


def test_the_sink_never_sees_the_body_of_a_failed_call(live_client) -> None:
    """IMPLEMENTATION RULING, pinned because F3-S4 depends on it and nothing
    else states it: the sink fires on success only.

    D24's rule is "persist the response body, then parse" — it is about not
    losing *evidence* to a parse bug. A 401's JSON error blob is not evidence:
    writing it into `cache/<key>/raw/<sig>.json` would make F3-S4's resume
    logic believe that query was satisfied, so the missing page would never be
    re-fetched and the gap would close silently on nothing. Failures belong in
    the ledger's history, which is where §5a puts "which failed and why".
    """
    sunk: list[bytes] = []
    client, sent = live_client(
        lambda request: httpx.Response(401, json={"error": "Unauthorized"}),
        sink=lambda params, raw, meta: sunk.append(raw),
    )
    with pytest.raises(AuthError):
        client.fetch_listings(dict(QUERY))

    assert sunk == [], "an error body was handed to the cache-writing seam"
    assert len(sent) == 1
    # ...but the call is still remembered, with its status, because it was billed.
    ledger = load_ledger()
    assert ledger.calls_this_month == 1
    assert ledger.history[-1].status == 401


def test_the_sink_never_sees_a_redirect_body_either(live_client) -> None:
    """The same ruling, at the boundary that a `>= 400` guard silently misses.

    `httpx` does not follow redirects by default, so a 3xx arrives as an
    ordinary response object, not an exception — a guard written as "4xx and up
    is the failure case" hands the redirect page straight to the sink, and only
    the JSON parse further down objects. By then the harm is done: F3-S4 would
    find `cache/<key>/raw/<sig>.json` on disk, conclude that query was
    satisfied, and close the gap on `<html>moved</html>`. The sink fires on 2xx
    ONLY; everything else is a failure the ledger records and the cache doesn't.
    """
    sunk: list[bytes] = []
    client, sent = live_client(
        lambda request: httpx.Response(
            302, headers={"Location": "https://elsewhere.example/"}, content=b"<html>moved</html>"
        ),
        sink=lambda params, raw, meta: sunk.append(raw),
    )
    with pytest.raises(UpstreamError) as caught:
        client.fetch_listings(dict(QUERY))

    assert sunk == [], "a redirect body was handed to the cache-writing seam"
    assert "302" in str(caught.value), (
        "the redirect was reported as whatever it failed to parse as, not as the "
        "non-2xx status it actually was"
    )
    assert len(sent) == 1, "the redirect must not be followed — that spends a second call"
    ledger = load_ledger()
    assert ledger.calls_this_month == 1
    assert ledger.history[-1].status == 302


def test_the_sink_is_told_which_call_it_is_looking_at(live_client) -> None:
    """F3-S4 names one file per call (`y2025-inactive-off000.json`) and has to
    know the offset and the signature to do it, plus the total count to write a
    manifest that can compute the gap. Pinned as a contract, not an incidental
    dict: a sink that gets bytes and no identity cannot write a resumable set.
    """
    seen: list[tuple[dict, bytes, dict]] = []
    client, _ = live_client(paged(690), sink=lambda p, raw, meta: seen.append((p, raw, meta)))
    client.fetch_listings(dict(QUERY))

    assert len(seen) == 2, "the sink is per CALL, not per pull (§5a page granularity)"
    first, second = seen
    assert first[2]["offset"] == 0 and second[2]["offset"] == 500
    assert "offset" not in first[0], "page 0 must stay identical to an un-offset query"
    assert second[0]["offset"] == 500
    for params, _raw, meta in seen:
        assert meta["sig"] == fixture_signature(params), (
            "the sink's signature disagrees with the params it was handed — the cache would "
            "file the response under a name nothing can look up"
        )
        assert meta["status"] == 200 and meta["total_count"] == 690
        assert FAKE_KEY not in json.dumps(params), "the key reached the cache-writing seam"


def test_a_sink_failure_is_not_swallowed(live_client) -> None:
    """The sink IS the durability guarantee. Reporting a successful pull whose
    response was never persisted would be exactly the "paid twice" failure D24
    exists to prevent — so its exception propagates rather than being logged
    and ignored."""

    def explode(params, raw, meta):
        raise OSError("disk full")

    client, sent = live_client(paged(39), sink=explode)
    with pytest.raises(OSError):
        client.fetch_listings(dict(QUERY))
    assert len(sent) == 1
    assert load_ledger().calls_this_month == 1, "the call was billed and must stay counted"


# ---------------------------------------------------------------------------
# failure paths that still cost money
# ---------------------------------------------------------------------------


def test_a_connection_failure_still_counts_the_call(live_client) -> None:
    """The request left; whether an answer came back is not something the
    quota cares about. Counted, typed as an upstream problem, and left on the
    ledger with an unknown status so a later look can tell "billed, outcome
    unknown" from "billed, 429"."""

    def die(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset", request=request)

    client, sent = live_client(die)
    with pytest.raises(UpstreamError):
        client.fetch_listings(dict(QUERY))

    assert len(sent) == 1
    ledger = load_ledger()
    assert ledger.calls_this_month == 1
    assert ledger.history[-1].status is None


def test_the_key_is_scrubbed_out_of_an_upstream_error_message(live_client) -> None:
    """CLAUDE.md: "Don't read, print, or log it." An exception message is the
    one place a secret escapes without anyone deciding to print it — it lands
    in a traceback, a PM report, and this transcript. RentCast has no reason to
    echo the key, which is precisely why nobody would notice if it did."""
    client, _ = live_client(
        lambda request: httpx.Response(401, json={"error": f"bad key {FAKE_KEY}"})
    )
    with pytest.raises(AuthError) as excinfo:
        client.fetch_listings(dict(QUERY))

    message = str(excinfo.value)
    assert FAKE_KEY not in message and FAKE_KEY not in repr(excinfo.value)
    assert "<redacted>" in message
    assert excinfo.value.status_code == 401


# ---------------------------------------------------------------------------
# pagination arithmetic — every extra iteration is a real call
# ---------------------------------------------------------------------------


def test_a_third_page_is_fetched_when_the_total_says_so(live_client) -> None:
    """Three pages, and the offsets advance by what was actually returned
    rather than by `limit` — a client that assumed a full page would skip
    records the moment a server returned a short one mid-set."""
    client, sent = live_client(paged(1200))
    result = client.fetch_listings(dict(QUERY))

    assert [request.url.params.get("offset") for request in sent] == [None, "500", "1000"]
    assert result.calls_spent == 3 and result.fetched == 1200 and result.complete is True
    assert len({record["id"] for record in result.records}) == 1200


def test_an_empty_page_ends_the_pull_even_if_the_total_disagrees(live_client) -> None:
    """The unbounded-loop guard, and it is a money bug rather than a hang: a
    server that keeps answering "no records" while claiming 690 would bill one
    call per iteration until the monthly cap stopped it."""
    client, sent = live_client(
        lambda request: httpx.Response(200, json=[], headers={"X-Total-Count": "690"})
    )
    result = client.fetch_listings(dict(QUERY))

    assert len(sent) == 1, f"the client kept paging an empty response {len(sent)} times"
    assert result.fetched == 0
    assert result.complete is False and result.missing == 690, (
        "an empty answer to a 690-record query is a gap, not an empty market"
    )


def test_max_calls_zero_sends_nothing_and_says_so(live_client) -> None:
    """A degenerate budget must be a no-op, not a raise: the caller asked for
    zero calls and got zero calls. `complete=False` is what stops the empty
    result from reading as "this market has no comps"."""
    client, sent = live_client(paged(690))
    result = client.fetch_listings(dict(QUERY), max_calls=0)

    assert sent == [] and result.calls_spent == 0
    assert result.complete is False
    assert load_ledger().calls_this_month == 0


# ---------------------------------------------------------------------------
# nothing is CONSTRUCTED when no call is possible
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "max_calls"), [(MONTHLY_CALL_CAP, None), (0, 0)], ids=["budget-gone", "max-calls-0"]
)
def test_no_transport_is_built_when_no_call_could_be_made(
    live_client, home: Path, monkeypatch, seed: int, max_calls
) -> None:
    """One step stronger than "no request was sent": no transport is even
    constructed.

    QA's guard proves this for fixture mode. The same property has to hold on
    the *live* path when the answer is already known — a client that opens a
    connection to discover it has no budget is one refactor away from opening
    it and then using it, and the check that stopped it would be the one
    nobody re-reads.
    """
    (home / "ledger.json").write_text(
        json.dumps({"month": current_month(), "calls_this_month": seed, "history": []}),
        encoding="utf-8",
    )
    client, sent = live_client(paged(39))
    monkeypatch.setattr(
        RentCastClient,
        "_open",
        lambda self, key: pytest.fail("a transport was built when no call was possible"),
    )

    try:
        result = client.fetch_listings(dict(QUERY), max_calls=max_calls)
    except CallBudgetExceededError:
        pass  # the budget-gone answer
    else:
        assert result.calls_spent == 0 and result.complete is False
    assert sent == []
