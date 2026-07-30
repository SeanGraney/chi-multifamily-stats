"""Queue row 13c — the review round's findings, pinned (developer-authored).

Each test here is one finding from the two reviewer passes the PM adjudicated,
written so the *measurement* that found it is what fails if it comes back. They
live beside QA's suite because they need the same `RENTCOMP_HOME`, the same two
modes, and the same `no_sockets` money backstop, and because five of the six are
about disk states QA's harness does not produce.

WHY F1 IS HERE AND NOT ONLY IN QA'S FILE
-----------------------------------------
The PM's finding was that `test_a_page_resume_re_enters_at_the_offset_its_held_
pages_account_for`'s second assertion is **the only test in the suite pinning the
quote to the spend for a window that spans pages** —
`test_the_calls_to_complete_a_pull_is_what_completing_it_actually_costs` pins the
same property, but every window in it answers in one call, so it cannot see this.
That is right, and it is the reason the property is restated here in a form that
does not also require a single call: `quoted == sent`, whatever the number is.

MONEY (WORKFLOW.md §6): every test injects an `httpx.MockTransport`. Nothing here
can open a socket, and nothing here can spend.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from f3s4_support import SEARCH, TODAY, entry_dir, planned, record
from r13c_support import (
    PAGE_SIZE,
    PAGED_RECORD_IDS,
    SPANNING_TOTAL,
    Denied,
    Interrupted,
    Market,
    expected_comps,
    page_files,
    pages_holding,
    record_ids_on_disk,
)

from rentcomp.client.pull import (
    _corroborate,
    _page_sig,
    _sig,
    _stored_responses,
    pull_status,
    run_pull,
)
from rentcomp.client.rentcast import MAX_LIMIT
from rentcomp.storage.cache import read_manifest, write_manifest
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import load_ledger
from rentcomp.storage.pulls import load_shaped_pull

def _first_page_sig(offset: int) -> str:
    """The filename stem the spanning window's page at `offset` is filed under."""
    return _page_sig(_sig(planned()[0]), offset)


# ===========================================================================
# F1 — the consent quote and the spend are one number: NOT HERE, row 13j's
# ===========================================================================
#
# Two tests lived here — `quoted == sent` for a window spanning pages, and
# `_require_budget` refusing on calls rather than windows. They are removed with
# the machinery they exercised: pricing in calls is **queue row 13j**, QA-first,
# after a first attempt at it inside this row introduced four defects of its own.
#
# `backend-reviewer` endorsed the DIRECTION (§5a/D24: price in calls now that a
# window and a call have diverged), so 13j should keep it. The property worth
# lifting is `quoted == sent` asserted WITHOUT also requiring a single call —
# `test_the_calls_to_complete_a_pull_is_what_completing_it_actually_costs` pins it
# already but every window in it answers in one call, so it cannot see this.

# ===========================================================================
# F2 — a full page with no X-Total-Count is not a whole window
# ===========================================================================
#
# `client/rentcast.py::_fetch_live` stops paginating when a page comes back
# SHORTER than `limit` and no total was reported (spec §3.2). A walk with no
# `limit` cannot tell a short page from a full one, and the consequence is this
# row's own defect relocated: a window holding 500 of 503 records reporting
# itself whole, with no `missing` entry and no priced way back.

#: A minimal record — only the count matters here, and 500 shaped comps would
#: make the test slow for no extra assurance.
def _bare(n: int) -> dict:
    return {"id": f"r13c-f2-{n}"}


def _full_page_market(*, tail: httpx.Response):
    """The spanning window answers a FULL page at offset 0 with no total header,
    then `tail` for whatever is asked next."""
    windows = {
        (query.params.get("status"), query.params.get("daysOld")): index
        for index, query in enumerate(planned())
    }
    sent: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset")
        if windows[(request.url.params.get("status"), request.url.params.get("daysOld"))] != 0:
            return httpx.Response(200, json=[record(500)])
        sent.append(offset)
        if offset is None:
            return httpx.Response(200, json=[_bare(n) for n in range(MAX_LIMIT)])
        return tail

    return httpx.MockTransport(handle), sent


