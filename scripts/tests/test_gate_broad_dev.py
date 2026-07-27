"""T-S3 round 2 — developer-supporting tests for the broad-pull mode, beyond
QA's suite in test_gate_broad.py:

  - PM RULING (round-2 dispatch, not in QA's tests): --verify-window combined
    with --days-old is FORBIDDEN — nonzero exit, clear message, zero network,
    no ledger. Strict window verification doesn't apply to broad pulls.
  - --bathrooms optionality also holds on the WINDOWED (non-override) path.
  - Broad verdict: records with missing/unparseable listedDate are excluded
    from the verdict count but are NOT an error (run still completes / GO).
  - Broad verdict threshold is inclusive: exactly 15 in-window distinct => GO.
  - Broad-mode resume after a hard stop spends only the remaining call (D24).

ZERO network (httpx.MockTransport), isolated tmp cwd, frozen clock — same
harness conventions as QA's suites.
"""

import json
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

import gate


class FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 26)


BROAD_ARGV = [
    "gate.py",
    "--address", "3651 S Wood St, Chicago, IL 60609",
    "--bedrooms", "3:4",
    "--radius", "2.0",
    "--property-types", "Multi-Family,Apartment,Townhouse,Single Family,Condo",
    "--window-start", "07-28",
    "--window-end", "08-20",
    "--years-back", "3",
    "--days-old", "1:1095",
    "--confirm",
]

WINDOWED_NO_BATH_ARGV = [
    "gate.py",
    "--address", "3651 S Wood St, Chicago, IL 60609",
    "--bedrooms", "4",
    "--radius", "1.0",
    "--window-start", "07-28",
    "--window-end", "08-20",
    "--years-back", "3",
    "--confirm",
]

FIX_DIR = Path("fixtures/live-samples")
LEDGER = FIX_DIR / "ledger.json"
DECISION = Path("rentcomp-pm/docs/gate-decision.md")


def _rec(i, listed):
    r = {
        "id": f"listing-{i}",
        "formattedAddress": f"{3000 + i} S Wood St, Chicago, IL 60609",
        "status": "Active",
        "price": 1500 + 5 * i,
    }
    if listed is not None:
        r["listedDate"] = f"{listed}T00:00:00.000Z"
    return r


def responder_for(records):
    def responder(request):
        return httpx.Response(200, json=records, headers={"X-Total-Count": str(len(records))})
    return responder


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


def run_gate(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", list(argv))
    gate.main()


# ---------------------------------------------------------------------------
# PM ruling: --verify-window + --days-old is forbidden
# ---------------------------------------------------------------------------

def test_verify_window_with_days_old_forbidden(sandbox, monkeypatch, capsys):
    captured = install_transport(monkeypatch, responder_for([_rec(0, "2025-08-01")]))
    argv = [a for a in BROAD_ARGV if a != "--confirm"] + ["--verify-window"]
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, argv)
    assert exc.value.code not in (0, None)
    assert captured == [], "forbidden combo must never touch the network"
    assert not LEDGER.exists()
    out = capsys.readouterr().out
    assert "--verify-window" in out and "--days-old" in out, (
        "refusal message must name the conflicting flags"
    )


def test_verify_window_with_days_old_forbidden_even_with_confirm(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, responder_for([_rec(0, "2025-08-01")]))
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, BROAD_ARGV + ["--verify-window"])
    assert exc.value.code not in (0, None)
    assert captured == []


# ---------------------------------------------------------------------------
# --days-old literal must be explicit colon syntax (round-1 defect class:
# RentCast treats a bare daysOld value as the range MAXIMUM)
# ---------------------------------------------------------------------------

def test_days_old_bare_value_refused(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, responder_for([_rec(0, "2025-08-01")]))
    argv = ["1095" if a == "1:1095" else a for a in BROAD_ARGV]
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, argv)
    assert exc.value.code not in (0, None)
    assert captured == [], "a bare --days-old value must never reach the wire"
    assert not LEDGER.exists()


# ---------------------------------------------------------------------------
# --bathrooms optional on the windowed (non-override) path too
# ---------------------------------------------------------------------------

def test_bathrooms_omitted_absent_from_wire_in_windowed_mode(sandbox, monkeypatch):
    records = [_rec(i, "2025-08-01") for i in range(20)]
    captured = install_transport(monkeypatch, responder_for(records))
    run_gate(monkeypatch, WINDOWED_NO_BATH_ARGV)
    assert len(captured) == 6, "windowed mode still plans 2 calls per year back"
    for req in captured:
        assert "bathrooms" not in dict(req.url.params)


# ---------------------------------------------------------------------------
# Broad verdict details
# ---------------------------------------------------------------------------

def test_broad_missing_listed_date_excluded_from_verdict_not_an_error(sandbox, monkeypatch):
    """16 verifiable in-window + 10 with missing/unparseable listedDate:
    the unverifiable records must not count toward the verdict, and their
    presence must not fail the run => GO on the 16."""
    records = [_rec(i, "2025-08-01") for i in range(16)]
    records += [_rec(100 + i, None) for i in range(5)]           # listedDate absent
    records += [
        {**_rec(200 + i, None), "listedDate": "not-a-date"} for i in range(5)
    ]                                                             # unparseable
    install_transport(monkeypatch, responder_for(records))
    run_gate(monkeypatch, BROAD_ARGV)  # completes without SystemExit

    text = DECISION.read_text()
    assert "**Verdict: GO**" in text
    assert "16" in text  # in-window verdict basis


def test_broad_threshold_is_inclusive_15_in_window_is_go(sandbox, monkeypatch):
    records = [_rec(i, "2025-08-01") for i in range(15)]          # exactly 15 in-window
    records += [_rec(100 + i, "2025-01-10") for i in range(10)]   # out-of-window noise
    install_transport(monkeypatch, responder_for(records))
    run_gate(monkeypatch, BROAD_ARGV)
    assert "**Verdict: GO**" in DECISION.read_text()


def test_broad_14_in_window_is_no_go(sandbox, monkeypatch):
    records = [_rec(i, "2025-08-01") for i in range(14)]
    records += [_rec(100 + i, "2025-01-10") for i in range(10)]
    install_transport(monkeypatch, responder_for(records))
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, BROAD_ARGV)
    assert exc.value.code == 1
    assert "NO-GO" in DECISION.read_text()


# ---------------------------------------------------------------------------
# D24 on the broad path: resume after hard stop costs only the remainder
# ---------------------------------------------------------------------------

def test_broad_mode_resume_after_hard_stop_spends_only_remaining_call(sandbox, monkeypatch):
    records = [_rec(i, "2025-08-01") for i in range(20)]
    captured = install_transport(monkeypatch, responder_for(records))
    with pytest.raises(SystemExit):
        run_gate(monkeypatch, BROAD_ARGV + ["--max-calls", "1"])
    assert len(captured) == 1

    run_gate(monkeypatch, BROAD_ARGV)  # resume: call 1 is a cache hit
    assert len(captured) == 2, "resume must spend only the remaining call"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 2
