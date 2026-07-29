"""F4-S4 developer tests for the padding debug summary — the claims QA's
`test_f4s4_padding_debug_summary.py` deliberately left unpinned.

QA's file pins the AC: the counts are right on a mixed fixture, on the empty
pull, on the all-in and all-out edges, on the real committed pull, and
`kept + dropped_outside_window` reconciles against every chain the pull
shaped. Its own docstring then says it pins "nothing about `Config`,
ordering, or field names beyond the two counts". Three of those absences hide
a claim worth locking, and this file locks them:

1. **The count follows chain *formation*, not a constant.** QA proves the
   count is not a raw-record count by asserting `!= 6` on a unit that stitches
   3 records into 1 chain. The mirror is stronger and is here: hand the same
   unit a `Config` under which those 3 records genuinely *are* 3 chains, and
   the count must rise to 6. Between the two, an implementation that counts
   records, counts chains, or hard-codes either number is distinguishable.

2. **What is outside the reconciliation identity.** A group that yields no
   spells never reaches the window, so it is neither kept nor dropped — the
   summary accounts for the window's decisions, not for parse losses. That is
   a real limit on how much `kept + dropped` can be trusted as "everything
   the pull contained", and it should fail loudly if someone later widens
   `dropped_outside_window` to mean "everything we threw away".

3. **The two entry points agree on the real pull**, not only on the 7-unit
   synthetic fixture. `storage/pulls.py` calls `shape_raw_pull`; if the
   delegation were ever unwound into two implementations, the synthetic
   fixture is exactly the input least likely to catch it.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import ShapingSummary, shape_raw_pull, shape_raw_pull_with_summary
from rentcomp.storage.config import Config

AS_OF = date(2027, 6, 1)
JUNE = ("06-15", "06-30")
FULL_YEAR = ("01-01", "12-31")


def _record(id_: str, address: str, listed: date | None, removed: date | None) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": None,
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 2,
        "bathrooms": 1,
        "squareFootage": 900,
        "status": "Active" if removed is None else "Inactive",
        "price": 2000.0,
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z" if listed else None,
        "removedDate": f"{removed.isoformat()}T00:00:00.000Z" if removed else None,
        "history": {},
    }


def _split(units: dict[str, list[tuple[date | None, date | None]]]) -> tuple[list[dict], list[dict]]:
    active: list[dict] = []
    inactive: list[dict] = []
    for address, segments in units.items():
        for i, (listed, removed) in enumerate(segments):
            record = _record(f"{address}-{i}", address, listed, removed)
            (active if removed is None else inactive).append(record)
    return active, inactive


#: One unit inside a Jun 15-30 window, and one padding unit whose three
#: records stitch into a single chain at the default 42-day threshold and into
#: three separate chains at a 7-day one. The gaps are 19 and 18 days, chosen to
#: sit strictly between the two thresholds.
_STITCH_SENSITIVE = {
    "1 Inside St": [(date(2025, 6, 15), date(2025, 7, 20))],
    "2 Padding St": [
        (date(2025, 1, 6), date(2025, 2, 10)),
        (date(2025, 3, 1), date(2025, 4, 2)),
        (date(2025, 4, 20), date(2025, 5, 30)),
    ],
}


@pytest.mark.parametrize(
    ("stitch_gap_days", "expected_dropped", "why"),
    [
        (42, 1, "19d and 18d off market both merge, so the padding unit is one chain"),
        (7, 3, "19d and 18d both clear a 7d threshold, so the padding unit is three chains"),
    ],
)
def test_the_drop_count_follows_how_the_pull_actually_stitched(
    stitch_gap_days, expected_dropped, why
) -> None:
    """The count is of chains — proven by changing how many chains the *same*
    records form and watching it move.

    This is the direction QA's `!= 6` assertion cannot reach: that one rules
    out a record count, this one rules out any count that ignores stitching
    (including a constant, and including one taken before the stitch step).
    """
    active, inactive = _split(_STITCH_SENSITIVE)
    config = Config(stitch_gap_days=stitch_gap_days)

    comps, summary = shape_raw_pull_with_summary(active, inactive, config, AS_OF, *JUNE)
    total = len(shape_raw_pull(active, inactive, config, AS_OF, *FULL_YEAR))

    assert summary.kept == len(comps) == 1
    assert summary.dropped_outside_window == expected_dropped, why
    assert summary.kept + summary.dropped_outside_window == total


def test_a_group_that_yields_no_spells_is_counted_in_neither_half() -> None:
    """The documented limit of the reconciliation identity.

    A record with no parseable `listedDate` produces no spells, so its group
    never reaches the window and is neither kept nor dropped-outside-window.
    `dropped_outside_window` means "the window ruled against this chain", not
    "we discarded this record" — a distinction that matters the moment anyone
    reads the summary as a full account of the pull.
    """
    active, inactive = _split(
        {
            "1 Inside St": [(date(2025, 6, 15), date(2025, 7, 20))],
            "2 Padding St": [(date(2025, 4, 2), date(2025, 5, 9))],
            "3 Undated St": [(None, date(2025, 6, 20))],
        }
    )
    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), AS_OF, *JUNE)

    assert (summary.kept, summary.dropped_outside_window) == (1, 1), (
        "the undated record leaked into a count: it never reached the window, so it belongs to "
        "neither half"
    )
    assert summary.kept == len(comps)


def test_the_summary_cannot_be_edited_after_the_fact() -> None:
    """A debug count exists to be evidence about a run that already happened;
    a later stage that could rewrite it would make it evidence about nothing.
    """
    _, summary = shape_raw_pull_with_summary([], [], Config(), AS_OF, *JUNE)
    assert isinstance(summary, ShapingSummary)
    with pytest.raises(dataclasses.FrozenInstanceError):
        summary.dropped_outside_window = 99  # type: ignore[misc]


_LIVE_SAMPLES = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
_REAL_AS_OF = date(2026, 7, 27)


@pytest.mark.parametrize("window", [JUNE, ("12-20", "02-15"), FULL_YEAR])
def test_both_entry_points_shape_the_real_pull_identically(window) -> None:
    """The delegation, on the 539-record pull rather than a 7-unit fixture.

    `storage/pulls.py` calls `shape_raw_pull`, so this is the tuple a user's
    numbers are actually built from; the summary is only trustworthy if it
    describes *that* run.
    """
    active = json.loads((_LIVE_SAMPLES / "fe9de5158f036802.json").read_text(encoding="utf-8"))
    inactive = json.loads((_LIVE_SAMPLES / "6327600317b11d16.json").read_text(encoding="utf-8"))

    comps, summary = shape_raw_pull_with_summary(active, inactive, Config(), _REAL_AS_OF, *window)
    assert comps == shape_raw_pull(active, inactive, Config(), _REAL_AS_OF, *window)
    assert summary.kept == len(comps)
