"""Queue row 13c — page-level resume and lost-page integrity: shared harness.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Layer 1 throughout: `run_pull` / `pull_status` / `load_shaped_pull`
are importable functions callable with plain values, and nothing this row
guarantees ("which offset went on the wire", "which page's bytes survived",
"how many records the loader could still shape") is observable from a browser.

WHY THIS LIVES IN `f3s4/` RATHER THAN A DIRECTORY OF ITS OWN
-------------------------------------------------------------
Row 13c is F3-S4's deliberately-deferred half, in the same module, judged on
the same two invariants (no double-pay; never mistaken for satisfied). The
fixtures it needs — an isolated `RENTCOMP_HOME`, the two modes, the
`no_sockets` money backstop — already exist in `f3s4/conftest.py`, and the
`Wire`/`record`/`SEARCH` vocabulary already exists in `f3s4_support.py`.
Duplicating either into a parallel directory would create exactly the
two-copies-of-one-rule drift this project has now paid for six times
(`CORRUPT_MANIFEST_ERRORS`, `records_in`, `scripts/gate.py:199`).

THE ONE THING THAT IS NEW HERE: A WINDOW THAT SPANS PAGES
----------------------------------------------------------
`f3s4_support.py`'s market answers every window in a single page, so every
file under `raw/` is a whole query and "query" and "call" are the same unit.
This row exists because they are not: the committed gate fixture is **500 of
690 records**, so the owner's next real pull of that search paginates, and at
page granularity both of F3-S4's invariants have a second, unguarded edge.

`Market` below therefore answers ONE window over three pages and every other
window in one, so a healthy pull holds 6 page-files and shapes 9 comps —
enough that losing the first, a middle, or the last page are three genuinely
different failures rather than three spellings of one.

PAGES ARE ADDRESSED BY THE RECORDS INSIDE THEM, NEVER BY FILENAME
------------------------------------------------------------------
`_page_sig`'s `y2026-active-off002` naming is F3-S4's logged **[DEFAULT]**, and
a harness that grepped for `-off002` would fail the day a developer legitimately
changes the layout — for no reason connected to what this row is about. Every
helper here finds a page by the unique record ids it carries (`pages_holding`),
which is layout-free and says what the test means: "the page holding the first
two records of the spanning window".

⚠ The sidecar is NOT usable as a per-query key, and a fix must not assume it is.
`_fetch_one`'s two write paths disagree about what `meta["sig"]` means: the
fixture path files the QUERY signature (`y2026-active`), while the live path
files `fixture_signature(page_params)`, which includes `offset` and is therefore
**different for every page**. Grouping pages by `meta["sig"]` would work in
fixture mode and silently not in live mode — the index/loader divergence class
that failed F3-S4 (see QUEUE.md row 13e), one layer down.

MONEY (WORKFLOW.md §6): `RENTCOMP_LIVE` is set only by `f3s4/conftest.py`'s
`live_env`, only beside a fake key, and never without an `httpx.MockTransport`
that answers inside the process. 42 of 50 monthly calls remain and they are the
owner's; nothing in this row may spend one.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from f3s4_support import SEARCH, TODAY, identity, planned, record

from rentcomp.storage.cache import raw_response_paths

__all__ = [
    "Denied",
    "Interrupted",
    "Market",
    "PAGED_RECORD_IDS",
    "PAGE_SIZE",
    "SEARCH",
    "SPANNING_TOTAL",
    "SPANNING_WINDOW",
    "TODAY",
    "bought_page_zero",
    "expected_comps",
    "expected_files",
    "expected_pages",
    "offsets_sent",
    "page_files",
    "pages_holding",
    "record_ids_on_disk",
]

#: Records per page in the spanning window. Small on purpose: the invariants are
#: about *which* pages are held, and a big page would only make the fixtures slow.
PAGE_SIZE = 2

#: How many records the spanning window's `X-Total-Count` promises — three pages
#: of `PAGE_SIZE`. Three, not two, because a **middle** page is the shape that
#: distinguishes a real page index from the cheapest wrong fix ("resume after the
#: highest offset I hold"), which recovers a lost last page and silently never
#: recovers a lost middle one.
SPANNING_TOTAL = PAGE_SIZE * 3

#: Which fetchable window spans pages — the first one the F4-S1 planner emits.
#: An index rather than a hardcoded `y2026-active`, so this harness cannot
#: disagree with the planner about what a pull is (and so it keeps working in
#: any calendar year: `run_pull` takes `today` as data).
SPANNING_WINDOW = 0

#: The record ids the spanning window answers with, in page order. Each `record`
#: carries a DISTINCT address, so dedupe/stitching cannot collapse two pages'
#: evidence into one comp and make a destroyed page invisible — the count of
#: shaped comps is this row's evidence meter, exactly as in F3-S4.
PAGED_RECORD_IDS: tuple[str, ...] = tuple(record(n)["id"] for n in range(1, SPANNING_TOTAL + 1))


def expected_pages() -> int:
    """How many page-files the spanning window alone should leave on disk."""
    return SPANNING_TOTAL // PAGE_SIZE


def expected_files() -> int:
    """Every page-file a healthy pull of `SEARCH` should leave on disk."""
    return expected_pages() + (len(planned()) - 1)


def expected_comps() -> int:
    """Every comp a healthy pull should shape: one per record, all distinct."""
    return SPANNING_TOTAL + (len(planned()) - 1)


class Market:
    """A replayable market, answered by *what was asked for* — never by a call
    counter.

    `f3s4_support.Wire` scripts responses by call ordinal (`n == 1`, `n == 2`),
    which is enough when every window is one call and the order is fixed. It is
    not enough here: this row is about a **resume** re-entering a window at a
    particular offset, and a script keyed on ordinal would answer a resumed
    page-2 request with page 0's body and then "prove" the records came back.

    So this transport behaves like a server: the same `(window, offset)` always
    returns the same records. A recovery that asks for the page it lost gets
    that page; one that asks for a page it already holds gets those same bytes
    back, and is caught by the caller counting requests rather than by the
    answer changing.
    """

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)
        queries = planned()
        #: `(status, daysOld)` -> the planner's index for that window.
        self._windows = {
            (query.params.get("status"), query.params.get("daysOld")): index
            for index, query in enumerate(queries)
        }

    # -- what a test asks it -----------------------------------------------

    @property
    def sent(self) -> list[tuple]:
        return [identity(request) for request in self.requests]

    @property
    def calls(self) -> int:
        return len(self.requests)

    # -- what the wire asks it ---------------------------------------------

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        window = self._windows.get(
            (request.url.params.get("status"), request.url.params.get("daysOld"))
        )
        if window is None:  # pragma: no cover - a request for an unplanned window
            raise AssertionError(
                f"the pull asked for a window the plan does not contain: {identity(request)}"
            )
        offset = int(request.url.params.get("offset") or 0)
        if window == SPANNING_WINDOW:
            return self._page(offset)
        # Every other window: one unique record, no total header, so the
        # client's "a short page is the end of the set" rule completes it.
        return httpx.Response(200, json=[record(100 + window)])

    def _page(self, offset: int) -> httpx.Response:
        records = [record(n) for n in range(1, SPANNING_TOTAL + 1)]
        return httpx.Response(
            200,
            json=records[offset : offset + PAGE_SIZE],
            headers={"X-Total-Count": str(SPANNING_TOTAL)},
        )


class Interrupted(Market):
    """`Market`, with the spanning window's SECOND call failing.

    The interruption this row's money half is about: page 0 arrives and is filed
    through the D24 sink, the next call dies, and the window is left genuinely
    short of evidence and genuinely owed. A 503 rather than a transport error so
    that the call is billed and recorded the way a real one would be.
    """

    def _page(self, offset: int) -> httpx.Response:
        if offset:
            return httpx.Response(503, json={"error": "gone"})
        return super()._page(offset)


class Denied:
    """A transport that raises on any request, naming the offset it was asked
    to buy — `f3s4_support.NoPurchase` with the page in the message.

    The structural pin for "zero calls" (never a counter compared against
    zero): a pull that spends nothing cannot tell this transport from `Market`,
    and a pull that spends anything dies at the reach.
    """

    def __init__(self, why: str = "") -> None:
        self._why = why
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        raise AssertionError(
            f"a RentCast call was issued for {identity(request)} when that page's bytes are "
            f"already filed under this pull{f' ({self._why})' if self._why else ''}. Every "
            "call is one of 50 the owner has this month, and per-CALL file granularity is "
            "what makes a partial set meaningful (D24 / ARCHITECTURE.md §5a)."
        )


# ---------------------------------------------------------------------------
# what is on disk, addressed by content
# ---------------------------------------------------------------------------


def _records_in_file(path: Path) -> list[dict]:
    """The records a stored response holds, or `[]` if it is not readable.

    Deliberately its own tiny parse rather than `storage.cache.read_raw_records`:
    a harness that located pages with the same function the code under test uses
    to decide what counts as evidence could not tell "the loader ignored this
    file" from "there was no such file".
    """
    try:
        payload = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    if isinstance(payload, dict):
        payload = payload.get("listings")
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def page_files(ref: str) -> list[Path]:
    """Every stored response under `ref` — pages and single-page windows alike."""
    return raw_response_paths(ref)


def pages_holding(ref: str, *record_ids: str) -> list[Path]:
    """The stored responses carrying any of these record ids.

    Content-addressed on purpose: see the module docstring on why no test here
    may name a `-off002` file.
    """
    wanted = set(record_ids)
    return [
        path
        for path in page_files(ref)
        if wanted & {str(item.get("id")) for item in _records_in_file(path)}
    ]


def record_ids_on_disk(ref: str) -> set[str]:
    """Every record id still readable anywhere under `ref`."""
    return {
        str(item.get("id")) for path in page_files(ref) for item in _records_in_file(path)
    }


def offsets_sent(wire: Market | Denied) -> list[str | None]:
    """The `offset` parameter of every request, in order — `None` for page 0.

    `client.rentcast._page_params` omits `offset` entirely on page 0 so that it
    hashes to the same signature the gate's un-offset call did, so "page 0" is
    `None` on the wire and never `"0"`. Both spellings are treated as page 0
    wherever this is used.
    """
    return [identity(request)[2] for request in wire.requests]


def bought_page_zero(wire: Market | Denied) -> bool:
    """Did anything on this wire ask for a window's FIRST page?"""
    return any(offset in (None, "0") for offset in offsets_sent(wire))