def test_a_full_page_with_no_total_count_is_never_a_whole_window(
    home, live_env, no_sockets
) -> None:
    """The measured lie: `complete=True, missing=(), calls_to_complete=0` over
    500 records of 503.

    No `X-Total-Count` means the size of the set is unknowable from the header,
    so the only evidence that more exists is that the page came back FULL — which
    is exactly why the client asks again. A pull that stops there is short by a
    quantity nobody can name, and says nothing.
    """
    transport, sent = _full_page_market(tail=httpx.Response(503, json={"error": "gone"}))

    outcome = run_pull(**SEARCH, today=TODAY, transport=transport)

    assert len(sent) >= 2, (
        f"the pull asked for offsets {sent} and stopped. A page of exactly {MAX_LIMIT} records "
        "with no total is the case the client itself would have paginated past."
    )
    assert outcome.complete is False, (
        "a window holding one FULL page and nothing else reported itself whole. There is no "
        "`missing` entry and no priced way back to the rest — worse than the lost-page defect "
        "this row exists for, because at least that one could be named."
    )
    assert outcome.missing, "the gap is not named, so F4-S6 has nothing to render"
    assert outcome.calls_to_complete >= 1, (
        "priced at 0 calls, the remaining records are unreachable through the product"
    )
    assert no_sockets == []


def test_a_short_final_page_with_no_total_count_completes_the_window(
    home, live_env, no_sockets
) -> None:
    """The other side of the same rule, so the fix cannot be "always ask again".

    A page shorter than `limit` IS the end of the set (spec §3.2). Reading it as
    "maybe there is more" would buy an empty page after every complete window,
    forever — one wasted call per window per attempt.
    """
    transport, sent = _full_page_market(
        tail=httpx.Response(200, json=[_bare(9001), _bare(9002)])
    )

    outcome = run_pull(**SEARCH, today=TODAY, transport=transport)

    assert outcome.complete is True, f"the window did not settle; offsets sent: {sent}"
    assert len(sent) == 2, (
        f"the pull sent {sent} — a page shorter than {MAX_LIMIT} ends the set, so a third "
        "call buys an empty answer and the loop has no stopping condition"
    )
    load_shaped_pull.cache_clear()
    denied = Denied("the window ended on a short page and is whole")
    again = run_pull(**SEARCH, today=TODAY, transport=denied.transport)
    assert denied.requests == [] and again.complete is True
    assert no_sockets == []


# ===========================================================================
# F3 — a BILLED response this parser cannot read is never owed
# ===========================================================================


@pytest.mark.parametrize(
    ("name", "body"),
    [
        pytest.param("a zero-byte body", b"", id="zero-byte 200"),
        pytest.param("a schema change", b'{"data": [{"id": "new"}]}', id="unknown envelope"),
    ],
)
def test_a_billed_response_that_cannot_be_read_is_not_bought_once_per_attempt(
    home, live_env, no_sockets, name, body
) -> None:
    """**The finding that refuted my own reasoning for removing the typed catch.**

    Pricing this off the store alone works for a body that HAS bytes and fails
    for the one that does not: `write_raw_response` refuses a zero-byte body
    (`if not raw: return None`), so nothing is filed, the store reports the page
    absent, and the window is owed again — measured `[5, 1, 1]`, one call every
    attempt, indefinitely.

    The store can only ever say *absent*. Only `ResponseUnusableError` says
    *absent because the server answered 200 with nothing and will answer the same
    way next time*, which is what its docstring is built on: the one failure
    where the call succeeded. Both parametrisations are billed 2xx bodies; only
    one of them reaches disk, and the price must be the same.
    """
    class _Interrupted(Market):
        def _page(self, offset: int) -> httpx.Response:
            if offset:
                return httpx.Response(200, content=body)
            return super()._page(offset)

    first = run_pull(**SEARCH, today=TODAY, transport=_Interrupted().transport)
    spent_once = load_ledger().calls_this_month

    assert first.complete is False, f"[{name}] a window that cannot be read is not whole"
    assert first.missing, f"[{name}] the unreadable window must still be named"
    assert first.calls_to_complete == 0, (
        f"[{name}] the pull quotes {first.calls_to_complete} call(s) for a response that was "
        "billed and returns the same answer every time. That quote can never come true, and "
        "the user pays it on every attempt."
    )

    for attempt in range(2, 5):
        load_shaped_pull.cache_clear()
        again = run_pull(**SEARCH, today=TODAY, transport=Denied(name).transport)
        assert again.calls_to_complete == 0
        assert load_ledger().calls_this_month == spent_once, (
            f"[{name}] attempt {attempt} spent again — no stopping condition, which is the "
            "shape that empties a 50-call month"
        )
    assert no_sockets == []


