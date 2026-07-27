"""WS-1 — F4-S4 [INVARIANT] window filter + cohort assignment, minimal.
QA-authored, written RED before any implementation exists.

Uses the ``rentcomp.pipeline.shape.shape_raw_pull`` seam pinned in
``test_ws1_record_shaping.py`` — ``window_start_mmdd``/``window_end_mmdd``
are that contract's year-agnostic month-day window bounds.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from rentcomp.storage.config import Config

try:
    from rentcomp.pipeline.shape import shape_raw_pull

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - red state before WS-1 lands
    _IMPORT_ERROR = exc

needs_shape = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.pipeline.shape is not importable yet (WS-1 red): {_IMPORT_ERROR}",
)

#: A summer window, deliberately not the full year, so in/out-of-window
#: records are genuinely distinguishable.
SUMMER_WINDOW = ("06-01", "08-31")


def _raw(*, id_: str, address: str, listed: str, removed: str | None, history: dict | None = None) -> dict:
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
        "bedrooms": 3,
        "bathrooms": 2,
        "squareFootage": 1000,
        "status": "Active" if removed is None else "Inactive",
        "price": 2000.0,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": history or {},
    }


def test_shape_module_exists() -> None:
    assert _IMPORT_ERROR is None, f"rentcomp.pipeline.shape.shape_raw_pull is required: {_IMPORT_ERROR}"


@needs_shape
def test_a_listing_start_inside_the_window_is_kept_with_cohort_year_of_its_start() -> None:
    rec = _raw(id_="in-window", address="1 W Window Ave", listed="2024-07-15", removed="2024-08-01")
    comps = shape_raw_pull([rec], [], Config(), date(2024, 9, 1), *SUMMER_WINDOW)
    assert len(comps) == 1
    assert comps[0].cohort_year == 2024


@needs_shape
def test_a_padding_only_listing_outside_the_window_is_dropped() -> None:
    """"padding-only records (pulled but stitched-start outside) are
    dropped" — a March listing is outside a Jun-Aug window."""
    rec = _raw(id_="padding-only", address="2 W Window Ave", listed="2024-03-01", removed="2024-03-20")
    comps = shape_raw_pull([rec], [], Config(), date(2024, 9, 1), *SUMMER_WINDOW)
    assert len(comps) == 0, f"a March listing must be dropped by a Jun-Aug window, got {len(comps)} comps"


@needs_shape
def test_a_stitched_chain_is_kept_or_dropped_by_its_stitched_start_not_any_individual_spell() -> None:
    """F4-S4 AC, verbatim: "a listing re-listed inside the window but
    originally listed before it is kept iff its stitched start is inside
    the window." Two spells 5 days apart (well under the 42d stitch
    threshold, so they merge): the ORIGINAL listing is outside the window,
    the re-list is inside it. Per the AC's "iff", the merged record is
    DROPPED — its stitched start (the original date) governs, not the part
    that happens to poke into the window."""
    original_listed = date(2024, 5, 20)  # outside Jun-Aug
    original_removed = original_listed + timedelta(days=10)
    relist = original_removed + timedelta(days=5)  # 2024-06-04, inside Jun-Aug
    rec = _raw(id_="straddle", address="3 W Window Ave", listed=relist.isoformat(), removed=None)
    rec["history"] = {
        original_listed.isoformat(): {
            "event": "Rental Listing",
            "price": 2000,
            "listingType": "Standard",
            "listedDate": f"{original_listed.isoformat()}T00:00:00.000Z",
            "removedDate": f"{original_removed.isoformat()}T00:00:00.000Z",
            "daysOnMarket": 10,
        }
    }
    comps = shape_raw_pull([rec], [], Config(), date(2024, 9, 1), *SUMMER_WINDOW)
    assert len(comps) == 0, (
        "a chain whose STITCHED START is outside the window must be dropped even though its "
        "re-list date falls inside it (F4-S4 AC's 'iff', straddle case)"
    )


@needs_shape
def test_the_mirror_straddle_case_is_kept_with_the_earlier_year_as_cohort() -> None:
    """Same shape, reversed: stitched start inside the window, the merged
    chain's later activity trails off past the window edge — must be KEPT,
    cohort_year = the start's year."""
    original_listed = date(2024, 8, 20)  # inside Jun-Aug
    original_removed = original_listed + timedelta(days=10)
    relist = original_removed + timedelta(days=5)  # trails into September, outside the window
    relist_removed = relist + timedelta(days=5)
    rec = _raw(id_="straddle-2", address="4 W Window Ave", listed=relist.isoformat(), removed=relist_removed.isoformat())
    rec["history"] = {
        original_listed.isoformat(): {
            "event": "Rental Listing",
            "price": 2000,
            "listingType": "Standard",
            "listedDate": f"{original_listed.isoformat()}T00:00:00.000Z",
            "removedDate": f"{original_removed.isoformat()}T00:00:00.000Z",
            "daysOnMarket": 10,
        }
    }
    comps = shape_raw_pull([rec], [], Config(), date(2024, 9, 10), *SUMMER_WINDOW)
    assert len(comps) == 1, "stitched start inside the window must be kept even if the chain trails past it"
    assert comps[0].cohort_year == 2024
