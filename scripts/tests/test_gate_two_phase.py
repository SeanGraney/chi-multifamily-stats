"""T-S3 — developer-supporting tests for the owner-addition ACs, beyond QA's
suite in scripts/tests/test_gate.py:

  B. Two-phase live-run wiring: phase 1 (--confirm --max-calls 1 spends exactly
     one call), phase 2 (--verify-window gives an offline verdict, zero spend,
     no key needed), phase 3 (resume: only the remaining calls are spent).
  C. Pagination capture: includeTotalCount sent on every request; X-Total-Count
     persisted per call in the ledger history; raw response body files stay raw.
  A. Builder edge cases QA left to the developer: rentcast_range(None, None)
     raises; rentcast_multi([]) raises.

ZERO network (httpx.MockTransport), isolated tmp cwd, frozen clock — same
harness conventions as QA's suite.

(Authored by dev on story/T-S3; relocated from scripts/tests_dev/ into
scripts/tests/ by QA at merge, per the dev's handoff note.)
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import gate

FROZEN_TODAY = date(2026, 7, 26)


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 26)


SUBJECT_ARGV = [
    "gate.py",
    "--address", "3651 S Wood St, Chicago, IL 60609",
    "--bedrooms", "4",
    "--bathrooms", "1.5",
    "--radius", "1.0",
    "--window-start", "07-28",
    "--window-end", "08-20",
    "--years-back", "3",
]

FIX_DIR = Path("fixtures/live-samples")
LEDGER = FIX_DIR / "ledger.json"


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-a-real-secret")
    monkeypatch.setattr(gate, "date", FrozenDate)
    return tmp_path


def install_transport(monkeypatch, responder):
    captured = []

    def handler(request):
        captured.append(request)
        return responder(request)

    real_client = httpx.Client

    def client_factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gate.httpx, "Client", client_factory)
    return captured


def run_gate(monkeypatch, extra=()):
    monkeypatch.setattr(sys, "argv", SUBJECT_ARGV + list(extra))
    gate.main()


def in_window_responder(request):
    """Return records whose listedDate sits mid-way through the daysOld range
    actually requested — so every response validates in-window for its own
    query. Also advertises X-Total-Count per the includeTotalCount param."""
    lo, hi = (int(x) for x in dict(request.url.params)["daysOld"].split(":"))
    listed = (FROZEN_TODAY - timedelta(days=(lo + hi) // 2)).isoformat() + "T00:00:00.000Z"
    records = [
        {
            "id": f"listing-{i}",
            "formattedAddress": f"{3600 + i} S Wood St, Chicago, IL 60609",
            "status": "Active",
            "price": 1800 + 10 * i,
            "listedDate": listed,
        }
        for i in range(20)
    ]
    return httpx.Response(200, json=records, headers={"X-Total-Count": "37"})


# ---------------------------------------------------------------------------
# C — includeTotalCount / X-Total-Count capture
# ---------------------------------------------------------------------------

def test_include_total_count_param_sent_on_every_call(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, in_window_responder)
    run_gate(monkeypatch, ["--confirm"])
    assert len(captured) == 6
    for req in captured:
        assert dict(req.url.params).get("includeTotalCount") == "true"


def test_x_total_count_persisted_in_ledger_raw_bodies_stay_raw(sandbox, monkeypatch):
    install_transport(monkeypatch, in_window_responder)
    run_gate(monkeypatch, ["--confirm"])

    ledger = json.loads(LEDGER.read_text())
    assert len(ledger["history"]) == 6
    for entry in ledger["history"]:
        assert entry["total_count"] == 37

    # D24: raw response files hold exactly the response body — no meta injected
    response_files = [p for p in FIX_DIR.glob("*.json") if p.name != "ledger.json"]
    assert len(response_files) == 6
    for p in response_files:
        data = json.loads(p.read_text())
        assert isinstance(data, list) and len(data) == 20
        assert set(data[0]) == {"id", "formattedAddress", "status", "price", "listedDate"}


def test_missing_x_total_count_header_recorded_as_none(sandbox, monkeypatch):
    install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=in_window_responder(req).json())
    )
    run_gate(monkeypatch, ["--confirm"])
    ledger = json.loads(LEDGER.read_text())
    assert all(e["total_count"] is None for e in ledger["history"])


# ---------------------------------------------------------------------------
# B — two-phase wiring
# ---------------------------------------------------------------------------

def test_two_phase_flow_spend_one_verify_offline_resume_remaining(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, in_window_responder)

    # Phase 1: exactly ONE call is spent, then the hard stop fires (exit 1)
    # with the paid-for response preserved on disk and on the ledger.
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, ["--confirm", "--max-calls", "1"])
    assert exc.value.code == 1
    assert len(captured) == 1
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 1
    response_files = [p for p in FIX_DIR.glob("*.json") if p.name != "ledger.json"]
    assert len(response_files) == 1

    # Phase 2: offline verdict — zero additional requests, zero ledger change,
    # normal (successful) return since the fetched response validates in-window.
    run_gate(monkeypatch, ["--verify-window"])
    assert len(captured) == 1, "--verify-window must never touch the network"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 1

    # Phase 3: resume — the already-paid call is a cache hit; only the
    # remaining 5 queries are spent.
    run_gate(monkeypatch, ["--confirm"])
    assert len(captured) == 6
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 6


def test_verify_window_needs_no_api_key_and_no_confirm(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, in_window_responder)
    with pytest.raises(SystemExit):
        run_gate(monkeypatch, ["--confirm", "--max-calls", "1"])
    assert len(captured) == 1

    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    run_gate(monkeypatch, ["--verify-window"])  # no key, no --confirm — still works
    assert len(captured) == 1


def test_verify_window_fails_on_out_of_window_record(sandbox, monkeypatch):
    def stray_responder(request):
        records = in_window_responder(request).json()
        records.append(
            {"id": "stray", "formattedAddress": "1 Stray St", "listedDate": "2000-01-01T00:00:00.000Z"}
        )
        return httpx.Response(200, json=records)

    install_transport(monkeypatch, stray_responder)
    with pytest.raises(SystemExit):
        run_gate(monkeypatch, ["--confirm", "--max-calls", "1"])

    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, ["--verify-window"])
    assert exc.value.code == 1


def test_verify_window_fails_on_missing_listed_date(sandbox, monkeypatch):
    def no_date_responder(request):
        records = in_window_responder(request).json()
        del records[0]["listedDate"]
        return httpx.Response(200, json=records)

    install_transport(monkeypatch, no_date_responder)
    with pytest.raises(SystemExit):
        run_gate(monkeypatch, ["--confirm", "--max-calls", "1"])

    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, ["--verify-window"])
    assert exc.value.code == 1


def test_verify_window_with_nothing_cached_exits_nonzero(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, in_window_responder)
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, ["--verify-window"])
    assert exc.value.code == 1
    assert captured == []


def test_verify_window_signature_matches_live_run(sandbox, monkeypatch):
    """The offline pass must look up the SAME cache signatures the live run
    writes — full run then verify finds all 6, verdict OK (normal return)."""
    captured = install_transport(monkeypatch, in_window_responder)
    run_gate(monkeypatch, ["--confirm"])
    assert len(captured) == 6
    run_gate(monkeypatch, ["--verify-window"])  # would exit 1 if any lookup missed
    assert len(captured) == 6


# ---------------------------------------------------------------------------
# A — builder edge cases beyond QA's suite
# ---------------------------------------------------------------------------

def test_rentcast_range_none_none_raises(sandbox):
    with pytest.raises(ValueError):
        gate.rentcast_range(None, None)


def test_rentcast_multi_empty_raises(sandbox):
    with pytest.raises(ValueError):
        gate.rentcast_multi([])