def test_an_unreadable_saved_response_with_no_call_behind_it_is_still_owed(
    home, fixture_mode, no_network
) -> None:
    """The guard that keeps F3's fix from over-reaching, restated at page level.

    Fixture mode raises the SAME exception for an unreadable saved response, and
    no call was billed for it — so F3-S4's rule stands: the not-owed answer is
    available only *because* the money is already gone. Read as not-owed here,
    the window would be quoted at zero calls forever with nothing on disk that
    could ever answer it.
    """
    for index, query in enumerate(planned()):
        from rentcomp.client.rentcast import fixture_signature

        body = b'[{"id": "truncated"' if index == 0 else json.dumps([record(index)]).encode()
        (fixture_mode / f"{fixture_signature(query.params)}.json").write_bytes(body)

    outcome = run_pull(**SEARCH, today=TODAY)

    assert outcome.complete is False
    assert outcome.calls_to_complete == 1, (
        "a window whose saved response cannot be read, and whose bytes were never filed, was "
        "quoted at 0 calls. Nothing on disk can answer it, so it is owed."
    )
    assert no_network == []


# ===========================================================================
# F5 — the "not owed" message names one file and destroys nothing
# ===========================================================================


def _spanning_error(ref: str, *, corroborate: bool = True) -> str:
    """The reason recorded against the spanning window.

    `corroborate=True` asks the same question `pull_status` asks — the manifest
    entry checked against the pages on disk — because damage applied AFTER a
    successful pull never reaches `manifest.json`: nothing is owed, so `run_pull`
    writes no manifest, and the recorded entry still says `satisfied` with no
    reason at all. The demoted reason is computed on every read, and this is that
    computation. `corroborate=False` reads what a run actually persisted.
    """
    sig = _sig(planned()[0])
    store = _stored_responses(ref)
    for query in read_manifest(ref).queries:
        if query.sig == sig:
            status = _corroborate(query, store) if corroborate else query
            return status.error or ""
    raise AssertionError(f"no manifest record for {sig}")


def test_an_unreadable_page_names_its_own_file_and_never_advises_deleting_the_entry(
    home, live_env, no_sockets
) -> None:
    """The only instruction the old message gave could not be followed.

    It said "delete this cache entry and search again" — which names no path,
    describes something no code in `backend/src` does, destroys every other page
    in the entry (500 readable records in the shape the gate fixture takes), and
    on the likeliest cause (a schema change) spends a whole pull to recover bytes
    that were fine. One file stopped the walk; that file is what a user can act
    on.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    assert outcome.complete is True
    middle = pages_holding(outcome.pull_ref, PAGED_RECORD_IDS[PAGE_SIZE])[0]
    blob = middle.read_bytes()
    middle.write_bytes(blob[: len(blob) // 2])
    load_shaped_pull.cache_clear()

    status = pull_status(outcome.pull_ref)
    error = _spanning_error(outcome.pull_ref)

    assert status.complete is False and status.calls_to_complete == 0
    assert "delete this cache entry" not in error, (
        f"the message still tells the user to destroy the entry: {error!r}. Following it "
        f"throws away the {len(page_files(outcome.pull_ref)) - 1} intact pages beside the "
        "damaged one, every one of them paid for."
    )
    assert middle.name in error, (
        f"the message names no file, so there is nothing to act on: {error!r}"
    )


def test_the_records_a_message_quotes_are_the_records_the_loader_can_shape(
    home, live_env, no_sockets
) -> None:
    """⚠ Two numbers for one pile of evidence is NORTH_STAR's first failure mode.

    The walk stops at the page that blocked it, so quoting its own running count
    told the user "2 of 6 records readable" while `/api/derive` was shaping 4 of
    them — and the smaller number was the one on screen. The count a message
    quotes has to be the count over every page, gaps included, because that is
    the pile the loader reads.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    middle = pages_holding(outcome.pull_ref, PAGED_RECORD_IDS[PAGE_SIZE])[0]
    middle.write_bytes(b"")
    load_shaped_pull.cache_clear()

    error = _spanning_error(outcome.pull_ref)
    survivors = len(record_ids_on_disk(outcome.pull_ref) & set(PAGED_RECORD_IDS))
    shaped = len(load_shaped_pull(outcome.pull_ref, Config()).comps)

    assert f"{survivors} of {SPANNING_TOTAL} records readable" in error, (
        f"the message quotes a different number from the {survivors} records still readable "
        f"in this window: {error!r}"
    )
    assert shaped == expected_comps() - PAGE_SIZE, (
        "the loader is not shaping what survived, so this test is not comparing the two "
        "numbers it means to compare"
    )


