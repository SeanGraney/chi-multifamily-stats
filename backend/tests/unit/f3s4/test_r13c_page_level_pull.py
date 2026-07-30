"""Queue row 13c — page-level resume, and a paginated window that reports
itself COMPLETE over evidence it has lost. Layer 1.

THE ROW IN ONE SENTENCE
-----------------------
Satisfaction is tracked per *query*, and a signature counts as held when **any**
page under it parses — so a window that spans pages reports itself whole after
losing one, and the loader silently shapes fewer records than the pull paid for.

Re-measured on `main` @ 8fccfe2 before a line of this file was written: a healthy
pull reports `complete=True` over 5 files and shapes 6 comps; delete page 0 of
its 2-page window and it still reports `complete=True, missing=(), calls_to_
complete=0` while the loader shapes **4**. Two paid-for records gone, nothing on
screen to say so — and a third symptom the row does not yet name: **the resume
then sends nothing at all**, so there is no priced path back to them. The user's
only route to their own evidence is a REFRESH that re-buys the whole window.

WHY IT LANDS BEFORE THE BUCKET STORY (F10-S1)
----------------------------------------------
F10-S1's `[INVARIANT]` is a leased-only DOM median per bucket. A median taken
over a comp set that is silently short is wrong in precisely the way that
invariant forbids, and nothing downstream can detect it — the pull says it is
whole. The exposure is not hypothetical: the committed gate fixture is **500 of
690 records**, so the owner's next real pull of that search paginates.

TEST PLAN — every behaviour once, at the lowest layer that can hold it
----------------------------------------------------------------------
No Playwright leg, and that is a decision rather than an omission. Nothing here
is browser-genuine: "which offset went on the wire", "which page's bytes are on
disk", "how many records the loader can still shape" are invisible above the
API, this row completes no user flow (F4-S6 owns rendering a partial pull, and
F3-S2 the modal), and a browser spec asserting a comp count would be an L2
assertion in a browser costume (AGENT_QA.md, "smells").

    #   behaviour                                            layer  test
    P1  the harness really paginates one window              L1     test_the_harness_spans_one_window_over_three_pages
    P2  the run under test is this worktree's source         L1     test_the_rentcomp_under_test_is_the_one_beside_this_file
    A1  a resume does not re-buy the page it holds           L1     test_a_query_interrupted_between_pages_does_not_re_buy_the_page_it_holds
                                                                    (in test_f3s4_resumable_pulls.py — xfail marker removed, see NOTE below)
    A2  the held page's bytes are not rewritten              L1     test_a_page_resume_leaves_the_pages_it_already_holds_untouched
    A3  it re-enters at the offset the held pages account
        for — neither re-buying nor skipping records         L1     test_a_page_resume_re_enters_at_the_offset_its_held_pages_account_for
    A4  re-running a COMPLETE paginated pull spends nothing  L1     test_rerunning_a_complete_paginated_pull_does_not_reach_for_the_wire
    A5  a lost manifest over a paginated entry rebuilds
        from its pages, free (index and loader move
        together — QUEUE.md row 13e's binding lesson)        L1     test_a_lost_manifest_over_a_paginated_entry_is_rebuilt_from_its_pages_for_free
    B1  a paginated pull that calls itself complete can
        produce every record it paid for  ** THE ROW **      L1     test_a_paginated_pull_that_calls_itself_complete_can_produce_every_record_it_paid_for
    B2  a recovery buys at most the pages that were lost     L1     test_a_page_recovery_never_costs_more_calls_than_the_pages_it_lost
    B3  a lost page is NAMED and PRICED, so a resume has
        a way back to it                                     L1     test_a_window_whose_page_is_gone_names_the_gap_and_prices_the_call_that_closes_it
    B4  a page on disk and unreadable is missing and owes
        nothing (F3-S4's shipped pricing, one level down)    L1     test_a_page_on_disk_that_cannot_be_read_is_missing_and_owes_no_call
    B5  a repair never deletes the pages that survived       L1     test_a_page_repair_never_removes_the_pages_that_survived
    C1  no source text still defers page-level resume,
        and none still claims it buys nothing                L1     test_no_source_text_still_defers_page_level_resume_to_another_story
    D1  the two routes cannot disagree about a paginated
        entry                                                L2     backend/tests/api/test_r13c_paginated_pull_on_the_wire.py

RED / GREEN AT DISPATCH — measured, not predicted (17 red / 19 green)
----------------------------------------------------------------------
RED: A1, A2, A3, B1 (**all seven** damage shapes), B3, B4, C1.
GREEN and must STAY green: P1, P2, A4, A5, B2, B5.

B1 failing on the `unreadable` shapes as well as the `gone` ones was not what
this file predicted before it was run, and the reason matters: a truncated or
emptied page leaves its SIBLINGS readable, so the signature is still "held" by
the any-page-parses rule and the window still reports satisfied. The lost-page
defect is therefore not specific to deletion — **any** damage to one page of a
spanning window is invisible.

A5 in particular is a guard, not a target: it is what
stops a fix that records the page index **only** in the manifest, which would
make the index stricter than the store and reintroduce the divergence class that
failed F3-S4 (QUEUE.md row 13e — "any fix must move index and loader together").

NOTE ON THE PRE-EXISTING xfail (a QA ruling the PM asked for)
--------------------------------------------------------------
`test_f3s4_resumable_pulls.py::test_a_query_interrupted_between_pages_does_not_
re_buy_the_page_it_holds` was written `xfail(strict=False)` as a *scope question*
— it had to be able to pass or fail without either breaking the gate before the
PM ruled. The ruling exists now and this row is it, so the marker's reason no
longer describes anything true. It is removed rather than switched to
`strict=True`: an `xfail` of either strictness reports as **xfailed and exits
0**, which is the false-green family this project has been bitten by five times
(WORKFLOW.md §2), and a developer implementing this row would see a green suite
with nothing in it to fix. Full reasoning in the QA return.

MONEY (WORKFLOW.md §6): fixture mode or a `MockTransport`, never a socket.
Every live-path test asserts `no_sockets` recorded nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from r13c_support import (
    PAGED_RECORD_IDS,
    PAGE_SIZE,
    SEARCH,
    SPANNING_TOTAL,
    TODAY,
    Denied,
    Interrupted,
    Market,
    bought_page_zero,
    expected_comps,
    expected_files,
    expected_pages,
    offsets_sent,
    page_files,
    pages_holding,
    record_ids_on_disk,
)

from rentcomp.client.pull import pull_status, run_pull
from rentcomp.storage.config import Config
from rentcomp.storage.pulls import PullNotFoundError, load_shaped_pull

# ===========================================================================
# P — the harness itself, and the tree it is testing
# ===========================================================================
#
# A harness precondition, as a FAILING test rather than a skip guard: "13
# passed, 10 skipped, exit 0" is indistinguishable from a pass, and this project
# has now been bitten by that four times (WORKFLOW.md §2).


def test_the_rentcomp_under_test_is_the_one_beside_this_file() -> None:
    """The run must be about the checkout this file lives in.

    The shared `.venv`'s editable install points at the MAIN checkout's
    `backend/src`, so a worktree run can silently answer about the wrong source
    — confidently, with no error and no skip. `test_f1s2_workspace_store_dev.py`
    makes the same observation; it is repeated here because this row's whole
    subject is a defect that only exists on one of the two trees, and a
    wrong-tree run would report it fixed.
    """
    import rentcomp

    package_root = Path(rentcomp.__file__).resolve().parents[3]
    tests_root = Path(__file__).resolve().parents[4]

    assert package_root == tests_root, (
        f"these tests live under {tests_root} but `import rentcomp` resolved to "
        f"{package_root}. The run is testing a different checkout's source — set "
        f"PYTHONPATH={tests_root / 'backend' / 'src'} (WORKFLOW.md §2; on Windows the "
        "PYTHONPATH separator is ';', not ':')."
    )


def test_the_harness_spans_one_window_over_three_pages(live_env, no_sockets) -> None:
    """Measured, never assumed: this row is only about a window that PAGINATES.

    F4-S5's QA proved that inheriting a boundary from fixture luck is worthless
    (F4-S3's 42-day floor was covered only incidentally and F4-S8 showed what
    that is worth). So the shape every test below depends on — one window
    answering over three calls at three distinct offsets, every other window in
    one, and one comp per record — is asserted here first. If this fails, every
    result below is meaningless rather than wrong.
    """
    market = Market()
    outcome = run_pull(**SEARCH, today=TODAY, transport=market.transport)

    assert outcome.complete is True, (
        f"the healthy pull did not complete: missing={outcome.missing}. Nothing below can "
        "mean anything until it does."
    )
    assert len(page_files(outcome.pull_ref)) == expected_files(), (
        f"the pull filed {len(page_files(outcome.pull_ref))} responses, not "
        f"{expected_files()} — the spanning window did not paginate, so this row's subject "
        f"is not present. Wire: {market.sent}"
    )
    assert len(pages_holding(outcome.pull_ref, *PAGED_RECORD_IDS)) == expected_pages(), (
        "the spanning window's records are not spread over separate page files"
    )
    assert record_ids_on_disk(outcome.pull_ref) >= set(PAGED_RECORD_IDS)
    assert len(load_shaped_pull(outcome.pull_ref, Config()).comps) == expected_comps(), (
        "the harness's records do not shape one comp each, so a comp count cannot be used "
        "as this row's evidence meter (distinct addresses per record is what guarantees it)"
    )
    assert no_sockets == []


# ===========================================================================
# A — MONEY: the unit that was paid for is the CALL, not the query
# ===========================================================================


@pytest.fixture
def interrupted(home, live_env, no_sockets):
    """A pull whose spanning window was interrupted between pages.

    Page 0 arrives and is filed; the next call fails with a 503, so the window
    is genuinely short of evidence and genuinely owed — F3-S4 already pins that
    much (`test_bytes_on_disk_never_promote_a_window_the_manifest_recorded_as_
    failed`). What this row adds is what the resume may then BUY.

    Returns `(ref, held_ids)` where `held_ids` are the record ids already on
    disk.
    """
    first = run_pull(**SEARCH, today=TODAY, transport=Interrupted().transport)
    assert first.complete is False, (
        "the setup did not hold: the interrupted pull reported itself complete"
    )
    held = record_ids_on_disk(first.pull_ref)
    assert set(PAGED_RECORD_IDS[:PAGE_SIZE]) <= held, (
        "the setup did not hold: page 0 of the spanning window is not on disk, so there is "
        "nothing a resume could wrongly re-buy"
    )
    load_shaped_pull.cache_clear()  # the resume is a new process
    return first.pull_ref, held


def test_a_page_resume_leaves_the_pages_it_already_holds_untouched(
    interrupted, home
) -> None:
    """A page that has been paid for is never written again — bytes AND mtime.

    mtime is in the comparison deliberately: a resume that re-bought page 0 and
    wrote back byte-identical bodies has still spent one of the owner's 50
    calls, and a contents-only snapshot would call that clean (F3-S4's
    `entry_snapshot` reasoning, at page granularity).
    """
    ref, _held = interrupted
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in pages_holding(ref, *PAGED_RECORD_IDS[:PAGE_SIZE])
    }
    assert before, "the setup did not hold: no page 0 on disk"

    run_pull(**SEARCH, today=TODAY, transport=Market().transport)

    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in page_files(ref)
    }
    for name, snapshot in before.items():
        assert after.get(name) == snapshot, (
            f"the resume rewrote {name}, the page this pull already holds. Either it bought "
            "that page a second time, or it tidied up bytes that were already paid for — "
            "D24 forbids both."
        )


def test_a_page_resume_re_enters_at_the_offset_its_held_pages_account_for(
    interrupted, home, no_sockets
) -> None:
    """The offset is not merely non-zero — it is the one that buys exactly the
    records this pull does not have.

    Two wrong answers this pins apart, both of which pass a "did it avoid offset
    0" check:

    * re-entering after the HIGHEST held offset — correct here, and silently
      unable to recover a lost MIDDLE page (see B1's shape table); and
    * re-entering at an offset past the gap, which completes the window while
      the records in the gap never arrive.

    Expressed as "the count of records already on disk" rather than as the
    literal `2`, so it stays true if the page size or the plan changes.
    """
    ref, held = interrupted
    already = len(held & set(PAGED_RECORD_IDS))
    market = Market()

    run_pull(**SEARCH, today=TODAY, transport=market.transport)

    assert not bought_page_zero(market), (
        f"the resume re-entered the spanning window at its first page ({market.sent}), "
        "buying bytes already filed under this ref. Per-CALL file granularity is what makes "
        "a partial set meaningful; resuming per QUERY throws it away (ARCHITECTURE.md §5a)."
    )
    assert offsets_sent(market) == [str(already)], (
        f"the resume sent {market.sent} — the interrupted window owes exactly the records it "
        f"does not hold, which begins at offset {already}. One call, at that offset: fewer "
        "leaves the gap open forever, more buys a page twice."
    )
    assert record_ids_on_disk(ref) >= set(PAGED_RECORD_IDS), (
        "the resume finished without every record of the spanning window on disk. Not "
        "re-buying page 0 is half the requirement; the other half is that the missing pages "
        "are still owed and still bought."
    )
    assert no_sockets == []


def test_rerunning_a_complete_paginated_pull_does_not_reach_for_the_wire(
    home, live_env, no_sockets
) -> None:
    """Zero calls, proven by removing the mechanism rather than by a counter.

    F3-S4 pins this for single-page windows. It is a different claim once a
    window spans pages, because the second run has to read a page INDEX back off
    disk and agree with it — the state where "some pages of this window are
    held" first becomes expressible.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    assert outcome.complete is True, "the pull this test re-runs did not complete"
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in page_files(outcome.pull_ref)
    }
    load_shaped_pull.cache_clear()

    denied = Denied("the whole paginated pull is already on disk")
    again = run_pull(**SEARCH, today=TODAY, transport=denied.transport)

    assert again.complete is True
    assert denied.requests == []
    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in page_files(outcome.pull_ref)
    }
    assert after == before, "a re-run of a complete paginated pull rewrote its pages"
    assert no_sockets == []


