"""T-S3 / F4-S7 — QA suite for scripts/gate.py, written BEFORE the defect fix.

ZERO network: every httpx.Client the gate constructs is routed through an
httpx.MockTransport; every test chdirs into a pytest tmp dir so the repo's
real fixtures/, .env, and ledger are never touched.

Two groups of tests:

1. DEFECT / OWNER-REQUIREMENT tests — expected RED against the current
   gate.py (they define the fix):
   - RentCast has no `daysOldMin`/`daysOldMax` params. Numeric ranges are
     colon syntax on the single param: `daysOld=min:max`, open-ended with
     `*`; a BARE single value for daysOld means "range maximum", so one
     must never be emitted when a range is intended.
   - Multi-value params are pipe-separated (`Multi-Family|Apartment|Townhouse`),
     not comma-separated.
   - Owner req A: the range/multi-value syntax lives in ONE reusable,
     tested function pair. Expected interface (F0-S4 and F2-S2 inherit it;
     bedrooms/bathrooms use identical syntax):
         gate.rentcast_range(min_value, max_value) -> str
             (5, 10) -> "5:10" · (2000, None) -> "2000:*" ·
             (None, 10) -> "*:10" · (5, 5) -> "5:5" (never bare "5")
         gate.rentcast_multi(values: list[str]) -> str   # pipe-joined
   - Owner req B: two-phase run support — offline validation of returned
     records against the requested padded window. Expected interface:
         gate.validate_window(records, w_start: date, w_end: date,
                              pad_days=gate.PAD_DAYS) -> dict
         with keys: "in_window" (list of records), "out_of_window"
         (list of records), "ok" (bool — True iff every record has a
         parseable listedDate inside [w_start - pad, w_end + pad],
         boundaries inclusive).
   The developer may propose different names/shapes via the PM; these
   tests then get updated by QA — not silently by the developer.

2. INVARIANT-PINNING tests — expected GREEN against the current gate.py
   (D24 / WORKFLOW §6 behaviors that must survive the fix): window math
   under a frozen "today", ledger-at-send-time, cache-hit-spends-nothing,
   hard stop at --max-calls, raw-response persistence (atomic, no .tmp
   leftovers), decision record, --confirm and API-key refusals.

Wire-level tests drive gate.main() with the OWNER'S REAL gate parameters
(QUEUE.md log 2026-07-26): 3651 S Wood St, 4bd/1.5ba, 1.0mi radius,
window 07-28..08-20, 3 years back — 6 planned calls.
"""

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import gate

# ---------------------------------------------------------------------------
# Frozen clock + owner's real gate parameters
# ---------------------------------------------------------------------------

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
    "--confirm",
]

# Hand-checked expectations for FROZEN_TODAY = 2026-07-26, PAD = 90:
#   2026: end 2026-08-20 is in the future -> min clamped to 1;
#         max = (today - 2026-07-28).days + 90 = -2 + 90 = 88
#   2025: min = (today - 2025-08-20).days - 90 = 340 - 90 = 250
#         max = (today - 2025-07-28).days + 90 = 363 + 90 = 453
#   2024: min = 705 - 90 = 615 · max = 728 + 90 = 818
EXPECTED_DAYS_OLD = {
    2026: (1, 88),
    2025: (250, 453),
    2024: (615, 818),
}
EXPECTED_DAYS_OLD_STRINGS = {"1:88", "250:453", "615:818"}

FIX_DIR = Path("fixtures/live-samples")
LEDGER = FIX_DIR / "ledger.json"
DECISION = Path("rentcomp-pm/docs/gate-decision.md")


