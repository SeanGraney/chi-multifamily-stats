#!/usr/bin/env python3
"""
RentComp go/no-go gate (T-S3 / F4-S7).

Standalone, pre-build verification script — answers the killer assumption
(does a real window pull return enough usable comps for the subject
address) against the LIVE RentCast API, within a hard-capped call budget.
Run this BEFORE committing to the build (spec §9). It has no dependency
on the not-yet-built `backend/` package; it's deliberately self-contained.

Usage:
    python scripts/gate.py --address "123 Main St, Chicago, IL 60614" \
        --bedrooms 2 --bathrooms 1 --radius 0.5 \
        --window-start 06-15 --window-end 06-30 --years-back 2 --confirm

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
from datetime import date
from pathlib import Path

import httpx

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


def signature(params: dict) -> str:
    canonical = json.dumps(params, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def raw_response_path(sig: str) -> Path:
    return FIXTURES_DIR / f"{sig}.json"


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
    ledger["history"].append({"sig": sig, "params": params, "status": resp.status_code})
    save_ledger(ledger)  # ledger increments at send time, not batch completion — D24

    resp.raise_for_status()
    data = resp.json()

    # raw bytes written before any parsing/validation — D24
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    tmp = raw_response_path(sig).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(raw_response_path(sig))  # atomic write .tmp -> rename

    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--address", required=True)
    parser.add_argument("--bedrooms", required=True)
    parser.add_argument("--bathrooms", required=True)
    parser.add_argument("--radius", type=float, default=0.5)
    parser.add_argument("--property-types", default="Multi-Family,Apartment,Townhouse")
    parser.add_argument("--window-start", required=True, help="MM-DD")
    parser.add_argument("--window-end", required=True, help="MM-DD")
    parser.add_argument("--years-back", type=int, default=2)
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--confirm", action="store_true",
                         help="Required. You are authorizing live API spend against the 50/month cap.")
    args = parser.parse_args()

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

    print(f"Gate run for: {args.address}")
    print(f"Windows: {windows}")
    print(f"Ledger so far this month: {ledger['calls_this_month']} calls")
    print(f"This run will spend at most {args.max_calls} more (hard stop).\n")

    all_records = []
    with httpx.Client(base_url=RENTCAST_BASE, headers={"X-Api-Key": api_key}, timeout=30) as client:
        for w in windows:
            for status in ("Active", "Inactive"):
                params = {
                    "address": args.address,
                    "radius": args.radius,
                    "bedrooms": args.bedrooms,
                    "bathrooms": args.bathrooms,
                    "propertyType": args.property_types,
                    "status": status,
                    "daysOldMin": w["daysOldMin"],
                    "daysOldMax": w["daysOldMax"],
                    "limit": 500,
                }
                data = call_rentcast(client, params, ledger, args.max_calls)
                records = data if isinstance(data, list) else data.get("listings", [])
                all_records.extend(records)
                print(f"  {w['year']} {status}: {len(records)} records")

    # crude usability count — real stitching/dedupe happens in F4, this is a sanity signal only
    unique_addrs = {r.get("formattedAddress") or r.get("id") for r in all_records}
    print(f"\nTotal raw records: {len(all_records)}")
    print(f"Distinct addresses/ids: {len(unique_addrs)}")

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
