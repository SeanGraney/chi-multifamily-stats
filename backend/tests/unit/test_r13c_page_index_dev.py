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

from rentcomp.client.rentcast import MAX_LIMIT
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


def page(
    offset: int, records: int | None, total: int | None = None, *, indexed: bool = True
) -> _StoredPage:
    return _StoredPage(
        offset=offset, records=records, total_count=total, indexed=indexed
    )


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


def test_the_page_offset_comes_from_the_filename_before_the_sidecar(home) -> None:
    """The DERIVED half wins, and the untrusted half is only a fallback.

    The offset is the page index's key, so a loose read of it does not misplace a
    page — it destroys one. Measured with the ordering inverted and one sidecar's
    `offset` set to `"not-a-number"`: the value collapsed to 0, overwrote the real
    page 0 in the index, and the pull then demanded a call for a page it was
    holding. `_page_sig` wrote the filename suffix, so that is the half this code
    can vouch for.
    """
    # Derived and trustworthy: the filename decides, even against a sidecar that
    # disagrees.
    assert _page_offset({"offset": 4}, _page_sig(SIG, 2)) == 2
    assert _page_offset({}, _page_sig(SIG, 500)) == 500
    # The fallback still earns its keep — F7's shape leaves a response whose
    # sidecar never landed, and a file from another writer may carry no marker.
    assert _page_offset({"offset": 6}, "some-other-writers-name") == 6
    # A name from before the convention is page 0, which is exactly right: it is
    # the whole of whatever it is.
    assert _page_offset({}, "fe9de5158f036802") == 0


@pytest.mark.parametrize(
    "hostile",
    [
        pytest.param(True, id="a bool would index a phantom page 1"),
        pytest.param("not-a-number", id="a string used to swallow its error and return 0"),
        pytest.param(-5, id="a negative offset is unreachable from a walk that starts at 0"),
        pytest.param(2.0, id="a float is not an offset"),
        pytest.param(None, id="absent"),
    ],
)
def test_a_hostile_sidecar_offset_never_displaces_a_page(home, hostile) -> None:
    """A corrupt index must not cost a call while every byte is intact.

    Read as strictly as `_total_of` reads the total beside it, and with a name to
    fall back on, every one of these lands the page at 0 — which is where a file
    with no readable page marker belongs — rather than at a phantom offset or on
    top of a page that is already there.
    """
    assert _page_offset({"offset": hostile}, "a-name-with-no-page-marker") == 0
    # And with a usable filename, the sidecar cannot move it at all.
    assert _page_offset({"offset": hostile}, _page_sig(SIG, 2)) == 2


def test_a_garbage_keyed_file_never_overwrites_a_readable_page(home) -> None:
    """The collision guard, which is what makes the strict read a belt as well as
    braces.

    Two files claiming one offset can only happen for files this module did not
    write. Whichever the directory scan reaches second, the **readable** one must
    survive in the index: an unreadable file displacing a real page 0 opens a gap
    where the bytes are intact, and the pull buys them again.
    """
    write_raw_response(
        KEY, _page_sig(SIG, 0), json.dumps([{"id": "real"}]).encode("utf-8"), meta={"offset": 0}
    )
    # A second file that also resolves to page 0 (no page marker in its name) and
    # holds nothing a parse can use.
    write_raw_response(KEY, "aaa-not-a-page-name", b"{not json", meta={})

    window = _stored_responses(KEY).pages

    assert window[SIG][0].records == 1, (
        "an unreadable file displaced the readable page it collided with, which opens a gap "
        "over bytes that are on disk and buys them again"
    )


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


# ===========================================================================
# the lost-total state — "not reported" and "reported then lost" are not one
# ===========================================================================
#
# Both reach the walk as `total_count is None`, and collapsing them was wrong in
# BOTH directions at once: a full page with its total lost had a gap invented for
# it, and a SHORT page with its total lost was declared whole. The second is the
# worse one, and it is worse than the defect this whole row exists to fix — 440
# paid-for records gone with no `missing` label naming them.


def test_a_short_page_whose_total_was_lost_is_never_declared_whole() -> None:
    """**The merge blocker, at the layer where it is decidable.**

    Measured before the fix: 250 records on disk, `X-Total-Count: 690` on the
    wire, that page's sidecar unreadable -> `complete=True, missing=(),
    calls_to_complete=0`. The short-page rule answered a question it had no
    standing to answer, because "the server reported no total" and "the total is
    on a sidecar we can no longer read" arrive here as the same `None`.
    """
    state = _window_state(indexed(page(0, records=250, indexed=False)))

    assert state.lost_total is True, (
        "the walk cannot tell a lost total from an absent one, so the short-page rule is "
        "about to decide whether 440 paid-for records exist"
    )
    assert state.whole is False, (
        "a window whose only page has an unreadable sidecar was declared whole. Nothing on "
        "disk can say how large that window is, and the page's own length is not evidence "
        "about a total the server may well have sent."
    )
    assert state.next_offset == 250, (
        "the window is not whole and names no offset, so there is no priced path back to "
        "whatever is missing — the exact outcome this row exists to eliminate"
    )


