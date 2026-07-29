"""Seed a `RENTCOMP_HOME` with two real, fixture-mode pulls for the F1-S2 E2E spec.

Run through the repo-root `.venv`'s python by `f1-s2-recents.spec.ts`'s
`beforeAll`. Prints one JSON object on stdout: `{"refs": [older, newer]}`.

Zero network, by construction and by policy (D17, WORKFLOW.md §6):
`RENTCOMP_LIVE` is never set, `RENTCAST_API_KEY` is removed, and every
response is served from the temp fixtures directory this script writes itself.
A pull here cannot reach RentCast and cannot move the ledger.

Why python and not TypeScript: the fixture filenames are content-signatures of
the planned query parameters (`client/rentcast.py::fixture_signature`), so
seeding a real cache entry means asking the real planner what it would fetch.
Reimplementing that in the spec would be a second implementation of the thing
under test.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta

os.environ.pop("RENTCOMP_LIVE", None)
os.environ.pop("RENTCAST_API_KEY", None)

from rentcomp.client.planner import fetchable_queries, plan_pull_queries  # noqa: E402
from rentcomp.client.pull import run_pull  # noqa: E402
from rentcomp.client.rentcast import fixture_signature  # noqa: E402

TODAY = date.today()

BASE = {
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}

ADDRESSES = [
    "3651 S Wood St, Chicago, IL 60609",
    "1200 W 18th St, Chicago, IL 60608",
]


def record(n: int) -> dict:
    return {
        "id": f"f1s2-e2e-{n}",
        "formattedAddress": f"{800 + n} N Verify Blvd, Chicago, IL 60609",
        "addressLine1": f"{800 + n} N Verify Blvd",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + 50 * n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + 10 * n,
        "listedDate": (TODAY - timedelta(days=150 + n)).isoformat() + "T00:00:00.000Z",
        "status": "Active",
    }


def main() -> int:
    fixtures = os.environ["RENTCOMP_FIXTURES_DIR"]
    os.makedirs(fixtures, exist_ok=True)

    refs = []
    for index, address in enumerate(ADDRESSES):
        args = {**BASE, "address": address}
        for offset, query in enumerate(fetchable_queries(plan_pull_queries(TODAY, **args))):
            path = os.path.join(fixtures, f"{fixture_signature(query.params)}.json")
            with open(path, "wb") as handle:
                handle.write(json.dumps([record(index * 10 + offset)]).encode("utf-8"))
        outcome = run_pull(**args, today=TODAY)
        assert outcome.calls_spent == 0, "fixture mode must never spend a call"
        refs.append(outcome.pull_ref)

    print(json.dumps({"refs": refs}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
