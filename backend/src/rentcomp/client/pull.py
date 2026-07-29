"""The pull orchestrator (F4-S9) — the code that runs a whole plan.

Every piece of a pull already existed and none of them were connected: F4-S1
plans the windows, F0-S4 executes *one* query with pagination, F3-S1 stores raw
bytes and resolves a `pull_ref`, WS-1 shapes records into comps. Nothing ran a
whole plan, wrote it through to the cache, and minted a ref. This module is
that composition, and nothing else — it computes no statistic and parses no
record.

WHY `client/` AND NOT `storage/`
--------------------------------
This is the code that decides to **spend money**. `client/` is the package D17's
guard already watches (`tests/unit/test_live_call_guard.py`), and
`client/rentcast.py` already imports `storage.ledger`/`storage.secrets` — a
`storage/` module importing `client/` would invert that direction.

`today` ARRIVES AS AN ARGUMENT, ALWAYS
--------------------------------------
The F4-S1 planner precedent ("time is data here, not ambient state"), with more
force: `today` is both the window arithmetic's origin AND the manifest's
`as_of`, which is the pipeline's only "now" (owner ruling 1) and therefore what
decides which comps are censored. The API edge supplies `date.today()`; nothing
below it reads a clock.

THE FOUR THINGS THIS MODULE IS RESPONSIBLE FOR
-----------------------------------------------
1. **Spending exactly `len(fetchable_queries(plan))`, never `len(plan)`.** A
   structurally-empty window (F4-S1 AC4) is planned so the user can see it was
   considered, and never sent, because it cannot match a record whatever the
   market holds. One of 50 monthly calls spent to guarantee an empty answer is
   the owner's money burnt.
2. **Write-through before parsing (D24, [INVARIANT]).** Responses reach disk
   through F0-S4's `sink` seam, which fires per call with the body exactly as it
   arrived and *before* anything validates it. A failure on query 5 never loses
   the four already paid for, and a parsing bug costs nothing because the bytes
   are already safe. **Fixture-mode pages are persisted separately** —
   `_fetch_from_fixtures` never calls the sink, so a pull that trusted the sink
   alone would mint a ref resolving to zero comps on the path every test, every
   dev server and every Playwright run takes.
3. **Refusing up front what the budget cannot afford.** Not mid-way: half a
   pull is worse than none, because the calls are spent AND the missing cohort
   skews every number computed from what came back. The check is live-path
   only — a fixture-mode pull spends nothing, so the cap has no claim on it,
   and refusing there would freeze the whole build at any month-end near the
   cap.
4. **Naming the gap.** A partial pull is usable (§5a: with a 50-call cap,
   refusing to show anything until the set is perfect would strand the user),
   so a per-query failure returns a partial `PullOutcome` rather than raising —
   and the missing windows are recorded in the manifest, where they survive the
   process and can be read back without spending a call.

RESUME GRANULARITY (a logged [DEFAULT])
----------------------------------------
A query is satisfied when its whole result arrived; the resume diff is
per-query. Page-level resume (re-entering a truncated query at its last offset)
is out of scope by PM ruling (queue row 13c) and buys nothing here: no
committed fixture and no live pull in this product's plan has yet needed more
than one page per window.

F3-S4 — WHAT THE RESUME DIFF IS ACTUALLY JUDGED ON
---------------------------------------------------
**The bytes are the truth; the manifest is an index over them.** Every earlier
version of this module trusted the manifest alone, which cost money in one
direction and honesty in the other:

* a manifest it could not read meant "nothing is satisfied", so a broken
  *index* re-bought four responses that were sitting untouched in `raw/` — on
  the live path, 4 of the owner's 50 calls to work around a file they could
  have deleted for free; and
* a `satisfied` flag outlived the file it described, so a pull reported itself
  whole over evidence that was gone and every downstream number was quietly
  short a cohort.

So `_reconcile` corroborates the index against the store, in one direction
each way: **disk demotes** a recorded satisfaction with no usable bytes behind
it, and **disk promotes** a window the index says nothing about (there is no
record, or no readable manifest at all). A record that says a window *failed*
is real information the bytes cannot contradict — it stays failed, and stays
owed.

**A demoted window is not automatically an owed one**, and that is the third
thing the store is asked. A response that is *gone* is worth a call; a response
that is *there and unreadable* is not, because the call returns bytes already
on disk and already paid for. Collapsing the two is not a rounding error — it
has no stopping condition, so the window is re-bought on every attempt for as
long as the user keeps trying (`_corroborate`, and `_StoredResponses` for why
the store answers two questions rather than one).

`as_of` ADVANCES ONLY WHEN BYTES ACTUALLY ARRIVE (PM ruling, F3-S4)
--------------------------------------------------------------------
`as_of` is "when this evidence was observed", and it is the pipeline's only
"now": a censored comp's `effective_dom` is `as_of − listed`. Restamping it on
an attempt that fetched nothing therefore adds vacancy to comps nobody
re-observed — the same disk yielding different numbers across a restart. The
signal is a **write actually happening** (`_fetch_one` reports whether any
response reached the store), never "the set of held signatures grew": a forced
refresh overwrites the same files, so a set comparison would freeze `as_of`
forever and break the honest case instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import httpx

from rentcomp.client.planner import PlannedQuery, fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import (
    Mode,
    MissingApiKeyError,
    RentCastClient,
    RentCastError,
    ResponseUnusableError,
    resolve_mode,
)
from rentcomp.storage.cache import (
    CORRUPT_MANIFEST_ERRORS,
    CacheMissError,
    Manifest,
    QueryStatus,
    cache_key,
    raw_response_paths,
    read_manifest,
    read_raw_meta,
    read_raw_records,
    write_manifest,
    write_raw_response,
)
from rentcomp.storage.ledger import load_ledger
from rentcomp.storage.secrets import load_api_key

__all__ = [
    "InsufficientCallBudgetError",
    "PullOutcome",
    "pull_ref_for",
    "pull_status",
    "run_pull",
]


class InsufficientCallBudgetError(RentCastError):
    """The month cannot afford this pull, refused before anything was sent.

    Structured rather than prose: F2/F3's modal has to show "needs 6, 5 left"
    so the user can decide whether to narrow the search, and a caller that has
    to regex an error message to find that out will not do it.
    """

    def __init__(self, message: str, *, required: int, remaining: int) -> None:
        super().__init__(message)
        #: Calls this pull would send — the owed fetchable queries, not the plan.
        self.required = required
        #: Calls the month has left (`Ledger.remaining`).
        self.remaining = remaining


@dataclass(frozen=True)
class PullOutcome:
    """What a pull did, and what is still absent from it.

    The fields F2-S1/F2-S3/F3-S2/F4-S6 all consume. `calls_spent` and the
    number the cap is enforced on are one number by construction (it is read
    off the ledger, which is the record that governs the cap) — F2-S3 is
    [INVARIANT] on exactly that.
    """

    pull_ref: str
    #: Queries the plan emitted, structurally-empty windows included.
    planned: int
    #: The subset worth spending a call on — what a whole pull costs.
    fetchable: int
    calls_spent: int
    complete: bool
    #: One label per window that never arrived, each naming its cohort year
    #: and status ("2025 Inactive"). Empty when the pull is whole.
    missing: tuple[str, ...]
    calls_to_complete: int


def pull_ref_for(
    *,
    address: str,
    radius: float,
    bedrooms: str,
    bathrooms: str | None = None,
    property_types: Iterable[str],
    years_back: int,
    window_start_mmdd: str,
    window_end_mmdd: str,
) -> str:
    """The ref a search maps to — F3-S1's cache key over the search params.

    A function of the search and nothing else: not of the clock, not of
    insertion order. F1's recents list and F14's workspace files are both keyed
    on it, so a ref that drifted between two identical searches would silently
    orphan the user's curation.

    `today` is deliberately NOT part of it. The same search tomorrow is the
    same evidence set — that is what makes the cache worth having — and the
    fetch date lives in the manifest, which is the only place it lives at all
    (F3-S1's PM-relayed AC 3).
    """
    return cache_key(
        {
            "address": address,
            "radius": radius,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "property_types": list(property_types),
            "years_back": years_back,
            "window_start": window_start_mmdd,
            "window_end": window_end_mmdd,
        }
    )


def run_pull(
    *,
    address: str,
    radius: float,
    bedrooms: str,
    bathrooms: str | None = None,
    property_types: Iterable[str],
    years_back: int,
    window_start_mmdd: str,
    window_end_mmdd: str,
    today: date,
    force_refresh: bool = False,
    transport: httpx.BaseTransport | None = None,
) -> PullOutcome:
    """Run a whole plan: fetch what is owed, keep every response, mint a ref.

    Sends exactly the fetchable queries this pull does not already hold, so a
    re-run of a complete pull costs zero and a resume costs exactly the
    remainder (§5a). Never raises for a per-query failure — the result is a
    partial `PullOutcome` naming the gap. It *does* raise before sending
    anything when the month cannot afford the pull
    (`InsufficientCallBudgetError`) or when live mode has no key
    (`MissingApiKeyError`).

    Args:
        force_refresh: re-fetch every fetchable window, including the ones
            already on disk. This is the user's explicit REFRESH click (F3-S2)
            and nothing else sets it — it is the difference between "finish
            what I paid for" and "go buy it again". Responses land in the same
            cache entry, overwriting per call; **reconciling a refresh against
            the previous set (spec §7's "never mix fresh and stale") belongs to
            F3-S3/F13-S1** and is deliberately not attempted here.
        transport: an `httpx.BaseTransport` for `RentCastClient` to send
            through. This is how a test exercises the live path without
            spending a call — and it is **not** consent: with no
            `RENTCOMP_LIVE=1` in the environment the pull stays in fixture mode
            and the transport receives nothing at all (D17).
    """
    property_types = list(property_types)
    plan = plan_pull_queries(
        today,
        years_back=years_back,
        window_start_mmdd=window_start_mmdd,
        window_end_mmdd=window_end_mmdd,
        address=address,
        radius=radius,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        property_types=property_types,
    )
    # The ONE filter both this module and F2-S3's estimator use. Two
    # implementations of "what does this pull cost" is exactly the drift F2-S3
    # is [INVARIANT] against.
    fetchable = fetchable_queries(plan)

    key = pull_ref_for(
        address=address,
        radius=radius,
        bedrooms=bedrooms,
        bathrooms=bathrooms,
        property_types=property_types,
        years_back=years_back,
        window_start_mmdd=window_start_mmdd,
        window_end_mmdd=window_end_mmdd,
    )
    window = (window_start_mmdd, window_end_mmdd)

    recorded = _read_manifest_or_none(key)
    known = {} if force_refresh else _reconcile(key, fetchable, recorded)
    owed = [query for query in fetchable if _is_owed(known.get(_sig(query)))]

    # The date this entry's evidence was observed, carried forward from what is
    # already on disk. Only a response actually arriving below moves it.
    as_of = _held_as_of(key, recorded, today)

    if not owed:
        if recorded is None:
            # Two cases, one answer. (a) A plan with NOTHING fetchable — every
            # window lies in the future (F4-S1 AC4) — still gets an entry, so
            # the search reads as "planned, cost nothing, found nothing"
            # rather than as a pull that does not exist. (b) The manifest was
            # lost or corrupted while its responses survived: rebuilding the
            # index from the store is the repair, and it costs nothing.
            _write_manifest(key, as_of, window, plan, fetchable, known)
        # Otherwise nothing to buy and nothing to write: report what is already
        # on disk rather than restamping a manifest with a fetch that did not
        # happen.
        return _outcome_from(key, planned=len(plan), fetchable=len(fetchable), calls_spent=0)

    mode = resolve_mode()
    spent_before = 0
    if mode is Mode.LIVE:
        # Both halves of D17 and the whole of AC5, before a transport exists.
        _require_api_key()
        spent_before = _require_budget(len(owed))

    for query in owed:
        status, arrived = _fetch_one(key, query, today, transport)
        known[_sig(query)] = status
        if arrived:
            # A response reached the store, so this pull really did observe the
            # market today. Nothing else moves `as_of` — see the module
            # docstring for why a failed attempt must not.
            as_of = today
        # Written after every query, not once at the end: a crash mid-pull must
        # leave a manifest that still explains what is on disk, or the retry
        # re-buys responses that were already paid for (D24).
        _write_manifest(key, as_of, window, plan, fetchable, known)

    calls_spent = (load_ledger().calls_this_month - spent_before) if mode is Mode.LIVE else 0
    return _outcome_from(
        key, planned=len(plan), fetchable=len(fetchable), calls_spent=calls_spent
    )


def pull_status(pull_ref: str) -> PullOutcome:
    """What a pull holds and what it is missing, read back from disk.

    No network, no clock, no ledger movement — reading must never be a
    mutation and must certainly never be a purchase. This is what lets a user
    who reopens a workspace tomorrow still be told "2025 inactive missing · 1
    call to complete".

    **Judged on the responses, not only on the flags that index them** (F3-S4).
    `POST /api/search` answers `complete` from here and `POST /api/derive`
    answers from the bytes; when a crash separates the two, a manifest whose
    `satisfied` flags outlive their files tells the user the evidence is whole
    and then shows them less of it than they paid for, with every downstream
    number short a cohort and nothing on screen to say so. Corroborating here
    costs a `stat` and a parse per response on a route that already refuses to
    fetch.

    Raises `CacheMissError` when there is no such pull.
    """
    manifest = read_manifest(pull_ref)
    store = _stored_responses(pull_ref)
    corroborated = Manifest(
        as_of=manifest.as_of,
        window=manifest.window,
        planned=manifest.planned,
        fetchable=manifest.fetchable,
        queries=tuple(_corroborate(query, store) for query in manifest.queries),
    )
    return PullOutcome(
        pull_ref=pull_ref,
        planned=corroborated.planned,
        fetchable=corroborated.fetchable,
        calls_spent=0,
        complete=corroborated.complete,
        missing=corroborated.missing,
        calls_to_complete=corroborated.calls_to_complete,
    )


def _corroborate(status: QueryStatus, store: _StoredResponses) -> QueryStatus:
    """A recorded window's status, checked against the bytes behind it.

    Demotion only: a claimed satisfaction with no usable response left on disk
    is demoted to an open window. A recorded *failure* is never promoted here —
    that would need the plan to know a signature was even expected, and
    `_reconcile` (which has it) is where that judgement is made.

    **Demoting is not the same as owing**, and conflating the two is how a
    window becomes an unbounded purchase. Two cases, one demotion, opposite
    prices:

    * the response file is **gone** — a call really does deliver something this
      pull does not have, so the window is owed and priced at one call; and
    * the response file is **there and unusable** — a call returns bytes we are
      already holding and already paid for, so the window is missing (its
      evidence cannot be produced) and **not owed**. This is the read-path
      twin of `_fetch_one`'s "the sink filed it, so nothing is owed" rule, and
      the reason it exists separately: a body the fetch path accepted and the
      store cannot use reaches this function without ever raising, so the
      typed guard never runs. Measured before it was fixed: 2 calls per attempt,
      every attempt, for a window that could never complete.

    The cost of being wrong is deliberately asymmetric. Refusing to re-buy a
    file that a disk fault (rather than the response itself) made unreadable
    leaves the pull honestly incomplete at zero cost, and the error below says
    exactly how to clear it. Re-buying it spends the owner's month, with no
    stopping condition — which is the failure D24 exists to prevent.
    """
    if not status.satisfied or status.sig in store.held:
        return status
    if status.sig in store.filed:
        return QueryStatus(
            label=status.label,
            sig=status.sig,
            satisfied=False,
            owed=False,
            error=(
                "this window's response is on disk and cannot be read as listings. It was "
                "paid for and re-fetching returns the same bytes, so no call is owed for "
                "it. If the file itself is damaged rather than the response, delete this "
                "cache entry and search again."
            ),
        )
    return QueryStatus(
        label=status.label,
        sig=status.sig,
        satisfied=False,
        error=(
            "the response that answered this window is no longer on disk, so the window "
            "is open again"
        ),
    )


# ---------------------------------------------------------------------------
# one query
# ---------------------------------------------------------------------------


def _fetch_one(
    key: str,
    query: PlannedQuery,
    today: date,
    transport: httpx.BaseTransport | None,
) -> tuple[QueryStatus, bool]:
    """Fetch one planned window, keeping every response byte it costs.

    Returns `(status, arrived)`, where `arrived` is True when at least one
    response body reached the store during this call — the ONE signal that
    moves the manifest's `as_of` (see the module docstring). It is reported
    from the writer's own answer rather than inferred from a file listing,
    because a forced refresh rewrites the same filenames and would be
    invisible to any comparison of what is held.

    A failure is returned, never raised: §5a's "incomplete first pull →
    usable, with the gap named loudly". Raising here would throw away the
    windows that did arrive — and on the live path, throw away calls that were
    already paid for.
    """
    base = _sig(query)
    persisted: set[int] = set()

    def sink(params: dict, raw: bytes, meta: dict) -> None:
        """D24 write-through. Called by `RentCastClient` per call, with the
        body exactly as it arrived, BEFORE anything parses it."""
        offset = _offset_of(meta)
        if write_raw_response(key, _page_sig(base, offset), raw, meta=_meta(meta, today)):
            persisted.add(offset)

    client = RentCastClient(transport=transport, sink=sink)
    try:
        result = client.fetch_listings(query.params)
    except ResponseUnusableError as exc:
        # A 2xx that arrived, was billed, and is filed (the sink runs before
        # anything parses) — and that this parser cannot read. Not satisfied:
        # its evidence is unusable, so `missing` must keep naming it. Not
        # owed either: another call returns the same bytes, and D24's whole
        # point is that a parsing bug or a schema surprise costs nothing.
        if persisted:
            return (
                QueryStatus(
                    label=_label(query),
                    sig=base,
                    satisfied=False,
                    owed=False,
                    error=(
                        f"the response arrived and could not be read: {exc}. It is filed "
                        "under this pull; re-fetching would return the same bytes, so no "
                        "call is owed for it."
                    ),
                ),
                True,
            )
        # Fixture mode: nothing reached the sink, so there is nothing on disk
        # and the window is owed like any other failure.
        return _failed(query, base, exc), False
    except RentCastError as exc:
        # The call delivered nothing (non-2xx, transport failure, no fixture,
        # budget). A retry might; the window stays owed.
        return _failed(query, base, exc), bool(persisted)
    except OSError as exc:
        # The store refused the bytes (a directory in the way, a full disk, a
        # permission). One window's storage failure must not lose the windows
        # already filed under this ref, so it is reported like any other gap
        # rather than unwinding the whole pull.
        return _failed(query, base, exc), bool(persisted)

    # Fixture mode never reaches the sink (`_fetch_from_fixtures` returns
    # without emitting), so its pages are written here instead. Keyed on the
    # page's own offset rather than on the mode, so neither path can
    # double-write and neither can be forgotten.
    for page in result.pages:
        if page.offset in persisted:
            continue
        written = write_raw_response(
            key,
            _page_sig(base, page.offset),
            page.raw,
            meta=_meta(
                {
                    "sig": base,
                    "offset": page.offset,
                    "total_count": page.total_count,
                    "fetched_at": today.isoformat(),
                },
                today,
            ),
        )
        if written:
            persisted.add(page.offset)

    if result.complete:
        return QueryStatus(label=_label(query), sig=base, satisfied=True), bool(persisted)
    return (
        QueryStatus(
            label=_label(query),
            sig=base,
            satisfied=False,
            error=(
                f"the response was truncated: {result.fetched} of "
                f"{result.total_count if result.total_count is not None else 'an unknown number of'} "
                "records arrived"
            ),
        ),
        bool(persisted),
    )


def _failed(query: PlannedQuery, base: str, exc: Exception) -> QueryStatus:
    """A window that did not arrive, recorded with the reason §5a asks for.

    The reason is the exception's own message, so a 429 ("wait") and a 500
    ("retry") stay the different instructions they are.
    """
    return QueryStatus(label=_label(query), sig=base, satisfied=False, error=str(exc))


def _meta(meta: dict, today: date) -> dict:
    """The sidecar this pull files beside a response.

    `as_of` is added on top of whatever the client reported: the client stamps
    `fetched_at` from the wall clock, while this pull's "now" is the `today`
    it was given (see the module docstring), and the manifest's `as_of` has to
    be rebuildable from these sidecars alone after a crash loses the manifest.
    """
    return {**meta, "as_of": today.isoformat()}


# ---------------------------------------------------------------------------
# the two refusals — both before a transport exists
# ---------------------------------------------------------------------------


def _require_api_key() -> None:
    """D17's second half, checked here as well as in the client.

    Not redundant: this runs *before* the manifest is written, so a keyless
    live run leaves no cache entry behind. An entry that resolves to zero comps
    reads downstream as "no comps in radius" and would send the user off
    widening a radius to fix a missing credential.
    """
    if not load_api_key():
        raise MissingApiKeyError(
            "RENTCOMP_LIVE=1 is set but no RentCast API key is configured. Set "
            "RENTCAST_API_KEY, or save one in Settings (<RENTCOMP_HOME>/secrets.json). "
            "Live mode requires both (D17). Nothing was sent."
        )


def _require_budget(required: int) -> int:
    """AC5: refuse to *start* a pull the month cannot afford; return what has
    been spent so far.

    Judged on the OWED queries, not the whole plan: refusing a 2-call resume
    against a 3-call remainder because the original plan was 6 would strand a
    user over calls this pull was never going to make.
    """
    ledger = load_ledger()
    if ledger.remaining < required:
        raise InsufficientCallBudgetError(
            f"this pull needs {required} RentCast call(s) and the month has "
            f"{ledger.remaining} left. Nothing was sent — narrow the search (fewer years, "
            "a shorter window) or wait for the monthly allowance to reset.",
            required=required,
            remaining=ledger.remaining,
        )
    return ledger.calls_this_month


# ---------------------------------------------------------------------------
# manifest <-> plan
# ---------------------------------------------------------------------------


def _read_manifest_or_none(key: str) -> Manifest | None:
    """The manifest at `key`, or `None` when there is no readable one.

    `None` covers both "no entry yet" and "an entry this version cannot read".
    They are the same fact for every caller here — the index tells us nothing —
    and the difference that used to matter (whether to write a manifest in the
    nothing-owed branch) resolves the same way for both: write one.

    `CORRUPT_MANIFEST_ERRORS` is imported rather than re-spelled. This is the
    sixth call site in that family on this project, and the previous five drifted
    apart one `except` clause at a time: `(LookupError, OSError, ValueError)`
    here caught three of the five types `storage/cache.py` names, so a manifest
    whose *types* are wrong (`window: 5` → `TypeError`, a `queries` list of
    non-records → `AttributeError`) walked straight out of `run_pull` and
    reached every caller as an anonymous 500. One name, so the next shape is
    added once.
    """
    try:
        return read_manifest(key)
    except (CacheMissError, *CORRUPT_MANIFEST_ERRORS):
        return None


def _reconcile(
    key: str,
    fetchable: list[PlannedQuery],
    recorded: Manifest | None,
) -> dict[str, QueryStatus]:
    """What this pull already holds — the index, corroborated by the store.

    See the module docstring for the rule. In one sentence: **disk demotes a
    claimed satisfaction it cannot back, disk promotes a window the index is
    silent about, a recorded failure stands — and a demotion is priced by
    whether the response is gone or merely unreadable.**
    """
    store = _stored_responses(key)
    index = {query.sig: query for query in (recorded.queries if recorded else ())}

    known: dict[str, QueryStatus] = {}
    for query in fetchable:
        sig = _sig(query)
        status = index.get(sig)
        if status is None:
            # The manifest is unreadable, or readable and silent about this
            # window. Bytes filed under it were paid for; re-buying them to
            # rebuild an index is the double-pay D24 forbids.
            if sig in store.held:
                known[sig] = QueryStatus(label=_label(query), sig=sig, satisfied=True)
            continue
        known[sig] = _corroborate(status, store)
    return known


@dataclass(frozen=True)
class _StoredResponses:
    """What a cache entry's `raw/` directory actually holds, in two questions.

    `held` — signatures with at least one USABLE response. Usable, not merely
    present: a file truncated by a crash, emptied, or standing as a directory
    is present and is not evidence, and a pull that counted it would report
    itself whole over bytes nothing can be derived from. `read_raw_records`
    draws that line in one place, so what this module calls held and what
    `storage/pulls.py` can actually shape are the same set by shared code.

    `filed` — signatures with a response file at all, usable or not. The
    difference between the two sets is exactly "we are holding bytes we cannot
    read", which is the one state where a window is missing and still owes
    nothing. Without it, "unusable" and "absent" are indistinguishable and the
    unusable case is re-bought forever.
    """

    held: frozenset[str]
    filed: frozenset[str]


def _stored_responses(key: str) -> _StoredResponses:
    """Both sets in one pass over the entry's raw responses."""
    held: set[str] = set()
    filed: set[str] = set()
    for path in raw_response_paths(key):
        sig = _query_sig_of(path.stem)
        filed.add(sig)
        if read_raw_records(path) is not None:
            held.add(sig)
    return _StoredResponses(held=frozenset(held), filed=frozenset(filed))


def _held_as_of(key: str, recorded: Manifest | None, today: date) -> date:
    """The day this entry's evidence was observed, before this run adds to it.

    From the manifest when there is one. When there is not — a crash lost it,
    and its responses did not — from the `as_of` each response's sidecar was
    filed with, so the repair does not stamp its own day on comps nobody
    re-observed. Never later than `today`: a pull cannot have observed the
    market after the date it was given.
    """
    if recorded is not None:
        return min(recorded.as_of, today)

    dates = [
        parsed
        for path in raw_response_paths(key)
        if (parsed := _sidecar_as_of(read_raw_meta(path))) is not None
    ]
    return min(max(dates), today) if dates else today


def _sidecar_as_of(meta: dict) -> date | None:
    """`as_of` out of one response's sidecar, falling back to `fetched_at`.

    `fetched_at` is the fallback for sidecars written before F3-S4 added
    `as_of`; it is the wall clock rather than the pull's `today`, which is why
    it is second and why `_held_as_of` clamps the result.
    """
    for field in ("as_of", "fetched_at"):
        value = meta.get(field)
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                continue
    return None


def _is_owed(status: QueryStatus | None) -> bool:
    """Should completing this pull send a call for this window?"""
    return status is None or status.is_owed


def _write_manifest(
    key: str,
    as_of: date,
    window: tuple[str, str],
    plan: list[PlannedQuery],
    fetchable: list[PlannedQuery],
    known: dict[str, QueryStatus],
) -> None:
    """Record §5a's manifest: planned, fetchable, and each window's fate.

    Only the FETCHABLE queries get a record. A structurally-empty window is
    planned-and-never-sent by design, so listing it as missing would show a
    permanent "evidence incomplete" warning for evidence that cannot exist —
    and F4-S6 renders `missing` verbatim.
    """
    write_manifest(
        key,
        as_of=as_of,
        window=window,
        planned=len(plan),
        fetchable=len(fetchable),
        queries=[
            known.get(
                _sig(query),
                QueryStatus(
                    label=_label(query),
                    sig=_sig(query),
                    satisfied=False,
                    error="not fetched",
                ),
            )
            for query in fetchable
        ],
    )


def _outcome_from(key: str, *, planned: int, fetchable: int, calls_spent: int) -> PullOutcome:
    """The outcome, read back off the manifest that was just written — so what
    a caller is told and what survives on disk cannot disagree."""
    status = pull_status(key)
    return PullOutcome(
        pull_ref=key,
        planned=planned,
        fetchable=fetchable,
        calls_spent=calls_spent,
        complete=status.complete,
        missing=status.missing,
        calls_to_complete=status.calls_to_complete,
    )


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------


def _sig(query: PlannedQuery) -> str:
    """A query's signature: `y2025-inactive` (F3-S4's convention).

    Load-bearing beyond tidiness: `storage/pulls.py` buckets a cached response
    into the Active or Inactive list by testing `"inactive" in path.stem`, so a
    file named any other way would be silently misfiled. Lowercase, and
    "inactive" contains "active" only as a substring of itself.
    """
    return f"y{query.year}-{query.status.lower()}"


def _page_sig(base: str, offset: int) -> str:
    """One file per CALL, not per query — `y2025-inactive-off000`.

    Per-call granularity is what makes a partial set meaningful and a resume
    cost exactly the remainder (§5a). A scheme that collided across pages would
    destroy a response that was paid for while every count downstream still
    looked plausible.
    """
    return f"{base}{_PAGE_MARKER}{offset:03d}"


#: The separator `_page_sig` puts between a query's signature and its page
#: offset. Named once so the two halves of the convention cannot drift.
_PAGE_MARKER = "-off"


def _query_sig_of(stem: str) -> str:
    """The query a stored response belongs to — `_page_sig` read backwards.

    `y2025-inactive-off000` → `y2025-inactive`. A filename that predates the
    convention (or comes from another writer) maps to itself, which is
    harmless: it simply matches no planned query and holds nothing back.
    """
    return stem.rsplit(_PAGE_MARKER, 1)[0]


def _label(query: PlannedQuery) -> str:
    """What the user is told is missing: "2025 Inactive"."""
    return f"{query.year} {query.status}"


def _offset_of(meta: dict) -> int:
    try:
        return int(meta.get("offset") or 0)
    except (TypeError, ValueError):
        return 0
