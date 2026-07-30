"""Queue row 13c (developer-authored) — the page index as a pure function.

QA's suite for this row asserts the row's *behaviour* end to end: what a resume
buys, what a damaged entry reports, what the loader can still shape. This file
sits underneath it and pins the two pure pieces that behaviour rests on, at the
layer where each rule can be stated in one line:

    `_window_state`  — the walk that decides whether a window is whole, and
                       which offset (if any) a call would deliver something new
                       at. This is the whole of the money question.
    the index's KEY  — that a page is located by its filename and its sidecar's
                       `offset`, and **never** by the sidecar's `sig`.

WHY THE `sig` TEST IS HERE AND NOT IN QA'S FILE
-----------------------------------------------
It is the one thing in this row a fixture-mode test structurally cannot catch,
and it is therefore the one thing most likely to be reintroduced. `_fetch_one`'s
two write paths file DIFFERENT sidecar `sig` values — the live path a per-page
hash (`fixture_signature(page_params)`, which includes `offset`), the fixture
path the per-query signature — so grouping pages by `meta["sig"]` works against
fixtures and silently does not against the wire. Reconciling the two is queue
row 13h's; not depending on either is this row's, and a *unit* test over the
index is where that can be asserted without a wire at all: file two pages of one
window with deliberately hostile `sig` values and require the index to group
them anyway.

WHY THESE ARE PRIVATE NAMES
----------------------------
`_window_state` computes what `PullOutcome.calls_to_complete` means, which is
the number F3-S2's modal asks the user to consent to. Reaching it only through
`run_pull` would need a transport, a ledger and a plan to state "a single page
with no recorded total is a whole window" — three moving parts for one rule, and
the reason it went unguarded at query granularity for as long as it did. The
[DEFAULT] file layout stays free to change: every case below builds its input
through `write_raw_response`/`_page_sig` rather than by naming a file.

MONEY (WORKFLOW.md §6): nothing here constructs a client or a transport. Not one
of these tests can spend, because none of them can fetch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rentcomp.client.pull import (
    _page_offset,
    _page_sig,
    _stored_responses,
    _StoredPage,
    _total_of,
    _window_state,
)
from rentcomp.storage.cache import write_raw_response

KEY = "d" * 64
SIG = "y2026-active"


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    return root


def page(offset: int, records: int | None, total: int | None = None) -> _StoredPage:
    return _StoredPage(offset=offset, records=records, total_count=total)


def indexed(*pages: _StoredPage) -> dict[int, _StoredPage]:
    return {page.offset: page for page in pages}


# ===========================================================================
# the walk — whole, and what a call would buy
# ===========================================================================


def test_a_window_with_nothing_filed_is_owed_from_its_first_page() -> None:
    """The unfetched case. `filed` is False, so `_reconcile` knows not to record
    a demotion for a window that was simply never bought."""
    state = _window_state({})

    assert state.filed is False
    assert state.whole is False
    assert state.next_offset == 0


def test_one_readable_page_with_no_recorded_total_is_a_whole_window() -> None:
    """The overwhelmingly common case, and the one that must not regress.

    Most windows answer in a single call and RentCast does not always echo
    `X-Total-Count`. With no total recorded, nothing on disk says more records
    exist — so the window is whole and owes nothing. Reading it as "page 0 of a
    longer set" would buy the same window again on every attempt, forever, which
    is the failure D24 treats as different in kind rather than in degree.
    """
    state = _window_state(indexed(page(0, records=1)))

    assert state.whole is True
    assert state.next_offset is None
    assert state.fetched == 1


def test_a_recorded_total_above_what_is_readable_prices_exactly_one_call() -> None:
    """The truncated first pull: 2 of 3 records arrived, so the window re-enters
    at the offset the held page accounts for — not at 0, which is already paid
    for, and not past the gap, which would complete a window whose records never
    came."""
    state = _window_state(indexed(page(0, records=2, total=3)))

    assert state.whole is False
    assert state.next_offset == 2
    assert (state.fetched, state.total_count) == (2, 3)


def test_pages_that_add_up_to_the_recorded_total_are_whole() -> None:
    state = _window_state(
        indexed(page(0, 2, total=6), page(2, 2, total=6), page(4, 2, total=6))
    )

    assert state.whole is True
    assert state.next_offset is None
    assert state.fetched == 6


@pytest.mark.parametrize(
    ("held", "expected_offset"),
    [
        pytest.param((2, 4), 0, id="the first page is gone"),
        pytest.param((0, 4), 2, id="a middle page is gone"),
        pytest.param((0, 2), 4, id="the last page is gone"),
    ],
)
def test_a_lost_page_is_priced_at_its_own_offset_wherever_it_sat(
    held: tuple[int, ...], expected_offset: int
) -> None:
    """Three genuinely different failures, not three spellings of one.

    The cheapest wrong fix — "resume after the highest offset I hold" — gets the
    last case right and silently never recovers the middle one. The second
    cheapest — "any page missing means the window is owed" — buys three calls
    where one page was lost.
    """
    state = _window_state(indexed(*(page(offset, 2, total=6) for offset in held)))

    assert state.whole is False
    assert state.next_offset == expected_offset


def test_a_gap_below_a_page_that_is_filed_is_owed_even_with_no_total_recorded() -> None:
    """The guard for the case no recorded total can answer.

    A server that paginates without echoing `X-Total-Count` is paginated all the
    same — the client stops on a short page (spec §3.2). Lose page 0 of such a
    window and there is no total to notice it by, but the page sitting at a
    HIGHER offset cannot exist without the pages before it. That is evidence
    more records exist, and it is what stops this walk from calling the window
    whole over the very page it lost.
    """
    state = _window_state(indexed(page(500, records=190)))

    assert state.whole is False
    assert state.next_offset == 0


def test_an_empty_page_ends_the_set_however_large_the_total_claimed_to_be() -> None:
    """`b"[]"` is a real, paid-for answer, and the walk needs the same stopping
    condition the fetch loop has for it.

    Without this, a server that reports a total it cannot fill (or a dataset that
    shrank between two calls) leaves a window permanently short by a quantity no
    call can deliver — bought again on every attempt, with no stopping condition.
    """
    state = _window_state(indexed(page(0, records=0, total=690)))

    assert state.whole is True
    assert state.next_offset is None


def test_a_page_present_and_unreadable_is_short_and_owes_nothing() -> None:
    """F3-S4's shipped pricing, one level down and unchanged by this row.

    The bytes were paid for and a re-fetch returns the same ones, so the window
    is missing (its evidence cannot be produced) and **not** owed. Queue row 13g
    is the row that may tell real corruption from an unreadable response, by
    recording a digest at write time; until it lands, stuck-and-free beats
    unbounded spend.
    """
    state = _window_state(indexed(page(0, records=None, total=6), page(2, 2, total=6)))

    assert state.whole is False
    assert state.next_offset is None, (
        "a page on disk that cannot be read was priced at a call. Re-fetching returns the "
        "same bytes, so the quote can never come true and the user pays it every attempt."
    )


def test_an_unreadable_page_never_hides_behind_the_readable_ones_around_it() -> None:
    """Why deletion is not the only shape that matters.

    Damaging one page leaves its siblings readable, so a rule that asks "does any
    page under this signature parse" calls the window satisfied — the defect this
    row removes is not specific to a file going missing.
    """
    whole = _window_state(indexed(page(0, 2, total=6), page(2, 2, total=6), page(4, 2, total=6)))
    damaged = _window_state(
        indexed(page(0, 2, total=6), page(2, None, total=6), page(4, 2, total=6))
    )

    assert whole.whole is True
    assert damaged.whole is False


# ===========================================================================
# what the index is keyed on — the row 13h trap, pinned
# ===========================================================================


def test_the_page_index_groups_a_window_by_filename_not_by_the_sidecar_sig(home) -> None:
    """**The one thing a fixture-mode test cannot catch.**

    The two write paths disagree about what they file as `meta["sig"]`: the live
    path files a per-PAGE hash, the fixture path the per-QUERY signature. So the
    sidecars below carry hostile values on purpose — one per-page-ish, one from
    another window entirely — and the index must still read these two files as
    two pages of ONE window, because that is what their filenames say.

    Grouping by `sig` would pass every fixture-mode test in this repo and split
    this window in two against the wire, where each half would then look like a
    whole single-page window and the pull would report itself complete over four
    of its six records. Queue row 13h owns making the two writers agree; this
    test owns not needing them to.
    """
    write_raw_response(
        KEY,
        _page_sig(SIG, 0),
        json.dumps([{"id": "a"}, {"id": "b"}]).encode("utf-8"),
        meta={"sig": "a-per-page-hash-0000", "offset": 0, "total_count": 4},
    )
    write_raw_response(
        KEY,
        _page_sig(SIG, 2),
        json.dumps([{"id": "c"}, {"id": "d"}]).encode("utf-8"),
        meta={"sig": "y2099-inactive", "offset": 2, "total_count": 4},
    )

    store = _stored_responses(KEY)

    assert sorted(store.pages) == [SIG], (
        f"the index split one window into {sorted(store.pages)}. It grouped by the sidecar's "
        "`sig`, which is per-page on the live path — so this window's pages would each look "
        "like a whole one and the pull would report itself complete over half its records."
    )
    assert sorted(store.pages[SIG]) == [0, 2]
    assert store.window(SIG).whole is True


def test_a_sidecar_with_no_offset_falls_back_to_the_filenames_page_marker(home) -> None:
    """Responses filed by a hand-written fixture (and by any writer that predates
    the sidecar carrying `offset`) still have to land at the right page.

    Both sources agree by construction for anything this module writes —
    `_page_sig` formats the suffix from the same number the sidecar records — so
    the fallback is about files this module did not write, and it must not park
    every one of them at offset 0.
    """
    assert _page_offset({}, _page_sig(SIG, 2)) == 2
    assert _page_offset({"sig": "irrelevant"}, _page_sig(SIG, 500)) == 500
    # The sidecar leads when it has the field at all.
    assert _page_offset({"offset": 4}, _page_sig(SIG, 2)) == 4
    # A name from before the convention (or from another writer) is page 0, which
    # is exactly right: it is the whole of whatever it is.
    assert _page_offset({}, "fe9de5158f036802") == 0


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [
        pytest.param(690, 690, id="an ordinary total"),
        pytest.param(0, 0, id="a total of zero is a real answer"),
        pytest.param(None, None, id="absent"),
        pytest.param("690", None, id="a string is not a total"),
        pytest.param(True, None, id="a bool is not a total"),
        pytest.param(-1, None, id="a negative total is not a total"),
        pytest.param(690.0, None, id="a float is not a total"),
    ],
)
def test_a_total_count_is_read_strictly_or_not_at_all(recorded, expected) -> None:
    """The total is the only thing that can turn an absent page into a call, so a
    value this code cannot vouch for must read as "not recorded" rather than as a
    number to charge against.

    Fails safe in the direction that matters: not recorded means the walk invents
    no purchase, which leaves a pull honestly incomplete at worst and never
    spends the owner's month on a malformed sidecar.
    """
    assert _total_of({"total_count": recorded} if recorded is not None else {}) == expected