def make_records(n=20):
    """n distinct-address fake listing records (>=15 distinct => GO verdict)."""
    return [
        {
            "id": f"listing-{i}",
            "formattedAddress": f"{3600 + i} S Wood St, Chicago, IL 60609",
            "status": "Active",
            "price": 1800 + 10 * i,
            "listedDate": "2025-08-01T00:00:00.000Z",
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Harness fixtures — isolated cwd, fake key, frozen clock, mocked transport
# ---------------------------------------------------------------------------

@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolate every run: tmp cwd (repo fixtures/.env untouched), fake API
    key, frozen today. Returns tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RENTCAST_API_KEY", "test-key-not-a-real-secret")
    monkeypatch.setattr(gate, "date", FrozenDate)
    return tmp_path


def install_transport(monkeypatch, responder):
    """Route any httpx.Client gate.py builds through a MockTransport.
    Returns the list of captured outbound requests (zero real network)."""
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


def run_gate(monkeypatch, argv=SUBJECT_ARGV):
    monkeypatch.setattr(sys, "argv", list(argv))
    gate.main()


def _required(name):
    fn = getattr(gate, name, None)
    assert fn is not None, (
        f"gate.py must expose a function named `{name}` "
        f"(T-S3 owner requirement — see this file's docstring for the "
        f"expected interface)"
    )
    return fn


def query_of(request):
    return dict(request.url.params)


# ===========================================================================
# GROUP 1 — DEFECT / OWNER-REQUIREMENT TESTS (expected RED pre-fix)
# ===========================================================================

# --- Defect 1: daysOld colon-range syntax on the wire ----------------------

def test_sent_params_use_daysold_colon_syntax(sandbox, monkeypatch):
    """RentCast has no daysOldMin/daysOldMax params. The gate must send the
    single `daysOld` param in colon range syntax, one (window x status) query
    per call — 6 calls for the owner's 3-year run."""
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    run_gate(monkeypatch)

    for req in captured:
        q = query_of(req)
        assert "daysOldMin" not in q and "daysOldMax" not in q, (
            f"nonexistent RentCast param sent: {q}"
        )
        assert "daysOld" in q, f"missing daysOld range param: {q}"

    pairs = {(query_of(r)["daysOld"], query_of(r)["status"]) for r in captured}
    assert pairs == {
        (d, s)
        for d in EXPECTED_DAYS_OLD_STRINGS
        for s in ("Active", "Inactive")
    }
    assert len(captured) == 6  # 3 windows x 2 statuses — matches the ledger plan


# --- Defect 2: propertyType pipe-separated multi-value ---------------------

def test_sent_params_use_pipe_separated_property_types(sandbox, monkeypatch):
    """RentCast multi-value syntax is pipe-separated; comma-joined values do
    not match any property type."""
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    run_gate(monkeypatch)

    assert captured, "no requests captured"
    for req in captured:
        pt = query_of(req).get("propertyType")
        assert pt == "Multi-Family|Apartment|Townhouse", (
            f"propertyType must be pipe-separated, got: {pt!r}"
        )


# --- Owner req A: one reusable, tested range/multi-value builder -----------

def test_rentcast_range_closed_range(sandbox):
    rr = _required("rentcast_range")
    assert rr(5, 10) == "5:10"
    assert rr(250, 453) == "250:453"


def test_rentcast_range_open_ended(sandbox):
    rr = _required("rentcast_range")
    assert rr(2000, None) == "2000:*"
    assert rr(None, 10) == "*:10"


def test_rentcast_range_never_emits_bare_single_value(sandbox):
    """RentCast treats a bare single daysOld value as the range MAXIMUM, not
    an exact match — a degenerate range must still be colon syntax."""
    rr = _required("rentcast_range")
    out = rr(5, 5)
    assert out == "5:5"
    assert ":" in str(out)


def test_rentcast_multi_pipe_join(sandbox):
    rm = _required("rentcast_multi")
    assert rm(["Multi-Family", "Apartment", "Townhouse"]) == "Multi-Family|Apartment|Townhouse"
    assert rm(["Apartment"]) == "Apartment"


# --- Owner req B: offline window-membership validation (two-phase run) -----

W_START = date(2025, 7, 28)
W_END = date(2025, 8, 20)


def _rec(rec_id, listed):
    r = {"id": rec_id, "formattedAddress": f"{rec_id} Test St, Chicago, IL"}
    if listed is not None:
        r["listedDate"] = listed
    return r


def test_validate_window_classifies_in_vs_out_with_pad(sandbox):
    """Records are in-window iff listedDate falls in
    [w_start - PAD, w_end + PAD], boundaries INCLUSIVE (the daysOld range
    the gate requests is inclusive at both ends)."""
    vw = _required("validate_window")
    pad_lo = W_START - timedelta(days=gate.PAD_DAYS)
    pad_hi = W_END + timedelta(days=gate.PAD_DAYS)
    records = [
        _rec("mid", "2025-08-01T00:00:00.000Z"),
        _rec("pad-lo-edge", pad_lo.isoformat() + "T00:00:00.000Z"),
        _rec("pad-hi-edge", pad_hi.isoformat() + "T00:00:00.000Z"),
        _rec("plain-date-format", "2025-08-05"),
        _rec("below-pad", (pad_lo - timedelta(days=1)).isoformat() + "T00:00:00.000Z"),
        _rec("above-pad", (pad_hi + timedelta(days=1)).isoformat() + "T00:00:00.000Z"),
    ]
    result = vw(records, W_START, W_END)
    in_ids = {r["id"] for r in result["in_window"]}
    out_ids = {r["id"] for r in result["out_of_window"]}
    assert in_ids == {"mid", "pad-lo-edge", "pad-hi-edge", "plain-date-format"}
    assert out_ids == {"below-pad", "above-pad"}


def test_validate_window_verdict_ok_iff_all_in_window(sandbox):
    """The operator verdict after live call #1: ok=True only when every
    record verifiably falls inside the padded window — that is the go-signal
    for spending calls 2..6."""
    vw = _required("validate_window")
    good = [_rec("a", "2025-08-01"), _rec("b", "2025-07-30")]
    assert vw(good, W_START, W_END)["ok"] is True

    mixed = good + [_rec("stray", "2024-01-01")]
    assert vw(mixed, W_START, W_END)["ok"] is False


def test_validate_window_missing_listed_date_never_counts_in_window(sandbox):
    """A record whose listedDate is absent or null cannot be verified —
    it must not be counted as in-window, and the verdict must not be ok."""
    vw = _required("validate_window")
    records = [_rec("ok", "2025-08-01"), _rec("absent", None), {"id": "null-date", "listedDate": None}]
    result = vw(records, W_START, W_END)
    in_ids = {r["id"] for r in result["in_window"]}
    assert "absent" not in in_ids and "null-date" not in in_ids
    assert result["ok"] is False


# ===========================================================================
# GROUP 2 — INVARIANT-PINNING TESTS (expected GREEN pre-fix; must stay green)
# ===========================================================================

def test_compute_window_frozen_today(sandbox):
    """Year-agnostic MM-DD windows, PAD=90 both sides, one window per year
    back, min clamped to >=1 (2026's window end is still in the future).

    NOTE: this pins compute_window's current return shape
    ({year, daysOldMin, daysOldMax}) as the INTERNAL representation — the
    colon syntax belongs at param-build time, not here. If the developer
    restructures this shape, flag via the PM and QA updates this test.
    """
    windows = gate.compute_window("07-28", "08-20", 3)
    assert len(windows) == 3
    by_year = {w["year"]: (w["daysOldMin"], w["daysOldMax"]) for w in windows}
    assert by_year == EXPECTED_DAYS_OLD
    assert all(w["daysOldMin"] >= 1 for w in windows)


def test_ledger_saved_at_send_time_not_batch_completion(sandbox, monkeypatch):
    """A failed (HTTP 500) first call must already be on the persisted
    ledger — the quota was spent at send time, before parse/raise (D24)."""
    install_transport(monkeypatch, lambda req: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        run_gate(monkeypatch)

    ledger = json.loads(LEDGER.read_text())
    assert ledger["calls_this_month"] == 1
    assert ledger["history"][0]["status"] == 500


def test_cached_response_spends_no_call(sandbox, monkeypatch):
    """Re-running an identical, complete pull hits the on-disk raw responses
    and spends ZERO additional calls (D24 — never pay twice)."""
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    run_gate(monkeypatch)
    assert len(captured) == 6
    run_gate(monkeypatch)  # identical params — all cache hits
    assert len(captured) == 6, "second identical run must spend no calls"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 6


def test_hard_stop_at_max_calls_preserves_fetched_responses(sandbox, monkeypatch):
    """At --max-calls N the gate exits WITHOUT sending call N+1; the N
    responses already fetched stay on disk and on the ledger."""
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, SUBJECT_ARGV + ["--max-calls", "2"])
    assert exc.value.code == 1
    assert len(captured) == 2, "call N+1 must never be sent"
    assert json.loads(LEDGER.read_text())["calls_this_month"] == 2
    response_files = [p for p in FIX_DIR.glob("*.json") if p.name != "ledger.json"]
    assert len(response_files) == 2, "already-fetched responses must be preserved"


def test_raw_responses_persisted_per_call_atomically(sandbox, monkeypatch):
    """Every paid-for response is written to fixtures/live-samples/ (one file
    per call, atomic tmp->rename, no .tmp leftovers) and holds the response
    content — this is the canonical fixture set the whole build inherits."""
    install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    run_gate(monkeypatch)

    assert list(FIX_DIR.glob("*.tmp")) == [], "no .tmp leftovers allowed"
    response_files = [p for p in FIX_DIR.glob("*.json") if p.name != "ledger.json"]
    assert len(response_files) == 6
    for p in response_files:
        assert json.loads(p.read_text()) == make_records()


def test_decision_record_go(sandbox, monkeypatch):
    """>=15 distinct addresses => GO decision record written into the repo."""
    install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records(20))
    )
    run_gate(monkeypatch)
    assert DECISION.exists()
    assert "**Verdict: GO**" in DECISION.read_text()


def test_decision_record_no_go_exits_nonzero(sandbox, monkeypatch):
    """<15 distinct addresses => NO-GO decision record + nonzero exit
    (build halts; redesign, not sprint 1)."""
    install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records(5))
    )
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch)
    assert exc.value.code == 1
    assert "NO-GO" in DECISION.read_text()


def test_refuses_without_confirm(sandbox, monkeypatch):
    """No --confirm => no run, no network, no ledger (WORKFLOW §6 —
    --confirm IS the owner's live-spend sign-off)."""
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    argv = [a for a in SUBJECT_ARGV if a != "--confirm"]
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch, argv)
    assert exc.value.code == 1
    assert captured == []
    assert not LEDGER.exists()


def test_refuses_without_api_key(sandbox, monkeypatch):
    """--confirm but no RENTCAST_API_KEY (and no .env in cwd) => refuse
    loudly, zero network."""
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    captured = install_transport(
        monkeypatch, lambda req: httpx.Response(200, json=make_records())
    )
    with pytest.raises(SystemExit) as exc:
        run_gate(monkeypatch)
    assert exc.value.code == 1
    assert captured == []
