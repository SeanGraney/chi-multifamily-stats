"""T-S3 round 2 — QA tests for the owner-authorized BROAD pull mode, written
BEFORE the developer change (expected RED against current gate.py).

Round-1 live run was NO-GO on real sparsity (1 comp), so round 2 pulls broad
and windows LOCALLY:

  R1. `--days-old` override (literal RentCast range string, e.g. "1:1095"):
      exactly ONE call per status (2 total) carrying that literal `daysOld` —
      no computed per-year windows on the wire. Because no computed window is
      embedded, cache signatures become DATE-INDEPENDENT: the same override
      run on a different calendar day must be all cache hits (pinned here).
  R2. `--bathrooms` becomes optional: when omitted, NO `bathrooms` key appears
      in the wire params at all (not "", not "*:*" — absent), so records with
      missing bathroom data are not excluded server-side. When provided it
      still goes on the wire verbatim.
  R3. `--bedrooms "3:4"` passes through verbatim as `bedrooms=3:4`.
  R4. Local windowing verdict in broad mode: GO/NO-GO threshold (>=15
      distinct) applies to records whose listedDate falls inside ANY of the
      years_back padded seasonal windows (existing compute_window math /
      validate_window semantics: [start-90, end+90], boundaries inclusive).
      Out-of-window records are NOT an error in broad mode. The decision
      record reports BOTH the in-window distinct count (verdict basis) and
      the raw distinct total (diagnostic), plus per-window counts.
      Expected minimal decision-record contract (wording free, attribution
      pinned by regex): a line attributing the in-window count containing
      "in-window" (or "in window"), a line attributing the raw diagnostic
      count containing "raw", and one line per window year containing that
      year and its in-window count. Different labels are negotiable via the
      PM; QA updates the regexes then.
  R5. Existing D24 invariants hold on the new code path — hard stop at
      --max-calls still fires in broad mode with fetched responses preserved;
      X-Total-Count still captured per ledger entry (asserted inside R1).

Windowed (non-override) mode is pinned unchanged by the existing 29-test
suite (6 computed calls, strict --verify-window semantics) — those must stay
green alongside these.

ZERO network (httpx.MockTransport), isolated tmp cwd, frozen clock — same
harness conventions as test_gate.py.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

import gate


class FrozenDate(date):
    """today = 2026-07-26 (the round-1 run date; keeps window math familiar)."""
    @classmethod
    def today(cls):
        return cls(2026, 7, 26)


class FrozenDateLater(date):
    """today = 2026-08-15 — a DIFFERENT calendar day, for the
    signature-date-independence pin (R1)."""
    @classmethod
    def today(cls):
        return cls(2026, 8, 15)


# Owner-authorized round-2 pull (QUEUE.md, main@8b693b7): broad daysOld,
# radius 2.0, bedrooms 3:4, five property types, NO bathrooms filter.
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

EXPECTED_PROPERTY_TYPES = "Multi-Family|Apartment|Townhouse|Single Family|Condo"

FIX_DIR = Path("fixtures/live-samples")
LEDGER = FIX_DIR / "ledger.json"
DECISION = Path("rentcomp-pm/docs/gate-decision.md")


# ---------------------------------------------------------------------------
# Record sets. Padded seasonal windows for window 07-28..08-20 / 3 years:
#   2026: [2026-04-29, 2026-11-18] · 2025: [2025-04-29, 2025-11-18]
#   2024: [2024-04-29, 2024-11-18]
# ---------------------------------------------------------------------------

def _rec(i, listed):
    return {
        "id": f"listing-{i}",
        "formattedAddress": f"{3000 + i} S Wood St, Chicago, IL 60609",
        "status": "Active",
        "price": 1500 + 5 * i,
        "listedDate": f"{listed}T00:00:00.000Z",
    }


def go_mix():
    """37 distinct in-window (2026: 11 · 2025: 12 · 2024: 14) + 16 distinct
    out-of-window (2025-01-10 — between the 2024 and 2025 padded windows)
    = 53 raw distinct. Broad-mode verdict must be GO (37 >= 15) and the
    out-of-window records must not be an error."""
    records = []
    i = 0
    for _ in range(11):
        records.append(_rec(i, "2026-08-01")); i += 1
    for _ in range(12):
        records.append(_rec(i, "2025-08-01")); i += 1
    for _ in range(14):
        records.append(_rec(i, "2024-08-05")); i += 1
    for _ in range(16):
        records.append(_rec(i, "2025-01-10")); i += 1
    return records


def no_go_mix():
    """5 distinct in-window + 20 distinct out-of-window = 25 raw distinct.
    Raw total clears 15 but the verdict basis (in-window) does not —
    broad mode must say NO-GO."""
    records = []
    i = 0
    for _ in range(5):
        records.append(_rec(i, "2025-08-01")); i += 1
    for _ in range(20):
        records.append(_rec(i, "2025-01-10")); i += 1
    return records


def responder_for(records):
    def responder(request):
        return httpx.Response(200, json=records, headers={"X-Total-Count": str(len(records))})
    return responder


# ---------------------------------------------------------------------------
# Harness (same conventions as test_gate.py)
# ---------------------------------------------------------------------------

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


def run_gate(monkeypatch, argv=BROAD_ARGV):
    monkeypatch.setattr(sys, "argv", list(argv))
    gate.main()


def query_of(request):
    return dict(request.url.params)


# ===========================================================================
# R1 — --days-old override: 2 literal calls, date-independent signatures
# ===========================================================================

def test_days_old_override_sends_two_literal_calls(sandbox, monkeypatch):
    """--days-old 1:1095 => exactly one call per status (2 total), each
    carrying the literal daysOld string — no computed per-year windows on
    the wire. Ledger and X-Total-Count capture work on this path too."""
    captured = install_transport(monkeypatch, responder_for(go_mix()))
    run_gate(monkeypatch)

    assert len(captured) == 2
    pairs = {(query_of(r)["daysOld"], query_of(r)["status"]) for r in captured}
    assert pairs == {("1:1095", "Active"), ("1:1095", "Inactive")}

    ledger = json.loads(LEDGER.read_text())
    assert ledger["calls_this_month"] == 2
    assert all(e["total_count"] == 53 for e in ledger["history"])


def test_days_old_override_signatures_are_date_independent(sandbox, monkeypatch):
    """With a literal daysOld, no today-derived number reaches the wire
    params, so cache signatures must be identical across calendar days:
    re-running the same override on a LATER day is all cache hits — zero
    additional calls (the round-1 same-day constraint disappears)."""
    captured = install_transport(monkeypatch, responder_for(go_mix()))
    run_gate(monkeypatch)
    assert len(captured) == 2

    monkeypatch.setattr(gate, "date", FrozenDateLater)  # next day(s)
    run_gate(monkeypatch)
    assert len(captured) == 2, "override run on a later day must be all cache hits"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 2


# ===========================================================================
# R2 — --bathrooms optional: omitted => absent from the wire
# ===========================================================================

def test_bathrooms_omitted_is_absent_from_wire(sandbox, monkeypatch):
    """No --bathrooms => NO bathrooms key in the wire params at all (not an
    empty string, not '*:*') — server must not exclude records with missing
    bathroom data. Providing it still puts it on the wire verbatim."""
    captured = install_transport(monkeypatch, responder_for(go_mix()))
    run_gate(monkeypatch)
    for req in captured:
        assert "bathrooms" not in query_of(req), (
            f"bathrooms must be entirely absent when omitted, got: {query_of(req)}"
        )

    run_gate(monkeypatch, BROAD_ARGV + ["--bathrooms", "1:1.5"])
    provided = captured[2:]
    assert len(provided) == 2  # different signature => fresh calls
    for req in provided:
        assert query_of(req)["bathrooms"] == "1:1.5"


# ===========================================================================
# R3 — bedrooms range passthrough
# ===========================================================================

def test_bedrooms_range_passes_through_verbatim(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, responder_for(go_mix()))
    run_gate(monkeypatch)
    assert captured, "no requests captured"
    for req in captured:
        q = query_of(req)
        assert q["bedrooms"] == "3:4"
        assert q["propertyType"] == EXPECTED_PROPERTY_TYPES
        assert q["radius"] == "2.0"


# ===========================================================================
# R4 — local windowing verdict + dual-count decision record
# ===========================================================================

def test_broad_verdict_go_counts_in_window_distinct(sandbox, monkeypatch):
    """GO iff >=15 DISTINCT comps fall in ANY padded seasonal window.
    37 in-window / 53 raw => GO, normal exit; out-of-window records are not
    an error in broad mode. Decision record attributes both numbers and the
    per-window counts (2026: 11 · 2025: 12 · 2024: 14)."""
    install_transport(monkeypatch, responder_for(go_mix()))
    run_gate(monkeypatch)  # must complete without SystemExit

    text = DECISION.read_text()
    assert "**Verdict: GO**" in text
    assert re.search(r"(?i)in[- ]window[^\n]*\b37\b", text), (
        "decision record must attribute the in-window distinct count (37)"
    )
    assert re.search(r"(?i)raw[^\n]*\b53\b", text), (
        "decision record must attribute the raw distinct total (53) as diagnostic"
    )
    for year, count in ((2026, 11), (2025, 12), (2024, 14)):
        assert re.search(rf"{year}\D[^\n]*\b{count}\b", text), (
            f"decision record must report the per-window count for {year} ({count})"
        )


def test_broad_verdict_no_go_when_raw_total_clears_but_in_window_thin(sandbox, monkeypatch):
    """The verdict basis is the in-window count, NOT the raw pull size:
    5 in-window / 25 raw distinct => NO-GO + nonzero exit, even though the
    raw total clears the >=15 threshold."""
    install_transport(monkeypatch, responder_for(no_go_mix()))
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch)
    assert exc.value.code == 1
    assert "NO-GO" in DECISION.read_text()


# ===========================================================================
# R5 — D24 invariants on the new path: hard stop still fires in broad mode
# ===========================================================================

def test_broad_mode_hard_stop_preserves_fetched_response(sandbox, monkeypatch):
    captured = install_transport(monkeypatch, responder_for(go_mix()))
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, BROAD_ARGV + ["--max-calls", "1"])
    assert exc.value.code == 1
    assert len(captured) == 1, "call 2 must not be sent past --max-calls 1"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 1
    response_files = [p for p in FIX_DIR.glob("*.json") if p.name != "ledger.json"]
    assert len(response_files) == 1
