"""F3-S4 [BE] Durable response cache + resumable pulls — shared harness.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Everything in this directory is Layer 1: `run_pull` /
`pull_status` / `read_manifest` / `load_shaped_pull` are importable functions
callable with plain values, and none of what this story guarantees is
observable from a browser — "how many wire requests were issued", "which bytes
survived a crash", "what date the manifest says the evidence was observed" are
invisible above this layer.

HOW "ZERO CALLS" IS PROVEN HERE — STRUCTURALLY, NEVER OFF A COUNTER
-------------------------------------------------------------------
`assert load_ledger().calls_this_month == 0` is inert in fixture mode: it
passes whether or not the code is right, because fixture mode never touches the
ledger at all. That exact assertion was one of this project's four known
vacuous tests. Every zero-call claim in this directory is therefore made by
**removing the mechanism** and observing that the outcome is unchanged:

* live path  — `NoPurchase` is an `httpx.MockTransport` that *raises* on any
  request. A pull that reached for a call dies at the reach, naming the query
  it tried to buy. There is no counter to fool.
* fixture path — the saved responses are deleted from the fixtures directory
  before the second run. A pull that went back to the source now finds nothing,
  so the query flips to unsatisfied and `complete` goes false. Nothing else in
  the system can produce that transition.
* both — `entry_snapshot()` compares bytes **and `mtime_ns`**, so re-buying a
  response and writing back identical bytes is still caught.

MONEY (WORKFLOW.md §6): 42 of 50 monthly calls remain and they are the owner's.
Every live-path test here injects a `MockTransport`, which answers inside the
process and never opens a socket, and asserts `tests/conftest.py`'s autouse
`no_network` backstop recorded zero attempts. `RENTCOMP_LIVE` is set only by
the `live_env` fixture, only alongside a fake key, and never without a
transport.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import httpx

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.cache import raw_response_paths

#: Frozen, so the plan, the query signatures, the fixture filenames and the
#: cache key are identical on every run and on every machine. `run_pull` takes
#: `today` as data (never reads a clock), which is what makes this possible.
TODAY = date(2026, 7, 27)

#: The story's own arithmetic: **4** fetchable queries, so "re-running a 3-of-4
#: pull issues exactly 1" is expressible literally rather than by analogy.
#: 2 cohort years x {Active, Inactive}, none structurally empty (verified
#: against the real planner by `test_the_harness_pulls_four_windows`).
SEARCH: dict = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}

#: What a whole pull of `SEARCH` costs, and therefore how many distinct
#: records a healthy pull shapes into comps (one per window, see `record`).
FETCHABLE = 4

FAKE_KEY = "qa-f3s4-fake-key-not-a-real-credential"


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------


def planned(today: date = TODAY, **overrides) -> list:
    """The real F4-S1 fetchable plan for `SEARCH` — asked of the planner, never
    hardcoded, so these tests can never disagree with the code about what a
    pull is."""
    return fetchable_queries(plan_pull_queries(today, **{**SEARCH, **overrides}))


def identity(request: httpx.Request) -> tuple:
    """`(status, daysOld, offset)` — which window a request is for.

    Enough to tell "the resume bought the window that was missing" from "the
    resume bought *a* window", which a bare request count cannot.
    """
    return (
        request.url.params.get("status"),
        request.url.params.get("daysOld"),
        request.url.params.get("offset"),
    )


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------


def record(n: int, *, listed_days_ago: int | None = None) -> dict:
    """One synthetic RentCast record, in the shape `pipeline/shape.py` reads.

    A DISTINCT address per `n`, so dedupe/grouping cannot collapse two windows'
    evidence into one comp and make a destroyed response invisible — the count
    of shaped comps is this directory's evidence meter.
    """
    listed = TODAY - timedelta(days=listed_days_ago if listed_days_ago is not None else 120 + 30 * n)
    return {
        "id": f"f3s4-listing-{n}",
        "formattedAddress": f"{100 + n} W Durable Ave, Chicago, IL 60609",
        "addressLine1": f"{100 + n} W Durable Ave",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 1900 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


def body(n: int) -> bytes:
    """One window's response body: a single, unique, in-window record."""
    return json.dumps([record(n)]).encode("utf-8")


def ok(records: list[dict], *, total: int | None = None) -> httpx.Response:
    headers = {} if total is None else {"X-Total-Count": str(total)}
    return httpx.Response(200, json=records, headers=headers)


def one_record_per_query(request: httpx.Request, n: int) -> httpx.Response:
    return ok([record(n)])


# ---------------------------------------------------------------------------
# fixture mode
# ---------------------------------------------------------------------------


def seed_fixtures(directory: Path, *, today: date = TODAY, skip: tuple[int, ...] = ()) -> list:
    """One saved response per fetchable window, keyed the way the client looks
    them up. `skip` leaves those windows (by index) unsaved — the fixture-mode
    equivalent of a call that failed.
    """
    queries = planned(today)
    for index, query in enumerate(queries):
        if index in skip:
            continue
        (directory / f"{fixture_signature(query.params)}.json").write_bytes(body(index))
    return queries


def unseed_fixtures(directory: Path) -> None:
    """Remove every saved response — the fixture-mode half of the structural
    zero-call pin (see the module docstring). After this, any query that goes
    back to the source flips to unsatisfied, which nothing else can cause."""
    for saved in directory.glob("*.json"):
        saved.unlink()


# ---------------------------------------------------------------------------
# transports
# ---------------------------------------------------------------------------


class Wire:
    """A recording `httpx.MockTransport`. Answers a script; never opens a
    socket. Requests are recorded before the script runs, so a test can assert
    on what was *sent* even when the answer is an error."""

    def __init__(self, answer) -> None:
        self._answer = answer
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request, len(self.requests))

    @property
    def sent(self) -> list[tuple]:
        return [identity(request) for request in self.requests]


class NoPurchase:
    """A transport with no answer in it — the structural pin for "zero calls".

    Not a counter that a test then compares against zero, but the *absence of
    the mechanism*: reaching for a call raises here, at the reach, naming the
    window that was about to be bought a second time. A pull that spends
    nothing cannot tell this transport apart from any other, and a pull that
    spends anything cannot survive it.
    """

    def __init__(self, why: str = "") -> None:
        self._why = why
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError(
            f"a RentCast call was issued for {identity(request)} when the answer was "
            f"already on disk{f' ({self._why})' if self._why else ''}. Every call is one of "
            "50 the owner has this month, and a response that has been paid for is never "
            "bought again (D24 / ARCHITECTURE.md §5a)."
        )


# ---------------------------------------------------------------------------
# what is on disk
# ---------------------------------------------------------------------------


def entry_dir(home: Path, ref: str) -> Path:
    return home / "cache" / ref


def entry_snapshot(home: Path, ref: str) -> dict:
    """Every file under a cache entry: contents **and** `mtime_ns`.

    mtime is in the snapshot deliberately — a re-bought response that happened
    to arrive with identical bytes would leave the contents equal and is still
    a call spent twice.
    """
    root = entry_dir(home, ref)
    snapshot: dict = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = (path.read_bytes(), path.stat().st_mtime_ns)
    return snapshot


def readable_raw_bodies(ref: str) -> list:
    """The raw responses under `ref` that can actually be read and parsed.

    "Present" is not the same as "readable": a file truncated by a crash, a
    zero-byte file, or a directory standing where a response belongs are all
    present and none of them are evidence.
    """
    bodies = []
    for path in raw_response_paths(ref):
        try:
            bodies.append(json.loads(path.read_bytes()))
        except (OSError, ValueError, UnicodeDecodeError):
            continue
    return bodies