# ===========================================================================
# F6 — "no bytes here" is not the same question as "is this a directory"
# ===========================================================================


@pytest.mark.parametrize(
    ("shape", "populate", "first_run_calls"),
    [
        pytest.param("an EMPTY directory", False, 1, id="empty directory is debris"),
        pytest.param("a NON-EMPTY directory", True, 0, id="non-empty directory is an obstruction"),
    ],
)
def test_a_directory_where_a_page_belongs_settles_either_way(
    home, live_env, no_sockets, shape, populate, first_run_calls
) -> None:
    """Empty and non-empty are opposite prices, and only one of them was right.

    `storage/cache.py::_clear_directory_obstruction` will `rmdir` an EMPTY
    directory, so a recovery call lands and the window settles — owed, once. It
    refuses a NON-EMPTY one by design ("something this code has no business
    deleting"), so a call can never land there: measured as owed, it was bought
    once per attempt with no stopping condition — `[1, 1, 1]` and counting.

    Both must settle. The assertion is the same for both shapes: after the first
    attempt, no further attempt may spend anything.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    assert outcome.complete is True
    target = pages_holding(outcome.pull_ref, PAGED_RECORD_IDS[0])[0]
    survivors = record_ids_on_disk(outcome.pull_ref) - set(PAGED_RECORD_IDS[:PAGE_SIZE])
    target.unlink()
    target.mkdir()
    if populate:
        (target / "someone-elses-file.txt").write_text("do not delete me", encoding="utf-8")
    load_shaped_pull.cache_clear()

    recovery = Market()
    run_pull(**SEARCH, today=TODAY, transport=recovery.transport)
    assert recovery.calls == first_run_calls, (
        f"[{shape}] the recovery spent {recovery.calls} call(s) ({recovery.sent}); "
        f"{first_run_calls} is what a write to this path can actually accomplish"
    )

    for attempt in (2, 3):
        load_shaped_pull.cache_clear()
        settled = Denied(f"{shape} — attempt {attempt}")
        run_pull(**SEARCH, today=TODAY, transport=settled.transport)
        assert settled.requests == [], (
            f"[{shape}] attempt {attempt} reached for a call. A window that cannot be "
            "completed must be stuck and FREE, never bought once per attempt (D24)."
        )
    assert record_ids_on_disk(outcome.pull_ref) >= survivors, (
        f"[{shape}] the recovery removed evidence that survived the damage"
    )
    if populate:
        assert (target / "someone-elses-file.txt").exists(), (
            "the recovery deleted a non-empty directory's contents, which this code has no "
            "business doing"
        )
    assert no_sockets == []


def test_a_path_with_no_bytes_behind_it_is_absent_rather_than_unreadable(
    home, live_env, no_sockets
) -> None:
    """`is_dir()` swallows `OSError` and answers `False`, so it cannot be the
    "are there bytes here" test on its own.

    A path that `stat` cannot answer for was therefore read as "a response is
    filed here and cannot be parsed" — missing, and never owed — when no byte was
    ever kept and `os.replace` would land. Probed with a name that resolves to
    nothing, which is the reachable member of that family.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    target = pages_holding(outcome.pull_ref, PAGED_RECORD_IDS[0])[0]
    target.unlink()
    # A sidecar with no response beside it: the shape `write_raw_response` leaves
    # when the raw write fails, and one that must read as "the page is gone".
    load_shaped_pull.cache_clear()

    status = pull_status(outcome.pull_ref)

    assert status.complete is False
    assert status.calls_to_complete == 1, (
        f"a page with no bytes behind it was priced at {status.calls_to_complete} call(s). A "
        "call really does deliver something this pull does not have."
    )
    assert no_sockets == []


