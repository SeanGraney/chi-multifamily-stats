#!/usr/bin/env python3
"""
RentComp go/no-go gate (T-S3 / F4-S7).

Standalone, pre-build verification script — answers the killer assumption
(does a real window pull return enough usable comps for the subject
address) against the LIVE RentCast API, within a hard-capped call budget.
Run this BEFORE committing to the build (spec §9). It has no dependency
on the not-yet-built `backend/` package; it's deliberately self-contained.

Usage (two-phase run — spend 1 call, verify offline, then resume):
    # Phase 1: spend exactly ONE live call, then hard-stop (exit 1, response kept)
    python scripts/gate.py --address "123 Main St, Chicago, IL 60614" \
        --bedrooms 2 --bathrooms 1 --radius 0.5 \
        --window-start 06-15 --window-end 06-30 --years-back 2 \
        --confirm --max-calls 1

    # Phase 2: OFFLINE window-validation verdict against the saved response —
    # zero API spend, no key needed (same args, swap --confirm for --verify-window)
    python scripts/gate.py --address "..." ... --verify-window

    # Phase 3: resume — the per-signature cache skips the already-paid call,
    # so only the remaining queries are spent
    python scripts/gate.py --address "..." ... --confirm

Broad-pull mode (round 2 — sparse markets): pass a literal daysOld range to
pull broad (2 calls: Active + Inactive) and apply the seasonal windows
LOCALLY at verdict time instead of on the wire:
    python scripts/gate.py --address "..." --bedrooms 3:4 --radius 2.0 \
        --property-types "Multi-Family,Apartment,Townhouse,Single Family,Condo" \
        --window-start 07-28 --window-end 08-20 --years-back 3 \
        --days-old 1:1095 --confirm
(--bathrooms omitted => no bathrooms filter on the wire. --verify-window does
not apply to broad pulls and is refused when combined with --days-old.)

Requires:
    RENTCAST_API_KEY in .env (mode 0600, never committed)
    pip install httpx

Hard constraint: refuses to make more than --max-calls (default 10) live
calls in a single run, and refuses to run at all without --confirm —
matching WORKFLOW.md §6 (no live call without a ledger entry, no live mode
without owner sign-off; you ARE that sign-off when you pass --confirm).
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

# The RentCast wire syntax (colon ranges, pipe-joined multi-values) is imported
# from the product package rather than kept as a second copy here — F0-S4, PM
# ruling 2026-07-27. Two copies of the syntax that spends money is a drift
# surface: T-S3 round 1 sent commas and bare values, got an empty result back,
# and an empty result is indistinguishable from a thin market. `backend/src` is
# put on the path explicitly so this script still runs from a bare clone with
# no `pip install -e .`, which is the whole reason it was self-contained.
_BACKEND_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(_BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(_BACKEND_SRC))

from rentcomp.client.query import rentcast_multi, rentcast_range  # noqa: E402

RENTCAST_BASE = "https://api.rentcast.io/v1"
FIXTURES_DIR = Path("fixtures/live-samples")
LEDGER_PATH = FIXTURES_DIR / "ledger.json"
DECISION_PATH = Path("rentcomp-pm/docs/gate-decision.md")
PAD_DAYS = 90


def load_dotenv():
    # minimal .env loader — avoids adding python-dotenv as a dependency for one script
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def load_ledger() -> dict:
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {"calls_this_month": 0, "history": []}


def save_ledger(ledger: dict) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2))
    tmp.replace(LEDGER_PATH)  # atomic


def month_day_in_year(month_day: str, year: int) -> date:
    m, d = (int(x) for x in month_day.split("-"))
    return date(year, m, d)


def compute_window(window_start: str, window_end: str, years_back: int) -> list[dict]:
    """Spec §3.2 — year-agnostic window queries, PAD=90 both sides."""
    today = date.today()
    windows = []
    for y in range(years_back):
        year = today.year - y
        w_start = month_day_in_year(window_start, year)
        w_end = month_day_in_year(window_end, year)
        days_old_min = max(1, (today - w_end).days - PAD_DAYS)
        days_old_max = (today - w_start).days + PAD_DAYS
        windows.append({"year": year, "daysOldMin": days_old_min, "daysOldMax": days_old_max})
    return windows


# rentcast_range / rentcast_multi are imported at the top of this module from
# rentcomp.client.query — same functions, same behaviour, same module-level
# names as before. Their docstrings (and the reasons behind the syntax) live
# there now.


def parse_listed_date(value):
    """Parse a RentCast listedDate — ISO datetime ('2025-08-01T00:00:00.000Z')
    or plain date ('2025-08-01') — to a date. Returns None if missing or
    unparseable (a record we cannot verify)."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def validate_window(records, w_start: date, w_end: date, pad_days: int = PAD_DAYS) -> dict:
    """Offline check that returned records actually fall inside the requested
    padded window: listedDate in [w_start - pad, w_end + pad], boundaries
    INCLUSIVE (the daysOld range the gate requests is inclusive at both ends).

    Records with a missing/unparseable listedDate cannot be verified: they go
    to out_of_window and force ok=False. ok is True iff every record verifiably
    falls inside the padded window — the go-signal for spending the remaining
    calls after phase 1.
    """
    lo = w_start - timedelta(days=pad_days)
    hi = w_end + timedelta(days=pad_days)
    in_window, out_of_window = [], []
    for record in records:
        listed = parse_listed_date(record.get("listedDate"))
        if listed is not None and lo <= listed <= hi:
            in_window.append(record)
        else:
            out_of_window.append(record)
    return {"in_window": in_window, "out_of_window": out_of_window, "ok": not out_of_window}