def test_a_lost_manifest_over_a_paginated_entry_is_rebuilt_from_its_pages_for_free(
    home, live_env, no_sockets
) -> None:
    """The guard on HOW this row may be fixed, not a target of the fix.

    GREEN today and it must stay green. `_held_signatures`/`_reconcile` and
    `_load_cache_backed_pull` both go through the single `records_from_raw`, so
    what the index calls held and what the loader can shape are the same set **by
    shared code** (QUEUE.md row 13e, where the PM reversed a fold-in ruling on
    exactly this ground). A fix that records the page index only in the manifest
    would make the index stricter than the store: with the manifest gone, every
    page on disk would read as unaccounted-for and the entry would be re-bought
    — the divergence class that failed F3-S4, one level down.

    So: delete the manifest, keep every page, and the entry must still reconcile
    to complete without spending a call.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
    assert outcome.complete is True
    (home / "cache" / outcome.pull_ref / "manifest.json").unlink()
    load_shaped_pull.cache_clear()

    denied = Denied("every page survived; only the index that named them was lost")
    repaired = run_pull(**SEARCH, today=TODAY, transport=denied.transport)

    assert denied.requests == []
    assert repaired.complete is True, (
        f"an entry that lost its manifest but kept every page reported itself incomplete "
        f"({repaired.missing}). The bytes are the truth and the manifest is an index over "
        "them; rebuilding the index is free (F3-S4's reconcile rule)."
    )
    assert len(load_shaped_pull(repaired.pull_ref, Config()).comps) == expected_comps()
    assert no_sockets == []


# ===========================================================================
# B — HONESTY: never whole over evidence that is gone
# ===========================================================================
#
# Each entry is `(apply, pages_lost, bytes_gone)`:
#   pages_lost — how many paid-for pages the damage genuinely destroyed, and
#                therefore the most a recovery may buy;
#   bytes_gone — True when the file is ABSENT (a call delivers something this
#                pull does not have → the window is owed), False when the file
#                is present and unusable (a call returns bytes already paid for
#                → missing, not owed). That asymmetry is F3-S4's shipped rule in
#                `_corroborate`, and this table is its page-level twin, not a new
#                policy. QUEUE.md row 13g is the row that may revisit the
#                pricing; if it does, it moves both levels together.
#
# Adding a fourteenth shape is a one-line edit — the point of the table.


def _delete(*ids: str):
    def apply(ref: str) -> None:
        for path in pages_holding(ref, *ids):
            path.unlink()

    return apply


def _truncate(*ids: str):
    def apply(ref: str) -> None:
        for path in pages_holding(ref, *ids):
            blob = path.read_bytes()
            path.write_bytes(blob[: len(blob) // 2])

    return apply


def _empty(*ids: str):
    def apply(ref: str) -> None:
        for path in pages_holding(ref, *ids):
            path.write_bytes(b"")

    return apply


def _make_directory(*ids: str):
    def apply(ref: str) -> None:
        for path in pages_holding(ref, *ids):
            path.unlink()
            path.mkdir()

    return apply


_FIRST = PAGED_RECORD_IDS[0]
_MIDDLE = PAGED_RECORD_IDS[PAGE_SIZE]
_LAST = PAGED_RECORD_IDS[-1]

PAGE_DAMAGE: dict[str, tuple] = {
    # --- a page is genuinely gone -------------------------------------------
    "the FIRST page of a spanning window was deleted": (_delete(_FIRST), 1, True),
    "a MIDDLE page of a spanning window was deleted": (_delete(_MIDDLE), 1, True),
    "the LAST page of a spanning window was deleted": (_delete(_LAST), 1, True),
    "a directory stands where the first page belongs": (_make_directory(_FIRST), 1, True),
    # --- a page is there and unusable ---------------------------------------
    "the FIRST page was truncated mid-json": (_truncate(_FIRST), 1, False),
    "a MIDDLE page was truncated mid-json": (_truncate(_MIDDLE), 1, False),
    "the FIRST page is a zero-byte file": (_empty(_FIRST), 1, False),
}


@pytest.fixture
def damaged(home, live_env, no_sockets):
    """A healthy paginated pull, then one page damaged the requested way.

    Returns `(ref, pages_lost, bytes_gone, ids_lost)`.
    """

    def _damage(shape: str):
        outcome = run_pull(**SEARCH, today=TODAY, transport=Market().transport)
        assert outcome.complete is True and len(page_files(outcome.pull_ref)) == expected_files(), (
            "the healthy pull this test damages did not complete over the expected pages, so "
            "nothing below would prove anything"
        )
        before = record_ids_on_disk(outcome.pull_ref)

        apply, pages_lost, bytes_gone = PAGE_DAMAGE[shape]
        apply(outcome.pull_ref)
        load_shaped_pull.cache_clear()  # the damage = a new process

        lost = before - record_ids_on_disk(outcome.pull_ref)
        assert len(lost) == PAGE_SIZE, (
            f"[{shape}] the damage removed {len(lost)} readable records, not one page's "
            f"worth ({PAGE_SIZE}) — the shape table is wrong, so fix the table"
        )
        return outcome.pull_ref, pages_lost, bytes_gone, lost

    return _damage


def _comps(ref: str) -> int | None:
    """What the pull can actually produce, or `None` if it refuses.

    A refusal is an acceptable answer to a damaged entry — loud beats silent.
    What is not acceptable is a *complete* pull that quietly produces less.
    """
    try:
        return len(load_shaped_pull(ref, Config()).comps)
    except (PullNotFoundError, OSError, ValueError):
        return None


@pytest.mark.parametrize("shape", sorted(PAGE_DAMAGE))
def test_a_paginated_pull_that_calls_itself_complete_can_produce_every_record_it_paid_for(
    damaged, shape
) -> None:
    """**THE ROW, stated as a property.**

    A paginated window may not report `complete=True` while the loader shapes
    fewer records than the pull's own evidence accounts for. Asserted through
    `pull_status` (what the user is told) and `load_shaped_pull` (what the
    numbers are computed from), because those are exactly the two things a lost
    page separates — F3-S4 fixed this at query granularity and it survives one
    level down.

    Deliberately does NOT dictate the answer. An honestly-incomplete pull is a
    perfectly good one (§5a: with a 50-call cap, refusing to show anything until
    the set is perfect would strand the user), and so is re-fetching the lost
    page. Silently short is the one answer that is not available — and it is the
    answer the code gives today, on every `gone` shape.

    Why it is not enough to catch this in a bucket test later: a median over a
    silently short comp set is wrong in the way F10-S1's `[INVARIANT]` forbids,
    and there is nothing downstream that could notice, because the pull says it
    is whole.
    """
    ref, _pages_lost, _bytes_gone, lost = damaged(shape)

    status = pull_status(ref)

    if not status.complete:
        assert status.missing, (
            f"[{shape}] the pull reports itself incomplete and names nothing. F4-S6 renders "
            "`missing` verbatim, so a gap with no label is a gap the user cannot act on."
        )
        return
    assert _comps(ref) == expected_comps(), (
        f"[{shape}] the pull reports complete with nothing missing, and the loader shapes "
        f"{_comps(ref)} of {expected_comps()} records — {sorted(lost)} are gone. The user is "
        "told a set is whole while every number on the screen is computed from less evidence "
        "than they paid for, with nothing to say so (NORTH_STAR; ARCHITECTURE.md §5a)."
    )


@pytest.mark.parametrize("shape", sorted(PAGE_DAMAGE))
def test_a_page_recovery_never_costs_more_calls_than_the_pages_it_lost(
    damaged, shape
) -> None:
    """D24 at page granularity: at most the pages that were actually lost.

    The cheapest wrong fix for this row is "a window with any page missing is
    owed" — which re-buys the whole window, three calls where one page was lost,
    on a 50-call month. The second-cheapest is to re-buy on every attempt, which
    has no stopping condition at all (`_corroborate`'s own measured example: 2
    calls per attempt, every attempt, for a window that could never complete).
    """
    ref, pages_lost, _bytes_gone, _lost = damaged(shape)
    market = Market()

    run_pull(**SEARCH, today=TODAY, transport=market.transport)

    assert market.calls <= pages_lost, (
        f"[{shape}] the recovery bought {market.calls} call(s) where {pages_lost} page(s) "
        f"were lost ({market.sent}). A page that has been paid for is never bought again, "
        "whatever happened to the file beside it (D24 / ARCHITECTURE.md §5a)."
    )


@pytest.mark.parametrize(
    "shape", sorted(name for name, (_a, _p, gone) in PAGE_DAMAGE.items() if gone)
)
def test_a_window_whose_page_is_gone_names_the_gap_and_prices_the_call_that_closes_it(
    damaged, shape
) -> None:
    """A lost page must leave a priced way back to it.

    This is the third symptom of the row, and the one its own note does not
    name: after page 0 of a spanning window is deleted, `run_pull` today sends
    **nothing at all** and reports `complete=True`. So the two records are not
    merely mis-accounted — they are unreachable through the product. The only
    remedy left is a REFRESH that re-buys the whole window, which is the
    double-pay this row exists to prevent, or deleting the cache entry, which
    costs the user everything else in it.

    `calls_to_complete` is what F3-S2's modal asks the user to consent to, so it
    must count the call a resume would actually make: the page is gone, another
    call really does deliver something this pull does not have, and
    `_corroborate` already draws that exact line at query granularity.
    """
    ref, pages_lost, _gone, lost = damaged(shape)

    status = pull_status(ref)

    assert status.complete is False, (
        f"[{shape}] the pull reports itself complete over {sorted(lost)}, which are not on "
        "disk. Satisfaction is being decided by ANY page under a signature parsing, so a "
        "window that spans pages reports itself whole after losing one."
    )
    assert status.missing, f"[{shape}] the gap is not named, so F4-S6 has nothing to render"
    assert status.calls_to_complete == pages_lost, (
        f"[{shape}] completing this pull is priced at {status.calls_to_complete} call(s) "
        f"where {pages_lost} page(s) are genuinely absent. Priced at 0, the resume never "
        "fetches and the records are unreachable; priced high, the user consents to buying "
        "pages that are already on disk."
    )


@pytest.mark.parametrize(
    "shape", sorted(name for name, (_a, _p, gone) in PAGE_DAMAGE.items() if not gone)
)
def test_a_page_on_disk_that_cannot_be_read_is_missing_and_owes_no_call(
    damaged, shape
) -> None:
    """The other half of the pricing, inherited from F3-S4 rather than invented.

    A page that is **there and unreadable** was paid for and a re-fetch returns
    the same bytes, so the window is missing (its evidence cannot be produced)
    and **not owed**. Collapsing this into the `gone` case is what has no
    stopping condition: the window is re-bought on every attempt, forever, for
    as long as the user keeps trying.

    Stuck-and-free rather than unbounded-spend is D24's position and F3-S4's
    shipped behaviour at query granularity. QUEUE.md row 13g proposes recording
    a byte length or digest at write time, which is the fact that would let the
    two cases be told apart honestly; if the owner takes that row, this
    assertion moves with the query-level one, together, and not before.
    """
    ref, _pages_lost, _gone, lost = damaged(shape)

    status = pull_status(ref)

    assert status.complete is False, (
        f"[{shape}] the pull reports itself complete while {sorted(lost)} cannot be read off "
        "disk. Present is not the same as usable."
    )
    assert status.missing, f"[{shape}] the gap is not named"
    assert status.calls_to_complete == 0, (
        f"[{shape}] completing this pull is priced at {status.calls_to_complete} call(s) for "
        "bytes that are already on disk and already paid for. A re-fetch returns the same "
        "response, so the call buys nothing and the loop never terminates (D24)."
    )


@pytest.mark.parametrize("shape", sorted(PAGE_DAMAGE))
def test_a_page_repair_never_removes_the_pages_that_survived(damaged, shape) -> None:
    """A repair must not tidy away the evidence it is repairing.

    The tempting fix for an inconsistent page set is to drop the whole window
    and re-pull it, which satisfies every "is it consistent now" assertion while
    throwing away calls the owner has already paid for.
    """
    ref, _pages_lost, _gone, _lost = damaged(shape)
    survivors = record_ids_on_disk(ref)
    assert survivors, f"[{shape}] the damage left nothing readable, so this proves nothing"

    run_pull(**SEARCH, today=TODAY, transport=Market().transport)

    assert record_ids_on_disk(ref) >= survivors, (
        f"[{shape}] the recovery removed records that survived the damage: "
        f"{sorted(survivors - record_ids_on_disk(ref))}. Paid-for bytes are never rolled "
        "back, and never cleaned up either (D24)."
    )


# ===========================================================================
# C — the stale cross-reference, pinned so the next reader cannot inherit it
# ===========================================================================


def test_no_source_text_still_defers_page_level_resume_to_another_story() -> None:
    """A stale cross-reference has cost this project real time twice, so it is
    an assertion rather than a review note.

    Two claims must be gone from the code once this row lands, and both are
    live today:

    1. that page-level resume is **out of scope / another story's** — the
       deferral is spent the moment this row is implemented; and
    2. `client/pull.py`'s stated reason for the deferral: *"no committed fixture
       and no live pull in this product's plan has yet needed more than one page
       per window."* That is **false as written** — the committed gate fixture
       is 500 of 690 records, so the owner's next real pull of that search
       paginates. A reader who believes it concludes this code path is dead.

    Deliberately asserted over sentences that mention page-level resume rather
    than over exact prose, so any honest rewrite passes and only the false lead
    fails. The test names the file and the line to fix in its own failure
    message, because that is the fix.
    """
    root = Path(__file__).resolve().parents[4]
    suspects = [
        root / "backend" / "src" / "rentcomp" / "client" / "pull.py",
        root / "backend" / "tests" / "unit" / "test_f3s4_durable_cache_dev.py",
        root / "backend" / "tests" / "unit" / "f3s4" / "test_f3s4_resumable_pulls.py",
    ]
    deferrals = ("out of scope", "is f3-s4's", "to f3-s4", "f3-s4's")
    offenders: list[str] = []
    for path in suspects:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for sentence in text.replace("\n", " ").split("."):
            lowered = sentence.lower()
            if "page-level resume" not in lowered and "page resume" not in lowered:
                continue
            if any(claim in lowered for claim in deferrals):
                offenders.append(f"{path.name}: {sentence.strip()[:160]}")
            if "more than one page per window" in lowered:
                offenders.append(f"{path.name}: {sentence.strip()[:160]}")

    assert offenders == [], (
        "source text still defers page-level resume to another story, or still gives the "
        "reason it was deferred, after this row implemented it:\n  "
        + "\n  ".join(offenders)
        + "\nRewrite it to describe what the code now does. The second claim is also false "
        "on its own terms: the committed gate fixture is 500 of 690 records."
    )


# ===========================================================================
# the money backstop, restated where it cannot be missed
# ===========================================================================


def test_no_test_in_this_module_can_reach_a_real_socket(no_network) -> None:
    """WORKFLOW.md §6, as an assertion rather than a convention.

    This row is *about* the money path, which is the strongest possible reason
    not to spend on it. Every live-path test here injects a `MockTransport`;
    this one exists so that a future edit which forgets to has something to
    fail against.
    """
    assert no_network == []
    assert json.dumps(sorted(PAGE_DAMAGE)), "the damage table is empty"
    assert SPANNING_TOTAL >= 3 * PAGE_SIZE, (
        "the spanning window no longer has a middle page, so the shape that distinguishes a "
        "real page index from 'resume after the highest offset I hold' is gone"
    )
