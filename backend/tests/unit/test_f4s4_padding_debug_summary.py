"""F4-S4 [INVARIANT], second half of the AC — "padding-only records (pulled
but stitched-start outside) are **dropped and counted in a pipeline debug
summary**". QA-authored, written RED before the developer starts.

THIS IS THE ONLY RED FILE OF THE THREE, AND THE ONLY ONE THAT ASKS FOR A SEAM
-----------------------------------------------------------------------------
Note for the PM, flagged rather than assumed: the *filtering* half of this
story is already implemented on `main` — WS-1 built the window filter and
cohort assignment into `pipeline/shape.py` ahead of this story (its own
docstring, step 6: "Window + cohort (F4-S4)"). So the sibling files
`test_f4s4_window_cohort.py` and `test_f4s4_window_properties.py` pass today.
That is reported as a finding, not treated as the story being done: their job
is now to *lock* behaviour that nobody has yet asserted (they kill mutants —
see the QA report), and this file is the part with no implementation at all.

`shape_raw_pull` today drops a padding-only chain with a bare `continue`. The
count is computed and thrown away, so the AC's second clause is unmet: a drop
that leaves no trace is indistinguishable from a pull that never fetched those
records, which is precisely the thing D24/§5a says a user must be able to tell
apart. "0 comps in your June window" and "539 records pulled, 539 outside your
window" call for completely different user actions.

THE SEAM THIS FILE ASKS FOR — A QA PROPOSAL, NOT A RULING
----------------------------------------------------------
The AC names a "pipeline debug summary" without giving it a shape, and QA does
not get to invent product contract. What follows is the *minimum* shape that
makes the AC assertable; the PM should ratify, rename, or replace it, and if
the developer picks a different name only `_resolve_seam` below changes.

    from rentcomp.pipeline.shape import shape_raw_pull_with_summary

    comps, summary = shape_raw_pull_with_summary(
        active_records, inactive_records, config, as_of,
        window_start_mmdd, window_end_mmdd,
    )
    summary.kept                    -> int, == len(comps)
    summary.dropped_outside_window  -> int, the padding-only chains

Three requirements on it, and nothing else:

1. **`shape_raw_pull` keeps working and keeps returning exactly `comps`.** It
   has three call sites in `storage/pulls.py` and a wall of existing dev tests
   comparing its output directly; a summary that arrived by changing its return
   type would be a refactor this story did not ask for. The two entry points
   must be the *same* computation — asserted below — so a second
   implementation cannot drift.
2. **The counts are of chains, not of raw records.** A padding-only *record*
   is not the unit that gets dropped: several raw records stitch into one
   chain, and the chain is what the window is applied to. Counting raw records
   would over-report by the re-list count and make `kept + dropped` fail to
   reconcile against anything.
3. **Nothing about `Config`, ordering, or field names beyond the two counts.**
   A summary that also carried per-cohort tallies or the dropped keys would be
   welcome; this file does not require it, so adding it later breaks nothing.

WHAT THIS FILE DELIBERATELY DOES *NOT* PIN — AND THE ONE QUESTION FOR THE OWNER
-------------------------------------------------------------------------------
Whether the count must reach the wire. "Debug summary" reads as an in-process
artefact, and `DerivedState.breakdown` is a *curation* accounting
(`included + excluded + filtered == pulled`, F7-S1) whose `pulled` means "comps
in this pull" — i.e. already post-window. Adding a padding count there would
change what an existing [INVARIANT] identity is over, which is not this
story's to do unilaterally. So this file asserts the summary exists and is
correct, and the "must it be visible to a user, and where?" question is
escalated in the QA report rather than answered by a test.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

# ---------------------------------------------------------------------------
# seam resolution — one place, so a rename costs one line
# ---------------------------------------------------------------------------

_SEAM_NAME = "shape_raw_pull_with_summary"

try:
    from rentcomp.pipeline.shape import shape_raw_pull_with_summary

    _SEAM_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - the red state before F4-S4 lands
    _SEAM_ERROR = exc

    def shape_raw_pull_with_summary(*_args, **_kwargs):  # type: ignore[misc]
        raise AssertionError("unreachable — every user is gated behind `needs_seam`")


needs_seam = pytest.mark.skipif(
    _SEAM_ERROR is not None,
    reason=f"rentcomp.pipeline.shape.{_SEAM_NAME} not importable yet: {_SEAM_ERROR}",
)


def test_the_pipeline_debug_summary_seam_exists() -> None:
    """The one loud, ungated failure that names what is missing; every
    assertion below skips legibly behind it, so the red state stays readable
    (AGENT_QA.md's commit rule — a collection error hides every spec beside
    it, a named failure plus N skips does not)."""
    assert _SEAM_ERROR is None, (
        f"F4-S4's AC requires that padding-only records are 'dropped **and counted in a pipeline "
        f"debug summary**'. `shape_raw_pull` drops them with a bare `continue` and keeps no count, "
        f"so the second half of the AC is unmet and unassertable. This file asks for "
        f"`rentcomp.pipeline.shape.{_SEAM_NAME}(active, inactive, config, as_of, window_start_mmdd, "
        f"window_end_mmdd) -> (comps, summary)` with `summary.kept` and "
        f"`summary.dropped_outside_window`; see this module's docstring for the full proposal "
        f"(QA-proposed, PM to ratify). Import error: {_SEAM_ERROR}"
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _record(id_: str, address: str, listed: date, removed: date | None) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": None,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 2,
        "bathrooms": 1,
        "squareFootage": 900,
        "status": "Active" if removed is None else "Inactive",
        "price": 2000.0,
        "listingType": "Standard",
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "removedDate": f"{removed.isoformat()}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed.isoformat()}T00:00:00.000Z",
        "lastSeenDate": f"{(removed or listed).isoformat()}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }


def _split(units: dict[str, list[tuple[date, date | None]]]) -> tuple[list[dict], list[dict]]:
    active: list[dict] = []
    inactive: list[dict] = []
    for address, segments in units.items():
        for i, (listed, removed) in enumerate(segments):
            record = _record(f"{address}-{i}", address, listed, removed)
            (active if removed is None else inactive).append(record)
    return active, inactive


AS_OF = date(2027, 6, 1)
_JUNE = ("06-15", "06-30")

#: Three units inside a Jun 15-30 window and four outside it. The outside four
#: are exactly what the +/-90d pad buys and this story throws away: spec §3.2
#: pads both sides because RentCast resets `listedDate` on re-list, so a padded
#: query necessarily returns records whose stitched start is nowhere near the
#: window.
_MIXED = {
    "1 Inside St": [(date(2025, 6, 15), date(2025, 7, 20))],
    "2 Inside St": [(date(2025, 6, 22), date(2025, 8, 1))],
    "3 Inside St": [(date(2024, 6, 30), date(2024, 8, 4))],
    "4 Padding St": [(date(2025, 4, 2), date(2025, 5, 9))],
    "5 Padding St": [(date(2025, 9, 14), date(2025, 10, 30))],
    "6 Padding St": [(date(2024, 3, 28), date(2024, 4, 20))],
    # a padding-only unit that is also RE-LISTED: three raw records, one chain,
    # so a summary counting raw records instead of chains reports 3 here
    "7 Padding St": [
        (date(2025, 1, 6), date(2025, 2, 10)),
        (date(2025, 3, 1), date(2025, 4, 2)),
        (date(2025, 4, 20), date(2025, 5, 30)),
    ],
}


# ---------------------------------------------------------------------------
# the AC's second clause
# ---------------------------------------------------------------------------


@needs_seam
def test_padding_only_records_are_counted_not_merely_discarded() -> None:
    """The AC, verbatim: dropped **and counted**.

    Four units in `_MIXED` have a stitched start outside the June window and
    three are inside it. The count is what turns "your search found 3 comps"
    into "your search found 3 comps; 4 more were fetched and fell outside your
    date window" — the difference between a user widening the radius (wrong
    fix) and widening the window (right fix), and the F4 epic's own 0-comp edge
    requires naming the binding constraint.
    """
    active, inactive = _split(_MIXED)
    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *_JUNE)

    assert summary.kept == 3, f"kept={summary.kept}, expected the 3 units starting inside the June window"
    assert summary.dropped_outside_window == 4, (
        f"dropped_outside_window={summary.dropped_outside_window}, expected 4 — the padding-only "
        "units (2025-04-02, 2025-09-14, 2024-03-28, and the re-listed 2025-01-06 chain)"
    )
    assert len(comps) == 3


@needs_seam
def test_the_summary_counts_chains_not_raw_records() -> None:
    """Requirement 2 of the seam proposal, on the unit built to catch it.

    `7 Padding St` is three raw records that stitch into one chain. The window
    is applied to chains, so the drop is one drop. A summary counting raw
    records would report 6 dropped here instead of 4 and would never reconcile
    against `kept`, which is a chain count by construction.
    """
    active, inactive = _split(_MIXED)
    _, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *_JUNE)
    assert summary.dropped_outside_window != 6, (
        "dropped_outside_window looks like a raw-record count: `7 Padding St` is 3 records that "
        "stitch into 1 chain, and the window filter drops chains"
    )
    assert summary.dropped_outside_window == 4


@needs_seam
@pytest.mark.parametrize(
    "window",
    [("06-15", "06-30"), ("01-01", "12-31"), ("12-20", "02-15"), ("07-04", "07-04")],
)
def test_kept_plus_dropped_always_reconciles_to_every_chain_the_pull_produced(window) -> None:
    """The identity that makes the summary trustworthy, in the same spirit as
    F7-S1's `included + excluded + filtered == pulled`: nothing may be lost
    between "chains this pull shaped" and "chains the window ruled on".

    The total is measured, not hard-coded — a full-year window admits every
    month-day by construction, so it *is* the chain count.
    """
    active, inactive = _split(_MIXED)
    total = len(shape_raw_pull(active, inactive, Config(), AS_OF, "01-01", "12-31"))

    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *window)
    assert summary.kept == len(comps), (
        f"summary.kept={summary.kept} but the call returned {len(comps)} comps"
    )
    assert summary.kept + summary.dropped_outside_window == total, (
        f"window {window[0]}..{window[1]}: kept={summary.kept} + "
        f"dropped={summary.dropped_outside_window} != {total} chains in the pull — records are "
        "vanishing between shaping and the window filter, or being counted twice"
    )


@needs_seam
def test_a_pull_entirely_inside_the_window_reports_zero_dropped() -> None:
    """The zero case, stated explicitly so that "always report 0" and "report
    the real count" cannot both pass. Its partner is the all-dropped case
    below; between them a constant is impossible."""
    active, inactive = _split({k: v for k, v in _MIXED.items() if "Inside" in k})
    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *_JUNE)
    assert (summary.kept, summary.dropped_outside_window) == (len(comps), 0)


@needs_seam
def test_a_pull_entirely_outside_the_window_reports_every_chain_dropped() -> None:
    """The 0-comp edge of the F4 flow, from the pipeline's side: an empty comp
    list with a non-zero drop count is a *diagnosable* empty result. An empty
    comp list with no count at all is the state the user cannot act on."""
    active, inactive = _split({k: v for k, v in _MIXED.items() if "Padding" in k})
    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *_JUNE)
    assert comps == ()
    assert (summary.kept, summary.dropped_outside_window) == (0, 4)


@needs_seam
def test_an_empty_pull_summarizes_as_zero_and_zero_rather_than_failing() -> None:
    """A pull that returned nothing is a normal outcome (the F4 epic's 0-comp
    edge), not an error, and the summary must survive it — this is the path a
    "widen the radius" empty state reads."""
    comps, summary = shape_raw_pull_with_summary([], [], Config(), AS_OF, *_JUNE)
    assert comps == ()
    assert (summary.kept, summary.dropped_outside_window) == (0, 0)


# ---------------------------------------------------------------------------
# the two entry points must be one computation
# ---------------------------------------------------------------------------


@needs_seam
@pytest.mark.parametrize("window", [("06-15", "06-30"), ("12-20", "02-15"), ("01-01", "12-31")])
def test_the_summarizing_entry_point_returns_exactly_what_shape_raw_pull_returns(window) -> None:
    """Requirement 1 of the seam proposal. If the two ever disagree, the count
    describes a shaping run nobody else did — and `storage/pulls.py` calls
    `shape_raw_pull`, so the numbers a user sees would come from the other
    one."""
    active, inactive = _split(_MIXED)
    comps, _ = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *window)
    assert comps == shape_raw_pull(active, inactive, Config(), AS_OF, *window), (
        "the summarizing entry point and `shape_raw_pull` returned different comps for the same "
        "inputs — they must be one computation, not two implementations"
    )


# ---------------------------------------------------------------------------
# the real committed pull
# ---------------------------------------------------------------------------

_LIVE_SAMPLES = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
_REAL_AS_OF = date(2026, 7, 27)


def _real_records() -> tuple[list[dict], list[dict]]:
    active = json.loads((_LIVE_SAMPLES / "fe9de5158f036802.json").read_text(encoding="utf-8"))
    inactive = json.loads((_LIVE_SAMPLES / "6327600317b11d16.json").read_text(encoding="utf-8"))
    return active, inactive


@needs_seam
@pytest.mark.parametrize(
    ("window", "kept", "dropped"),
    [
        (("06-15", "06-30"), 28, 539),
        (("12-20", "02-15"), 72, 495),
        (("01-01", "12-31"), 567, 0),
    ],
)
def test_the_real_pulls_padding_is_counted_and_it_is_most_of_the_pull(window, kept, dropped) -> None:
    """The AC's second clause on real data, and the reason it is in the AC.

    A 16-day June window over the committed 539-record pull keeps 28 comps and
    discards 539 chains — **95% of what the pull paid for is padding**, by
    design (spec §3.2 pads +/-90 days on both sides). A pipeline that silently
    throws away 95% of its evidence with no count is one where a query-planner
    or stitcher regression that quietly widened the drop set would look exactly
    like a thin market.
    """
    active, inactive = _real_records()
    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), _REAL_AS_OF, *window)

    assert (summary.kept, summary.dropped_outside_window) == (kept, dropped), (
        f"window {window[0]}..{window[1]} on the committed pull: got "
        f"kept={summary.kept}, dropped={summary.dropped_outside_window}; expected {kept}/{dropped}"
    )
    assert summary.kept == len(comps)
    assert summary.kept + summary.dropped_outside_window == 567, (
        "the committed pull shapes to 567 chains; kept + dropped must account for all of them"
    )