def signature(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def raw_response_path(sig: str) -> Path:
    return FIXTURES_DIR / f"{sig}.json"


def build_params(args, days_old: str, status: str) -> dict:
    """One (daysOld-range x status) query. Single source of the wire params for
    both the live run and the offline --verify-window pass — the two must
    produce identical signatures or the cache/resume contract breaks.

    `days_old` is already in RentCast colon syntax (computed per-year window,
    or the operator's literal --days-old override in broad mode).

    --bathrooms omitted => NO bathrooms key at all (not "", not "*:*") — the
    server must not exclude records with missing bathroom data. Provided =>
    verbatim passthrough (bare value = exact match, colon syntax = range).
    Bedrooms likewise passes through verbatim ("3:4" stays "3:4")."""
    params = {
        "address": args.address,
        "radius": args.radius,
        "bedrooms": args.bedrooms,
        # RentCast multi-value syntax is pipe-separated (CLI stays comma-separated)
        "propertyType": rentcast_multi(v.strip() for v in args.property_types.split(",")),
        "status": status,
        # RentCast numeric ranges are colon syntax on ONE param — daysOldMin/Max don't exist
        "daysOld": days_old,
        "limit": 500,
        # F4-S7 pagination question: ask for X-Total-Count on every response
        "includeTotalCount": "true",
    }
    if args.bathrooms is not None:
        params["bathrooms"] = args.bathrooms
    return params


def extract_records(data) -> list:
    return data if isinstance(data, list) else data.get("listings", [])


def call_rentcast(client: httpx.Client, params: dict, ledger: dict, max_calls: int) -> dict:
    sig = signature(params)
    cached = raw_response_path(sig)
    if cached.exists():
        print(f"  [cache hit] {sig} — no call spent")
        return json.loads(cached.read_text())

    if ledger["calls_this_month"] >= max_calls:
        print(f"FATAL: hit --max-calls={max_calls} limit. Stopping — resume later, already-fetched "
              f"responses are preserved and won't be re-paid for.")
        sys.exit(1)

    print(f"  [LIVE CALL {ledger['calls_this_month'] + 1}/{max_calls}] {params}")
    resp = client.get("/listings/rental/long-term", params=params)
    ledger["calls_this_month"] += 1
    # F4-S7 pagination evidence: persist X-Total-Count per call in the ledger
    # history (sidecar metadata — the raw response body files stay raw, D24)
    total_count = resp.headers.get("X-Total-Count")
    if total_count is not None:
        try:
            total_count = int(total_count)
        except ValueError:
            pass  # keep the raw header string if it isn't an int
    ledger["history"].append(
        {"sig": sig, "params": params, "status": resp.status_code, "total_count": total_count}
    )
    save_ledger(ledger)  # ledger increments at send time, not batch completion — D24

    resp.raise_for_status()
    data = resp.json()

    # raw bytes written before any parsing/validation — D24
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = raw_response_path(sig).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(raw_response_path(sig))  # atomic write .tmp -> rename

    return data


def run_verify_window(args) -> None:
    """Phase 2 of the two-phase run: OFFLINE validation of already-fetched raw
    responses against the padded window. Zero network, zero spend, no API key
    needed. Exit 0 = safe to resume live spend; exit 1 = stop and re-check the
    query design (or nothing fetched yet)."""
    windows = compute_window(args.window_start, args.window_end, args.years_back)
    print(f"Offline window validation for: {args.address} (zero API spend)")
    print("NOTE: daysOld ranges (and thus cache signatures) depend on today's date — "
          "run all phases on the same calendar day, or already-paid responses "
          "won't match and would be re-paid.\n")
    found = 0
    missing = 0
    all_ok = True
    for w in windows:
        w_start = month_day_in_year(args.window_start, w["year"])
        w_end = month_day_in_year(args.window_end, w["year"])
        for status in ("Active", "Inactive"):
            params = build_params(args, rentcast_range(w["daysOldMin"], w["daysOldMax"]), status)
            sig = signature(params)
            cached = raw_response_path(sig)
            if not cached.exists():
                missing += 1
                print(f"  {w['year']} {status}: not fetched yet ({sig}) — skipped")
                continue
            found += 1
            records = extract_records(json.loads(cached.read_text()))
            result = validate_window(records, w_start, w_end)
            all_ok = all_ok and result["ok"]
            print(f"  {w['year']} {status}: {len(result['in_window'])} in-window, "
                  f"{len(result['out_of_window'])} out-of-window -> "
                  f"{'OK' if result['ok'] else 'FAIL'}")
            for r in result["out_of_window"][:5]:
                print(f"      out: id={r.get('id')} listedDate={r.get('listedDate')!r}")

    print(f"\nChecked {found} cached response(s); {missing} planned quer"
          f"{'y' if missing == 1 else 'ies'} still unfetched.")
    if found == 0:
        print("VERDICT: NOTHING TO VALIDATE — no cached responses match these params. "
              "Run phase 1 first (--confirm --max-calls 1).")
        sys.exit(1)
    if not all_ok:
        print("VERDICT: WINDOW VALIDATION FAILED — records fall outside the padded window "
              "or lack a verifiable listedDate. Do NOT spend further calls; "
              "re-check the query design.")
        sys.exit(1)
    print("VERDICT: WINDOW VALIDATION OK — safe to resume the live run "
          f"({missing} quer{'y' if missing == 1 else 'ies'} remaining; "
          f"already-paid responses will be cache hits).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--bedrooms", required=True,
                         help="Verbatim RentCast value — bare for exact match, colon syntax "
                              "for a range (e.g. '3:4').")
    parser.add_argument("--bathrooms", default=None,
                         help="Optional. Omitted => NO bathrooms filter on the wire at all "
                              "(records with missing bathroom data are not excluded server-side). "
                              "Provided => verbatim passthrough.")
    parser.add_argument("--radius", type=float, default=0.5)
    parser.add_argument("--property-types", default="Multi-Family,Apartment,Townhouse")
    parser.add_argument("--window-start", required=True, help="MM-DD")
    parser.add_argument("--window-end", required=True, help="MM-DD")
    parser.add_argument("--years-back", type=int, default=2)
    parser.add_argument("--days-old", default=None,
                         help="BROAD-PULL override: a literal RentCast daysOld range string "
                              "(e.g. '1:1095'). One call per status (2 total) with this exact "
                              "range — no computed per-year windows on the wire; the seasonal "
                              "windows are applied LOCALLY at verdict time instead. Cache "
                              "signatures become date-independent on this path.")
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--confirm", action="store_true",
                         help="Required. You are authorizing live API spend against the 50/month cap.")
    parser.add_argument("--verify-window", action="store_true",
                         help="OFFLINE mode: validate already-fetched raw responses against the "
                              "padded window and exit. Spends nothing, needs no key or --confirm; "
                              "takes precedence over --confirm if both are passed.")
    args = parser.parse_args()

    if args.verify_window and args.days_old:
        # PM ruling (T-S3 round 2): strict window verification is meaningless for
        # a broad pull — it would flag every record outside the seasonal windows,
        # but a broad pull DELIBERATELY fetches those and windows locally at
        # verdict time instead.
        print("FATAL: --verify-window cannot be combined with --days-old. Strict window "
              "verification only applies to computed per-year window pulls; a broad pull "
              "intentionally returns records outside the seasonal windows and is windowed "
              "locally when the verdict is computed. Drop one of the two flags.")
        sys.exit(2)

    if args.days_old is not None and ":" not in args.days_old:
        # Guard against the round-1 defect class at the operator interface:
        # RentCast treats a bare daysOld value as the range MAXIMUM, not an
        # exact match — a literal override must always be explicit colon syntax.
        print(f"FATAL: --days-old must be a colon range string (e.g. '1:1095'), "
              f"got {args.days_old!r}. RentCast treats a bare daysOld value as the "
              f"range MAXIMUM, so a bare value is ambiguous — write the range explicitly.")
        sys.exit(2)

    if args.verify_window:
        run_verify_window(args)
        return

    if not args.confirm:
        print("Refusing to run without --confirm — this spends real API calls against the "
              "50/month cap (WORKFLOW.md §6). Re-run with --confirm once you're ready.")
        sys.exit(1)

    load_dotenv()
    api_key = os.environ.get("RENTCAST_API_KEY")
    if not api_key:
        print("FATAL: RENTCAST_API_KEY not set. Put it in .env (mode 0600), never commit it.")
        sys.exit(1)

    ledger = load_ledger()
    windows = compute_window(args.window_start, args.window_end, args.years_back)

    # The wire plan: broad mode is ONE literal daysOld range (2 calls total);
    # windowed mode is one computed range per year back (2 * years_back calls).
    if args.days_old:
        wire_plan = [("broad", args.days_old)]
    else:
        wire_plan = [(str(w["year"]), rentcast_range(w["daysOldMin"], w["daysOldMax"]))
                     for w in windows]

    print(f"Gate run for: {args.address}")
    print(f"Windows: {windows}" + (" (applied LOCALLY at verdict time — broad pull)"
                                   if args.days_old else ""))
    print(f"Ledger so far this month: {ledger['calls_this_month']} calls")
    print(f"This run will spend at most {args.max_calls} more (hard stop).")
    if args.days_old:
        print(f"BROAD PULL: literal daysOld={args.days_old} — cache signatures are "
              f"date-independent; resuming on a later day is safe.\n")
    else:
        print("NOTE: daysOld ranges (and thus cache signatures) depend on today's date — "
              "if resuming a prior run, resume on the SAME calendar day it started, "
              "or already-paid responses won't be cache hits.\n")

    all_records = []
    with httpx.Client(base_url=RENTCAST_BASE, headers={"X-Api-Key": api_key}, timeout=30) as client:
        for label, days_old in wire_plan:
            for status in ("Active", "Inactive"):
                params = build_params(args, days_old, status)
                data = call_rentcast(client, params, ledger, args.max_calls)
                records = extract_records(data)
                all_records.extend(records)
                print(f"  {label} {status}: {len(records)} records")

    # crude usability count — real stitching/dedupe happens in F4, this is a sanity signal only
    def distinct_keys(records):
        return {r.get("formattedAddress") or r.get("id") for r in records}

    unique_addrs = distinct_keys(all_records)
    print(f"\nTotal raw records: {len(all_records)}")
    print(f"Distinct addresses/ids: {len(unique_addrs)}")

    if args.days_old:
        # Broad mode: the seasonal windows the wire no longer enforces are
        # applied LOCALLY here — the verdict basis is distinct comps whose
        # listedDate lands in ANY padded per-year window (validate_window
        # semantics: ±PAD_DAYS, boundaries inclusive). Records with missing/
        # unparseable listedDate can't be verified in-window: excluded from
        # the verdict count, NOT an error (they stay in the raw fixtures).
        per_window = []
        in_window_keys = set()
        for w in windows:
            w_start = month_day_in_year(args.window_start, w["year"])
            w_end = month_day_in_year(args.window_end, w["year"])
            result = validate_window(all_records, w_start, w_end)
            keys = distinct_keys(result["in_window"])
            per_window.append((w["year"], len(keys)))
            in_window_keys |= keys
        in_window_count = len(in_window_keys)
        per_window_lines = "\n".join(
            f"- {year} window: {count} in-window distinct comps"
            for year, count in per_window
        )
        print(f"In-window distinct comps (verdict basis): {in_window_count}")

        verdict = ("GO" if in_window_count >= 15
                   else "NO-GO — insufficient in-window comp coverage")
        decision = f"""# T-S3 Go/No-Go Gate — Decision Record (broad pull)

**Run date:** {date.today().isoformat()}
**Subject:** {args.address}
**Mode:** broad pull — literal daysOld={args.days_old}; seasonal windows applied locally
**Window (year-agnostic):** {args.window_start}..{args.window_end}, {args.years_back} years back, padded ±{PAD_DAYS}d inclusive
**Calls on ledger this month:** {ledger['calls_this_month']}
**Raw records pulled:** {len(all_records)}
**Raw distinct addresses/ids (diagnostic only):** {len(unique_addrs)}
**In-window distinct comps (verdict basis):** {in_window_count}

Per-window in-window distinct counts:
{per_window_lines}

**Verdict: {verdict}**

(Threshold: >=15 distinct comps whose listedDate lands inside ANY padded
seasonal window — the same ±{PAD_DAYS}-day inclusive windows a windowed pull
would have requested on the wire, applied locally to the broad pull instead.
Records with a missing or unparseable listedDate cannot be verified in-window:
they are excluded from the verdict count but are NOT an error — they remain in
the raw fixture data for the F4 pipeline to handle. Distinctness rule:
formattedAddress, falling back to id — same as the windowed-mode raw count.)

Raw responses saved to `fixtures/live-samples/` — these seed the entire build's
fixture-mode development going forward (WORKFLOW.md §6).
"""
        DECISION_PATH.parent.mkdir(parents=True, exist_ok=True)
        DECISION_PATH.write_text(decision)
        print(f"\nDecision record written to {DECISION_PATH}")
        print(f"\n{verdict}")
        if verdict.startswith("NO-GO"):
            sys.exit(1)
        return

    verdict = "GO" if len(unique_addrs) >= 15 else "NO-GO — insufficient comp coverage"
    decision = f"""# T-S3 Go/No-Go Gate — Decision Record

**Run date:** {date.today().isoformat()}
**Subject:** {args.address}
**Windows:** {windows}
**Calls spent this run:** {ledger['calls_this_month']}
**Raw records pulled:** {len(all_records)}
**Distinct addresses/ids:** {len(unique_addrs)}

**Verdict: {verdict}**

(Threshold: >=15 distinct comps pre-stitching, matching spec §8's leading indicator
of >=15 usable comps per pull. This is a raw-count sanity check, not the final
usable-comp count — real dedupe/stitch/window/cohort filtering happens in F4 and
will reduce this number. If this raw count is already under 15, the pipeline's
filtered count will be lower still, which is why this is a gate, not a formality.)

Raw responses saved to `fixtures/live-samples/` — these seed the entire build's
fixture-mode development going forward (WORKFLOW.md §6).
"""
    DECISION_PATH.parent.mkdir(parents=True, exist_ok=True)
    DECISION_PATH.write_text(decision)
    print(f"\nDecision record written to {DECISION_PATH}")
    print(f"\n{verdict}")

    if verdict.startswith("NO-GO"):
        sys.exit(1)


if __name__ == "__main__":
    main()