def test_a_full_page_whose_total_was_lost_is_also_undecided_rather_than_guessed() -> None:
    """The other direction of the same missing distinction.

    A page at `MAX_LIMIT` with its total lost used to have a gap invented for it
    on nothing better than the page's length. It still asks for another page —
    which is right — but now because the total is *unknown*, not because 500
    happens to equal the limit. One rule, both directions.
    """
    state = _window_state(indexed(page(0, records=MAX_LIMIT, indexed=False)))

    assert state.lost_total is True and state.whole is False
    assert state.next_offset == MAX_LIMIT


def test_a_sibling_page_that_still_records_a_total_settles_the_window() -> None:
    """The recovery, and the reason a lost sidecar is usually harmless.

    "The sidecar is gone but another page already proved a total exists" is a
    different state from "nothing on disk can supply one", and only the second is
    undecidable. This is also **why my own F7 test missed the blocker**: its
    scenario had an earlier page whose surviving total the walk carried forward,
    and §5a's common case — one page per window — has no such sibling.
    """
    state = _window_state(
        indexed(page(0, 2, indexed=False), page(2, 2, total=6), page(4, 2, total=6))
    )

    assert state.lost_total is False, "a total was recovered, so nothing is undecided"
    assert state.whole is True
    assert state.next_offset is None


def test_a_genuinely_absent_total_still_ends_the_set_on_a_short_page() -> None:
    """The narrowing must not swallow the ordinary case.

    A sidecar that reads back fine and simply carries no `total_count` means the
    server reported none — most windows, per §5a — and a page shorter than the
    limit is then the end of the set. Reading THAT as undecided would leave every
    single-page window permanently un-whole and re-bought.
    """
    state = _window_state(indexed(page(0, records=1, indexed=True)))

    assert state.lost_total is False
    assert state.whole is True and state.next_offset is None


# ===========================================================================
# N1 — a blocked page must not annihilate the gaps below it
# ===========================================================================


def test_a_blocked_page_does_not_hide_a_buyable_gap_beneath_it() -> None:
    """Measured before the fix: page 0 deleted, page 1 emptied -> `next_offset=
    None, owed=False`, zero calls, forever.

    Page 0 was **gone and buyable**; the unreadable page above it was what made it
    unreachable through the product. Two aggravations made it worse than the
    arithmetic: the message claimed "every other page of this pull is intact", and
    the trigger is transient — a OneDrive or antivirus lock on the page above — so
    whether the owner could recover paid-for evidence turned on a file lock.
    """
    state = _window_state(indexed(page(2, records=None, total=6), page(4, 2, total=6)))

    assert state.whole is False
    assert state.next_offset == 0, (
        "the gap at offset 0 is gone and buyable, and an unreadable page ABOVE it erased the "
        "only priced path to it"
    )
    assert state.blocked is not None, "the unreadable page must still be reported"


def test_a_blocked_page_still_yields_the_total_its_own_sidecar_records() -> None:
    """The total was read after the walk had already stopped, so it was never read.

    Measured: "2 of an unknown number of records readable" over an entry whose
    sidecars both record `total_count=6`. A blocked page's *bytes* are unusable;
    its sidecar usually is not, and it is often the only thing that knows how
    large the window is.
    """
    state = _window_state(indexed(page(0, 2, total=6), page(2, records=None, total=6)))

    assert state.total_count == 6, (
        "the blocking page's own sidecar records the window's size and the walk stopped "
        "before reading it, so every message about this window says 'an unknown number'"
    )


def test_a_blocked_page_with_no_gap_below_it_is_still_missing_and_free() -> None:
    """The N1 fix must not become "always owed" — B4's pricing is untouched.

    With every other page readable, an unreadable one leaves the window short and
    owing nothing: the bytes are on disk and paid for, and a re-fetch returns them
    (queue row 13g is what could tell that from real corruption).
    """
    state = _window_state(
        indexed(page(0, 2, total=6), page(2, records=None, total=6), page(4, 2, total=6))
    )

    assert state.whole is False
    assert state.next_offset is None
    assert state.blocked is not None