# ===========================================================================
# F7 — a sidecar that cannot be written does not un-write the response
# ===========================================================================


def test_a_response_whose_sidecar_cannot_be_written_still_moves_the_evidence_date(
    home, live_env, no_sockets
) -> None:
    """`as_of` is the pipeline's only "now", and `effective_dom = as_of − listed`.

    Measured: obstruct only the sidecar path and the raw write still succeeds
    (543 bytes on disk) while `write_raw_response` raises, so `arrived` came back
    False and `as_of` stayed ten days behind — every censored comp in the entry
    silently under-reporting vacancy, while the outcome said `complete=True`.

    The bytes are the paid-for thing; the sidecar is an index over them.
    """
    earlier = TODAY - timedelta(days=10)
    first = run_pull(**SEARCH, today=TODAY, transport=Interrupted().transport)
    assert first.complete is False, "the setup did not hold: the interrupted pull completed"
    # The plan's `daysOld` is a function of `today`, so both runs must use one
    # date for the harness to recognise its own windows. The earlier observation
    # date is therefore planted in the manifest rather than pulled at — which is
    # the same disk state a pull ten days ago would have left.
    recorded = read_manifest(first.pull_ref)
    write_manifest(
        first.pull_ref,
        as_of=earlier,
        window=recorded.window,
        planned=recorded.planned,
        fetchable=recorded.fetchable,
        queries=recorded.queries,
    )
    load_shaped_pull.cache_clear()

    # Obstruct ONLY the sidecar of the page the resume is about to buy.
    raw_dir = entry_dir(home, first.pull_ref) / "raw"
    blocked = raw_dir / f"{_first_page_sig(PAGE_SIZE)}.meta.json"
    blocked.mkdir()
    (blocked / "not-ours.txt").write_text("x", encoding="utf-8")

    second = run_pull(**SEARCH, today=TODAY, transport=Market().transport)

    landed = raw_dir / f"{_first_page_sig(PAGE_SIZE)}.json"
    assert landed.is_file() and landed.read_bytes(), (
        "the response whose sidecar could not be written was not kept. It was billed, and "
        "D24 is unconditional about the bytes."
    )
    assert read_manifest(second.pull_ref).as_of == TODAY, (
        f"as_of stayed at {read_manifest(second.pull_ref).as_of} while a response arrived on "
        f"{TODAY}. Every censored comp's effective_dom is measured from as_of, so a frozen "
        "stamp under-reports vacancy for evidence that really was re-observed."
    )
    assert no_sockets == []


# ===========================================================================
# F8 — a failed REFRESH does not claim the entry is empty
# ===========================================================================


def test_a_failed_refresh_never_says_nothing_is_on_disk_over_pages_it_still_holds(
    home, live_env, no_sockets
) -> None:
    """A REFRESH counts only the pages it bought itself, which is right for
    deciding what to buy and wrong for describing the entry.

    With the first page failing, the run's own view is empty — and the recorded
    reason then read as "nothing of this window is on disk" while `/api/derive`
    went on shaping comps from the pages that were still there. A message the
    screen contradicts is worse than no message.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    assert outcome.complete is True
    held = len(record_ids_on_disk(outcome.pull_ref) & set(PAGED_RECORD_IDS))
    load_shaped_pull.cache_clear()

    refused = Market()
    refused.transport = httpx.MockTransport(
        lambda request: httpx.Response(503, json={"error": "gone"})
    )
    run_pull(**SEARCH, today=TODAY, force_refresh=True, transport=refused.transport)

    error = _spanning_error(outcome.pull_ref, corroborate=False)
    assert f"{held} of {SPANNING_TOTAL} records readable" in error, (
        f"a failed refresh recorded {error!r} over an entry still holding {held} of this "
        f"window's records. The reason describes the run; the reader takes it to describe "
        "the entry, and the derive is about to prove it wrong."
    )
    assert len(load_shaped_pull(outcome.pull_ref, Config()).comps) == expected_comps(), (
        "a failed refresh lost evidence it was replacing"
    )
    assert no_sockets == []
