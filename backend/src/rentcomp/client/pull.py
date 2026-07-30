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
   cap. ⚠ **It is counted in WINDOWS, and a window is no longer a call** — a
   resume that re-enters a window mid-way can send more than one, so this refusal
   and `Manifest.calls_to_complete` both under-count for a paginated window.
   That is **queue row 13j**, split out on purpose: a first attempt at it here
   introduced four defects of its own, and the number reaches the user (F3-S2's
   modal asks consent for it, F4-S6 renders it) so it wants its own tests
   first.
4. **Naming the gap.** A partial pull is usable (§5a: with a 50-call cap,
   refusing to show anything until the set is perfect would strand the user),
   so a per-query failure returns a partial `PullOutcome` rather than raising —
   and the missing windows are recorded in the manifest, where they survive the
   process and can be read back without spending a call.

RESUME GRANULARITY — THE PAGE, NOT THE QUERY (queue row 13c)
-------------------------------------------------------------
A window is satisfied when every page its own evidence accounts for is on disk
and readable — never when *any one* page filed under its signature parses. The
unit that was paid for is the **call** (§5a's per-call file granularity), so it
is also the unit of satisfaction and the unit of a resume: a query interrupted
between calls is re-entered at the offset its held pages account for, and a
query that lost one page buys back that page and nothing else.

That is not hypothetical arithmetic. The committed gate fixture holds 500
records of a set of 690, so a real pull of that search spans several calls, and
at that granularity both of F3-S4's invariants had a second unguarded edge: a
window that lost its first page reported itself whole (`complete=True,
missing=()`), the loader then shaped fewer comps than the pull had paid for,
and — worst of the three — the next attempt sent nothing at all, so the lost
records were unreachable without a REFRESH that re-bought the whole window.

**The page index is derived from the store on every read, never held only in
the manifest.** `_stored_responses` reads the same files `storage/pulls.py`
shapes from, through the same `records_from_raw`, so what this module calls
held and what the loader can produce are the same set *by shared code* rather
than by parallel logic (queue row 13e's binding lesson: recording the index
only in the manifest would make the index stricter than the store, and an entry
that lost its manifest while keeping every page would be re-bought). Rebuilding
that index costs a directory scan and a parse, and no call.

**It is keyed on the response filename's own `-offNNN` suffix and on the
sidecar's `offset`/`total_count` — never on the sidecar's `sig`.** The two
write paths below file different `sig` values (the live path a per-page hash,
the fixture path the per-query signature), so grouping by `meta["sig"]` would
work against fixtures and silently not against the wire. Reconciling the two
is queue row 13h's; this module only declines to depend on either.

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

import stat
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx

from rentcomp.client.planner import PlannedQuery, fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import (
    MAX_LIMIT,
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
        status, arrived = _fetch_one(
            key, query, today, transport, restart=force_refresh, mode=mode
        )
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
    """A recorded window's status, checked against the pages behind it.

    Demotion only: a claimed satisfaction whose pages do not add up to the
    evidence they themselves account for is demoted to an open window. A
    recorded *failure* is never promoted here — that would need the plan to know
    a signature was even expected, and `_reconcile` (which has it) is where that
    judgement is made.

    **Judged per page (queue row 13c).** Satisfaction used to be granted to any
    signature with one readable file under it, so a window spanning three calls
    reported itself whole after losing one and the loader silently shaped less
    evidence than the pull had paid for.

    WIDENED REACH, RECORDED RATHER THAN INHERITED
    ----------------------------------------------
    Demotion used to need EVERY page under a signature to be unreadable; now one
    is enough. So a *transient* unreadable page — a OneDrive or antivirus lock,
    which on this platform is a real event rather than a thought experiment —
    demotes a window the old rule left alone. Two consequences, both deliberate:
    the message names the single file rather than advising anything destructive
    (`_demoted`), and a gap beneath a blocked page stays buyable
    (`_window_state`), so a lock cannot strand recoverable evidence.

    A residual, pre-existing and not created here: a window demoted by this
    function is corrected on every read but never rewritten to disk, because
    nothing is owed and `run_pull` therefore writes no manifest. `manifest.json`
    can go on saying `satisfied` while every read says otherwise. **The read route
    is not silent** — `pull_status` corroborates, so `/api/search` does report
    `missing`; only the file on disk is stale. Whoever picks that up should know it
    now fires on one damaged page rather than on all of them.

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
    window = store.window(status.sig)
    if not status.satisfied or window.whole:
        return status
    return _demoted(status.label, status.sig, window)


def _demoted(
    label: str, sig: str, window: _WindowState, entry: _WindowState | None = None
) -> QueryStatus:
    """An open window, priced by whether its missing evidence is gone or merely
    unreadable — the asymmetry `_corroborate` documents, stated once so the read
    path and `_reconcile` cannot drift apart about it.

    **The advice in the unreadable message is the fix for a message that could
    not be followed.** It used to say "delete this cache entry and search
    again", which (a) named no path and is something no code in `backend/src`
    does, (b) destroys every other page in the entry — 500 readable records in
    the shape the gate fixture takes — and (c) on the likeliest cause, a schema
    change, spends a whole pull to recover bytes that were already fine. One file
    stopped the walk; that file is what the message names.

    `entry` (defaulting to `window`) is what the record count is quoted from. The
    two differ only under a REFRESH, where `window` counts just the pages this
    run bought — so a message written off it would describe the run while the
    reader takes it to describe the entry, and `/api/derive` would be shaping
    comps from pages the message said were not there.
    """
    entry = entry if entry is not None else window
    if window.next_offset is None:
        return QueryStatus(
            label=label,
            sig=sig,
            satisfied=False,
            owed=False,
            error=(
                f"this window is holding {_blocked_name(window)}, which cannot be read as "
                f"listings ({_evidence_count(entry)}). It was paid for and re-fetching "
                "returns the same bytes, so no call is owed for it. If that one file is "
                "damaged rather than the response — a directory or a locked file standing "
                "where it belongs — clearing it is enough; every other page of this pull is "
                "intact and is still being used."
            ),
        )
    return QueryStatus(
        label=label,
        sig=sig,
        satisfied=False,
        owed=True,
        error=(
            f"the evidence that answered this window is no longer complete on disk "
            f"({_evidence_count(entry)}), so the window is open again from offset "
            f"{window.next_offset}{_also_blocked(window)}"
        ),
    )


def _also_blocked(window: _WindowState) -> str:
    """The second half of a window that is BOTH short a page and blocked by one.

    A buyable gap and an unreadable page can coexist, and the buyable one now
    wins `next_offset` (see the walk). Saying only "open again from offset 0"
    would send a user to buy one page and leave them without the reason the
    window still will not settle afterwards.
    """
    if window.blocked is None or window.blocked.path is None:
        return ""
    return (
        f". Separately, `{window.blocked.path.name}` is on disk and cannot be read as "
        "listings; that page was paid for and no call is owed for it"
    )


def _blocked_name(window: _WindowState) -> str:
    """The single file that stopped the walk, by name — never a bare "a response".

    Named rather than counted because the user's only useful action is against
    one file, and the widened blast radius makes that concrete: satisfaction is
    now per page, so a *transient* lock on page 2 of 3 (antivirus, OneDrive)
    demotes a window that a page-agnostic rule left alone. Telling that user to
    delete the entry would turn a lock that clears itself in seconds into real
    data loss.
    """
    blocked = window.blocked
    if blocked is None or blocked.path is None:  # pragma: no cover - defensive
        return "a response this pull cannot read"
    return f"`{blocked.path}`"


def _evidence_count(window: _WindowState) -> str:
    """"4 of 6 records readable" — how much of this window's evidence survives.

    Counted over **every** page, gaps included, because this is the number a user
    sees and `/api/derive` shapes its comps from the same pile. Quoting the
    walk's own `fetched` (which stops at whatever ended the walk) told the user
    "2 of 6 readable" while the loader was shaping 4 — two different answers to
    one question about one set of files, which is the failure NORTH_STAR puts
    first.
    """
    promised = window.total_count if window.total_count is not None else "an unknown number of"
    return f"{window.readable} of {promised} records readable"


# ---------------------------------------------------------------------------
# one query
# ---------------------------------------------------------------------------


def _fetch_one(
    key: str,
    query: PlannedQuery,
    today: date,
    transport: httpx.BaseTransport | None,
    *,
    restart: bool,
    mode: Mode,
) -> tuple[QueryStatus, bool]:
    """Fetch what one planned window is missing, one PAGE at a time.

    Returns `(status, arrived)`, where `arrived` is True when at least one
    response body reached the store during this call — the ONE signal that
    moves the manifest's `as_of` (see the module docstring). It is reported
    from the writer's own answer rather than inferred from a file listing,
    because a forced refresh rewrites the same filenames and would be
    invisible to any comparison of what is held.

    **One call per gap, and the gap is re-read off disk between calls** (queue
    row 13c). Handing the whole window to `fetch_listings` and letting it
    paginate forward would re-buy every page after the first missing one — three
    calls where one page was lost, on a 50-call month — so each turn asks the
    store what the window is short, buys exactly that page, and asks again. A
    window with nothing missing costs nothing and never constructs a client.

    A failure is returned, never raised: §5a's "incomplete first pull →
    usable, with the gap named loudly". Raising here would throw away the
    windows that did arrive — and on the live path, throw away calls that were
    already paid for.

    Args:
        restart: ignore the pages already on disk and re-buy the window from its
            first page. This is the user's REFRESH (`force_refresh`) and nothing
            else — "go buy it again" rather than "finish what I paid for". The
            old pages are overwritten per call and never deleted, so a refresh
            that fails part way still leaves the evidence it was replacing.
        mode: which mode this pull resolved to, passed in rather than re-read so
            one pull cannot change its mind halfway. It answers exactly one
            question: **was a call billed for this attempt?** In fixture mode
            none ever is, and that is what separates "we are holding an answer we
            cannot use" (not owed) from "the saved response is unreadable and
            nothing was filed" (owed) — see `_verdict`.
    """
    base = _sig(query)
    bought: set[int] = set()
    attempted: set[int] = set()

    def window(*, this_run_only: bool) -> _WindowState:
        only = bought if (restart and this_run_only) else None
        return _window_state(_held_pages(key, base, only=only))

    while True:
        state = window(this_run_only=True)
        # Nothing left worth buying: the window is whole, or it is holding bytes
        # it cannot read (paid for, and a re-fetch returns the same ones), or it
        # is short of a page it has already asked for once in this run — asking
        # again here would be a purchase per iteration with no stopping
        # condition, which is the shape D24 treats as different in kind.
        if state.whole or state.next_offset is None or state.next_offset in attempted:
            return _verdict(query, base, state, None, spent=False, entry=window(this_run_only=False)), bool(bought)
        attempted.add(state.next_offset)

        landed, failure = _fetch_page(key, base, query, state.next_offset, today, transport)
        bought |= landed
        if failure is not None:
            # Re-read before judging: a response that could not be parsed is
            # filed on the way past, so what this window now holds is a fact
            # about disk rather than about the exception.
            return (
                _verdict(
                    query,
                    base,
                    window(this_run_only=True),
                    failure,
                    spent=mode is Mode.LIVE,
                    entry=window(this_run_only=False),
                ),
                bool(bought),
            )


def _verdict(
    query: PlannedQuery,
    base: str,
    state: _WindowState,
    failure: Exception | None,
    *,
    spent: bool,
    entry: _WindowState,
) -> QueryStatus:
    """One window's status, read off its page state.

    The single triage both the top of `_fetch_one`'s loop and its failure branch
    go through, so the two cannot answer the same disk state two different ways —
    the drift class that failed F3-S4 (one rule for the fetch path, another for
    the read path) reappearing inside one function.

    **A BILLED response this parser cannot read is never owed, whatever reached
    the store.** Pricing that case off the store alone was wrong, and measurably
    so: a 200 with a **zero-byte body** is refused by `write_raw_response`
    (`if not raw: return None`), so nothing is filed, the store reports the page
    absent, and the window was bought again on every attempt — measured
    `[5, 1, 1]`, one call per attempt indefinitely. The store can only ever say
    *absent*; only the exception says *absent because the server answered 200
    with nothing and will answer the same way next time*, which is what
    `ResponseUnusableError` exists to carry ("the one failure where the call
    succeeded").

    `spent` is what keeps that from over-reaching. In fixture mode the same
    exception means an unreadable **saved** response with no call behind it and
    nothing on disk, and F3-S4 pins that as owed — the not-owed answer is only
    available *because* the money is already gone.

    Args:
        state: what this run may count (a REFRESH counts only its own pages).
        entry: what the cache entry actually holds, used for messages only. A
            refresh whose first page fails has an empty `state` while the entry
            is full of pages `/api/derive` will happily shape, so a message
            written off `state` would tell the user nothing is on disk while the
            screen showed comps derived from it.
    """
    if state.whole:
        return QueryStatus(label=_label(query), sig=base, satisfied=True)
    if spent and isinstance(failure, ResponseUnusableError):
        return _unreadable_answer(query, base, entry, failure)
    if state.next_offset is None:
        return _demoted(_label(query), base, state, entry)
    return _short(query, base, state, failure, entry=entry)


def _unreadable_answer(
    query: PlannedQuery, base: str, entry: _WindowState, exc: Exception
) -> QueryStatus:
    """A 2xx that was billed and that this parser cannot read: missing, not owed.

    Its own function rather than a branch of `_demoted` because only this path
    knows *why* — a schema change and a damaged file need opposite actions from
    the user — and because the response may have reached the store (an
    unrecognised envelope) or not (a zero-byte body). The price is the same
    either way: the money is gone and the next call returns the same answer.
    """
    return QueryStatus(
        label=_label(query),
        sig=base,
        satisfied=False,
        owed=False,
        error=(
            f"the response arrived and could not be read: {exc}. The call was billed and "
            "re-fetching returns the same answer, so no call is owed for it. This window's "
            f"evidence is what it was: {_evidence_count(entry)}."
        ),
    )


def _fetch_page(
    key: str,
    base: str,
    query: PlannedQuery,
    offset: int,
    today: date,
    transport: httpx.BaseTransport | None,
) -> tuple[set[int], Exception | None]:
    """Buy ONE page of a window and keep it, whatever else goes wrong.

    Returns `(offsets filed by this call, the failure if there was one)`. Both
    at once and deliberately: a `ResponseUnusableError` is raised *after* the
    sink has already filed the bytes, and an interruption between pages leaves
    the earlier ones paid for — D24 is unconditional about them.

    **Every failure is returned untyped, and the price is read off the store
    instead.** F3-S4 needed a typed catch here (`ResponseUnusableError` → filed,
    so not owed; anything else → owed) because the fetch path was the only place
    that knew which had happened. It is not any more: `_fetch_one` re-reads the
    window's pages after every attempt, so "we are holding bytes we cannot use"
    is answered by the same `records_from_raw` the loader uses, on the same
    files, rather than by a second rule that could disagree with it (queue row
    13e). The exception's *message* is still carried, because only it can say
    whether a schema change or a damaged file is the reason.
    """
    landed: set[int] = set()

    def sink(params: dict, raw: bytes, meta: dict) -> None:
        """D24 write-through. Called by `RentCastClient` per call, with the
        body exactly as it arrived, BEFORE anything parses it."""
        page_offset = _offset_of(meta)
        if write_raw_response(key, _page_sig(base, page_offset), raw, meta=_meta(meta, today)):
            landed.add(page_offset)

    client = RentCastClient(transport=transport, sink=sink)
    try:
        # `max_calls=1` is what makes this a PAGE fetch rather than a window
        # fetch: the client would otherwise paginate on past the gap and re-buy
        # pages this pull is already holding.
        result = client.fetch_listings(_page_request(query.params, offset), max_calls=1)
    except (RentCastError, OSError) as exc:
        # A non-2xx, a transport failure, a missing fixture, the budget — or the
        # store refusing the bytes (a directory in the way, a full disk). One
        # window's failure must not lose the windows already filed under this
        # ref, so it is reported rather than raised.
        return landed, exc

    # Fixture mode never reaches the sink (`_fetch_from_fixtures` returns
    # without emitting), so its page is written here instead. Keyed on the
    # page's own offset rather than on the mode, so neither path can
    # double-write and neither can be forgotten.
    try:
        for page in result.pages:
            if page.offset in landed:
                continue
            if write_raw_response(
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
            ):
                landed.add(page.offset)
    except OSError as exc:
        return landed, exc
    return landed, None


def _page_request(params: dict, offset: int) -> dict:
    """One page's params. `offset` is absent on page 0 so the request hashes to
    the same fixture signature the gate's un-offset call did — the same rule
    `client/rentcast.py::_page_params` applies on the wire, restated here
    because fixture mode looks its response up from these params directly."""
    page = {key: value for key, value in params.items() if key != "offset"}
    if offset:
        page["offset"] = offset
    return page


def _held_pages(
    key: str, base: str, *, only: set[int] | None
) -> dict[int, _StoredPage]:
    """One window's page index, read back off disk.

    Re-read rather than carried in memory, so that what this pull believes it
    holds and what the loader can shape are the same fact — the property queue
    row 13e made binding. `only` restricts it to the offsets THIS run bought,
    which is what a REFRESH means.
    """
    pages = _stored_responses(key).pages.get(base) or {}
    return pages if only is None else {
        offset: page for offset, page in pages.items() if offset in only
    }


def _short(
    query: PlannedQuery,
    base: str,
    state: _WindowState,
    exc: Exception | None,
    *,
    entry: _WindowState,
) -> QueryStatus:
    """A window still owed pages, recorded with the reason §5a asks for and the
    price §5a's resume will actually pay.

    The reason carries the exception's own message, so a 429 ("wait") and a 500
    ("retry") stay the different instructions they are — and the offset, so the
    record says which call would close the gap rather than only that one would.

    `entry` rather than `state` decides whether "nothing of this window is on
    disk" may be said, because the two differ exactly once: under a REFRESH,
    `state` counts only the pages this run bought, so a refresh whose first page
    failed has an empty `state` over an entry still full of pages that
    `/api/derive` will shape into comps. Claiming nothing was on disk there is
    a statement the screen contradicts.
    """
    if not entry.filed and exc is not None:
        # Nothing of this window is on disk: the failure IS the whole story, and
        # dressing it up as a truncation would bury the reason.
        return QueryStatus(
            label=_label(query),
            sig=base,
            satisfied=False,
            owed=True,
            error=str(exc),
        )
    truncation = (
        f"the response was truncated: {_evidence_count(entry)}, so the window is open "
        f"again from offset {state.next_offset}"
    )
    return QueryStatus(
        label=_label(query),
        sig=base,
        satisfied=False,
        owed=True,
        error=truncation if exc is None else f"{truncation} — the next page did not arrive: {exc}",
    )


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
            # rebuild an index is the double-pay D24 forbids — so the index is
            # rebuilt from the pages themselves, whole ones and partial ones
            # alike (queue row 13e: index and store move together).
            window = store.window(sig)
            if window.whole:
                known[sig] = QueryStatus(label=_label(query), sig=sig, satisfied=True)
            elif window.filed:
                known[sig] = _demoted(_label(query), sig, window)
            continue
        known[sig] = _corroborate(status, store)
    return known


@dataclass(frozen=True)
class _StoredPage:
    """One CALL's worth of a window, as it survives on disk.

    `records` is `None` for a file that is present and is **not** evidence — a
    body truncated by a crash, emptied, or of a shape `records_from_raw` cannot
    read. Present is not usable, and a pull that counted it would report itself
    whole over bytes nothing can be derived from. The line is drawn by
    `read_raw_records`, in one place, so what this module calls held and what
    `storage/pulls.py` can actually shape are the same set by shared code.

    `total_count` is what *this* call's `X-Total-Count` promised, recorded in
    the sidecar at write time. It is the only durable statement that more of a
    window exists than one page holds, and therefore the only thing that can
    make an absent page worth a call rather than a guess.
    """

    offset: int
    records: int | None
    total_count: int | None
    #: Could this page's SIDECAR be read at all? `False` means `total_count` is
    #: unknown rather than absent, and the two must never collapse — see
    #: `_window_state`'s lost-total clause, which is the whole reason this field
    #: exists.
    indexed: bool = True
    #: Where this page lives, so a message about an unreadable one can name the
    #: single file to clear instead of telling the user to delete the entry.
    path: Path | None = None


@dataclass(frozen=True)
class _WindowState:
    """What one window's page set adds up to — §5a's three answers, per query.

    * `whole` — every page the evidence itself accounts for is on disk and
      readable. This is "satisfied", and it is a claim about pages, not about a
      signature having *something* under it.
    * `next_offset` — the offset a resume must ask for first, or `None` when no
      call would deliver anything this pull does not already hold.
    * `filed` — any response file at all under this window.
    * `lost_total` — a page on disk whose sidecar cannot be read, with no other
      page able to supply the window's total. The state that makes "whole"
      undecidable; see the walk.
    * `readable` — records readable across **every** page, gaps included. This
      is the loader's number, and the one a user-facing message must quote:
      `fetched` stops at whatever ended the walk, so quoting it told the user
      "2 of 6 readable" while `/api/derive` was shaping 4 — two numbers for one
      pile of evidence, which is NORTH_STAR's core failure mode.

    Together these are F3-S4's pricing, one level down: a page that is **gone**
    is worth a call; a page that is **there and unreadable** is not, because
    re-fetching returns the same bytes and the loop would have no stopping
    condition. Queue row 13g is the row that may revisit that asymmetry by
    recording a digest at write time; until then both levels price it the same
    way, deliberately.
    """

    filed: bool
    whole: bool
    next_offset: int | None
    fetched: int
    readable: int
    total_count: int | None
    lost_total: bool = False
    #: The page that stopped the walk, when one did — what `_demoted`'s message
    #: names so the advice is "clear this file", never "delete this entry".
    blocked: _StoredPage | None = None


@dataclass(frozen=True)
class _StoredResponses:
    """A cache entry's `raw/` directory, indexed by query and then by page."""

    pages: dict[str, dict[int, _StoredPage]]

    def window(self, sig: str) -> _WindowState:
        return _window_state(self.pages.get(sig) or {})


def _stored_responses(key: str) -> _StoredResponses:
    """The page index, in one pass over the entry's raw responses.

    Grouped by the **filename's** query signature (`_query_sig_of`), because
    that is the one identifier both write paths agree on — see the module
    docstring on the sidecar's `sig`.

    A path with **no bytes behind it** is skipped rather than counted as filed:
    nothing was ever kept there, so the page is absent, not unreadable, and a
    call really would deliver something this pull does not have. `_has_bytes`
    draws that line — see it for why `is_dir()` alone cannot.
    """
    pages: dict[str, dict[int, _StoredPage]] = {}
    for path in raw_response_paths(key):
        if not _has_bytes(path):
            continue
        records = read_raw_records(path)
        meta = read_raw_meta(path)
        offset = _page_offset(meta, path.stem)
        page = _StoredPage(
            offset=offset,
            records=None if records is None else len(records),
            total_count=_total_of(meta),
            # `read_raw_meta` returns `{}` for a sidecar that is absent, corrupt
            # or obstructed. Nothing this module writes is empty (`_meta` always
            # adds `as_of`), so `{}` means "the index for this page is gone",
            # which is a different fact from "the server reported no total".
            indexed=bool(meta),
            path=path,
        )
        window = pages.setdefault(_query_sig_of(path.stem), {})
        held = window.get(offset)
        # Two files claiming one offset can only happen for files this module did
        # not write, and the readable one must win: letting a garbage-keyed file
        # overwrite a real page would open a gap where the bytes are intact and
        # buy them again — the double-pay `_reconcile` exists to prevent.
        if held is None or (held.records is None and page.records is not None):
            window[offset] = page
    return _StoredResponses(pages=pages)


def _has_bytes(path: Path) -> bool:
    """Is there a response body at this path — anything at all a parse could try?

    **`is_dir()` is not this question, and using it as though it were left two
    holes.** A dangling symlink and a path whose `stat` fails both make
    `is_dir()` return `False` (it swallows `OSError`), so both were read as
    "a response is filed here and cannot be parsed" — missing, and never owed —
    when in fact no byte was ever kept and a call would deliver something new.

    So the question is asked of `stat` directly, and each answer priced by
    whether a re-fetch can land:

    * a regular file (empty or not) — bytes are filed here. Whether they parse
      is `read_raw_records`' business; either way they were paid for.
    * an **empty** directory — debris, and `storage/cache.py::_clear_directory_
      obstruction` will `rmdir` it, so the recovery write succeeds and the window
      settles. Absent, and owed.
    * a **NON-empty** directory — the writer refuses to touch it by design ("a
      non-empty one is something this code has no business deleting"), so a call
      can never land there. Treated as filed-and-unreadable, which prices it at
      zero: measured as owed, it was bought once per attempt with no stopping
      condition, which is the shape that empties a 50-call month.
    * anything `stat` cannot answer for — no bytes we can vouch for, and
      `os.replace` will almost always land, so absent and owed.
    """
    try:
        entry = path.stat()
    except OSError:
        return False
    if not stat.S_ISDIR(entry.st_mode):
        return True
    try:
        return any(path.iterdir())
    except OSError:
        # A directory we cannot even list is one the writer certainly cannot
        # clear, so it is an obstruction rather than debris.
        return True


def _window_state(pages: dict[int, _StoredPage]) -> _WindowState:
    """Walk a window's pages the way the client paginated them, and price the
    gaps.

    The same arithmetic `client/rentcast.py::_fetch_live` uses to decide it has
    the whole set, run backwards over what is on disk: start at 0, advance by
    the records each page actually carried, and stop on a recorded total, on an
    empty page, or on a page **shorter than `MAX_LIMIT`** when no total was ever
    recorded. Sharing the rule is the point — a walk that computed "whole"
    differently from the loop that fetched it would either re-open a complete
    window on every attempt or, worse, call an incomplete one whole.

    ⚠ **That last clause is load-bearing and was missing.** Measured without it:
    500 records at offset 0 and no `X-Total-Count` read as a whole window, so a
    pull holding 500 of 503 reported `complete=True, missing=(), calls_to_
    complete=0` — this row's own defect, relocated and worse, because there was
    not even a `missing` entry to act on. `MAX_LIMIT` is the right constant and
    not a guess: `client/query.py::build_listing_params` sends `limit=MAX_LIMIT`
    on every request this product makes, and `_fetch_live` caps it there, so a
    page of exactly that size is exactly the case where the client itself would
    have asked for another.

    ⚠⚠ **"No total was reported" and "the total was reported and then lost" are
    not the same state, and collapsing them was wrong in BOTH directions.** They
    reach this walk as the same `total_count is None`, so the short-page test
    above decided a question it had no standing to answer:

    * a page **at** `MAX_LIMIT` with its total lost → the walk invented a gap and
      asked for a page on nothing better than the page's length; and
    * a page **short of** `MAX_LIMIT` with its total lost → the walk declared the
      window **whole**. Measured: 250 records on disk, `X-Total-Count: 690` on the
      wire, sidecar unreadable → `complete=True, missing=(), calls_to_complete=0`
      with **440 paid-for records gone and no label naming them**. That is worse
      than the defect this row exists to fix, which at least left `missing`
      non-empty.

    So `_StoredPage.indexed` records whether the sidecar could be read at all,
    and a page whose sidecar is gone contributes `lost_total` rather than a
    silent `None`. The rule that follows is the one both reviewers arrived at
    independently: **only a total we can still account for may settle the
    question.** If another page proved a total, use it (that is the common
    recovery, and why an obstruction beside a multi-page window is harmless). If
    none did, the window is not whole and the short-page test does not run —
    neither direction of the old guess is available.

    ⚠ **It is not specific to a failed write.** Corrupting a sidecar reproduces
    the identical false-`whole`, so the fragility was the short-page heuristic
    itself; `storage/cache.py`'s swallowed sidecar error only made an ordinary
    `OSError` a silent trigger for it.

    **A gap is otherwise recorded only when something on disk says more records
    exist** — a recorded total above the position reached, or a page filed at a
    *higher* offset (which cannot exist without the pages before it). Without
    that guard an ordinary single-page window would look like page 0 of a longer
    set and be bought again forever.

    ⚠ **The short-page test uses `MAX_LIMIT` and the gap arithmetic does not; do
    not "unify" them.** They are different questions: whether a page just
    *received* was short is about that page, while a gap the client will *fill*
    is filled at the limit this product sends. One constant serving both would
    be wrong for one of them.

    **The walk does not stop at the first gap, because the price depends on
    what lies beyond it.** A window missing its middle page holds the pages on
    both sides, so exactly one call finishes it; a walk that stopped at the gap
    would price the whole remainder (two calls where one page was lost, on a
    50-call month). Each gap is measured against the next page actually held,
    and converted to calls at the largest page size this window has been seen to
    return — RentCast pages are uniform except the last.
    """
    readable = sum(page.records for page in pages.values() if page.records is not None)
    if not pages:
        return _WindowState(
            filed=False, whole=False, next_offset=0, fetched=0, readable=0, total_count=None
        )

    above = sorted(pages)
    offset = 0
    fetched = 0
    #: Records in the page the walk read LAST, which is what decides whether the
    #: set ended (see the short-page clause). Starts at 0 so a window whose page 0
    #: is absent does not read as "the last page was full".
    last_records = 0
    total: int | None = None
    unindexed: list[_StoredPage] = []
    gaps: list[tuple[int, int]] = []
    blocked: _StoredPage | None = None

    while True:
        page = pages.get(offset)
        if page is None:
            beyond = next((held for held in above if held > offset), None)
            if beyond is not None:
                # A page sits above this hole, so the hole is real evidence that
                # is gone — and it is bounded by the page that follows it.
                gaps.append((offset, beyond))
                offset = beyond
                continue
            if total is not None:
                if offset < total:
                    gaps.append((offset, total))
            elif unindexed:
                # The window's total is UNKNOWN, not absent: a page is on disk
                # whose sidecar cannot be read, and no other page proved a total
                # either. See the clause in the docstring — the short-page test
                # below may not be applied here, in either direction.
                gaps.append((offset, offset + 1))
            elif last_records >= MAX_LIMIT:
                # No total was ever reported and the LAST page came back full, so
                # the client itself would have asked for one more (spec §3.2).
                #
                # ⚠ The last page, never the largest. Judged on the maximum, a
                # window whose first page was full and whose second was short read
                # as "still going", and the walk asked for a page past the end of
                # the set on every iteration — measured 50 calls, offsets 500
                # through 596, i.e. the entire month.
                gaps.append((offset, offset + 1))
            break
        if page.records is None:
            # Paid for, filed, and not evidence. The walk cannot pass it — its
            # record span is unknown — so it stops here and names the file that
            # stopped it. Its total is harvested FIRST: a blocked page's sidecar
            # is usually intact and is often the only thing that knows how large
            # the window is (measured otherwise as "2 of an unknown number of
            # records readable" over two sidecars both recording 6).
            if page.total_count is not None:
                total = page.total_count
            blocked = page
            break
        if page.total_count is not None:
            total = page.total_count
        elif not page.indexed:
            unindexed.append(page)
        fetched += page.records
        last_records = page.records
        if page.records == 0 or (total is not None and offset + page.records >= total):
            # An empty page ends the set (the client's own defensive rule), and so
            # does reaching the total the window promised.
            break
        offset += page.records

    return _WindowState(
        filed=True,
        whole=blocked is None and not gaps,
        # ⚠ A blocked page does NOT annihilate the gaps below it. It used to:
        # with page 0 deleted and page 1 emptied, `next_offset` came back `None`
        # and page 0 — genuinely gone, genuinely buyable — became unreachable
        # through the product, which is the exact outcome this row exists to
        # eliminate. Worse, the trigger is transient (a OneDrive or antivirus
        # lock on page 1), so whether the owner could recover paid-for evidence
        # turned on a file lock.
        next_offset=gaps[0][0] if gaps else None,
        fetched=fetched,
        readable=readable,
        total_count=total,
        lost_total=total is None and bool(unindexed),
        blocked=blocked,
    )


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
    """The offset the CLIENT just reported, for naming the file it landed in.

    Strict, and 0 only as a last resort: this number picks the filename, so a
    value read loosely could file a page on top of one already paid for. Nothing
    the client emits is anything but a non-negative `int`.
    """
    offset = _strict_offset(meta.get("offset"))
    return offset if offset is not None else 0


def _page_offset(meta: dict, stem: str) -> int:
    """Which page of its window a stored response is — the FILENAME first, the
    sidecar as fallback.

    **This ordering is the whole point and it used to be the other way round.**
    The offset is the page index's key, so reading it loosely is how a corrupt
    sidecar destroys a good index: measured with one sidecar's `offset` set to
    `"not-a-number"`, the loose read swallowed the error, returned 0, collapsed
    that page onto the real page 0, and the pull then demanded a call for a page
    it was holding. The filename's `-offNNN` suffix is *derived* — `_page_sig`
    wrote it — so it is the trustworthy half; the sidecar is upstream-adjacent
    data and is read as strictly as `_total_of` reads the total beside it.

    The fallback still earns its keep: `write_raw_response` can leave a response
    whose sidecar never landed, and a file from another writer may carry no page
    marker at all. Such a name maps to page 0, which is exactly right — it is the
    whole of whatever it is.
    """
    from_name = _page_offset_of(stem)
    if from_name is not None:
        return from_name
    from_sidecar = _strict_offset(meta.get("offset"))
    return from_sidecar if from_sidecar is not None else 0


def _strict_offset(value: object) -> int | None:
    """A page offset out of untrusted data, or `None` when there isn't one.

    `bool` is excluded explicitly (`isinstance(True, int)` is `True`, and `True`
    would index a phantom page 1), as are negatives — a walk that starts at 0
    can never reach them, so they would silently hide a page rather than
    misplace it.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _page_offset_of(stem: str) -> int | None:
    """`y2025-inactive-off002` → 2, or `None` for a name without a page marker."""
    _base, marker, tail = stem.rpartition(_PAGE_MARKER)
    if not marker or not tail.isdigit():
        return None
    return int(tail)


def _total_of(meta: dict) -> int | None:
    """The `X-Total-Count` a call reported, out of its sidecar.

    The one durable statement that a window holds more than the page in front of
    us — so it is read strictly: a bool, a float, a string or a negative number
    is "not recorded", never a total to price a call against.
    """
    total = meta.get("total_count")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        return None
    return total
