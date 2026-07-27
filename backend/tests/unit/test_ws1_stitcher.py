"""WS-1 — F4-S3 [INVARIANT] stitcher, minimal (no T-S1 golden-file exhaustive
coverage — that rides with F4-S3's own later QA dispatch per PM scope
ruling, QUEUE.md row 6). QA-authored, written RED before any implementation
exists (AGENT_QA.md protocol).

Uses the same ``rentcomp.pipeline.shape.shape_raw_pull`` seam pinned in
``test_ws1_record_shaping.py`` — see that file's docstring for the full
contract and the reasoning for testing stitching through the composed
entry point rather than a separate `stitch()` import.

Threshold note (QUEUE.md row 9): "42d strict `<`" — gap == 42 does NOT
merge, gap == 41 DOES.
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

FULL_YEAR = ("01-01", "12-31")


def _raw(
    *,
    id_: str,
    address: str,
    listed: str,
    removed: str | None,
    price: float = 2000.0,
    sqft: float | None = 1000.0,
) -> dict:
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
        "squareFootage": sqft,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }


def _record_with_two_spells(address: str, gap_days: int) -> dict:
    """One record whose ``history`` holds a prior spell, and whose top-level
    fields describe the current one, separated by exactly ``gap_days``."""
    first_listed = date(2024, 1, 1)
    first_removed = first_listed + timedelta(days=30)
    second_listed = first_removed + timedelta(days=gap_days)
    second_removed = second_listed + timedelta(days=20)
    rec = _raw(
        id_="two-spell",
        address=address,
        listed=second_listed.isoformat(),
        removed=second_removed.isoformat(),
        price=1900.0,  # lower than the first spell's price -> a legitimate cut
    )
    rec["history"] = {
        first_listed.isoformat(): {
            "event": "Rental Listing",
            "price": 2000,
            "listingType": "Standard",
            "listedDate": f"{first_listed.isoformat()}T00:00:00.000Z",
            "removedDate": f"{first_removed.isoformat()}T00:00:00.000Z",
            "daysOnMarket": 30,
        }
    }
    return rec, first_listed, second_removed


def test_shape_module_exists() -> None:
    assert _IMPORT_ERROR is None, (
        f"rentcomp.pipeline.shape.shape_raw_pull is required: {_IMPORT_ERROR}"
    )


@needs_shape
def test_gap_of_41_days_merges_into_one_stitched_comp() -> None:
    rec, first_listed, _ = _record_with_two_spells("300 W Gap Ave", gap_days=41)
    comps = shape_raw_pull([rec], [], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 1, f"gap=41d (< 42d threshold) must merge; got {len(comps)} comps"
    assert comps[0].initial_ask == 2000.0, "initialAsk must be the FIRST spell's price"
    assert comps[0].relist_count == 1


@needs_shape
def test_gap_of_42_days_does_not_merge() -> None:
    rec, first_listed, _ = _record_with_two_spells("301 W Gap Ave", gap_days=42)
    comps = shape_raw_pull([rec], [], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 2, (
        f"gap=42d (the threshold itself, strict '<' per QUEUE.md row 9) must NOT merge; "
        f"got {len(comps)} comps"
    )


@needs_shape
def test_merged_dom_counts_the_gap_days() -> None:
    """F4-S3 AC: "DOM of a merged chain >= sum of spell DOMs" — the exact
    definition is effectiveDOM = final removal - first listed, gap days
    included (story text, not an approximation)."""
    rec, first_listed, second_removed = _record_with_two_spells("302 W Gap Ave", gap_days=10)
    comps = shape_raw_pull([rec], [], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 1
    expected_dom = (second_removed - first_listed).days
    assert comps[0].effective_dom == expected_dom, (
        f"effective_dom must be final_removal - first_listed INCLUDING the gap "
        f"({expected_dom}d), got {comps[0].effective_dom}"
    )
    sum_of_spell_doms = 30 + 20  # the two individual spell lengths
    assert comps[0].effective_dom >= sum_of_spell_doms


@needs_shape
def test_a_chain_ending_active_is_censored_with_dom_measured_at_the_pull_date() -> None:
    """"a chain ending in an active spell is censored with DOM = today -
    first listed" — "today" is owner ruling 1's `as_of` (the pull date),
    never the wall clock."""
    as_of = date(2024, 5, 1)
    first_listed = date(2024, 4, 1)
    rec = _raw(id_="still-active", address="303 W Gap Ave", listed=first_listed.isoformat(), removed=None)
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1
    comp = comps[0]
    assert comp.censored is True
    assert comp.effective_dom == (as_of - first_listed).days


@needs_shape
def test_relist_at_a_lower_price_is_recorded_as_a_cut() -> None:
    """"a re-list at a lower price IS a cut" — the merged chain's
    cut_history must contain it, not just its relist_count."""
    rec, _, _ = _record_with_two_spells("304 W Gap Ave", gap_days=5)  # 2000 -> 1900
    comps = shape_raw_pull([rec], [], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 1
    comp = comps[0]
    assert len(comp.cut_history) >= 1, "a lower-priced re-list must appear in cut_history"
    prices = {(cut.from_price, cut.to_price) for cut in comp.cut_history}
    assert (2000.0, 1900.0) in prices


@needs_shape
def test_a_smaller_configured_threshold_stops_merging_a_gap_that_used_to_merge() -> None:
    """F4-S3 AC spirit: "threshold 0 => no merging." Config's floor is 7
    (§2.3), so a literal in-process 0 is out of contract (Config would raise
    ValueError) — asserted instead via the smallest legal threshold: a gap
    that merges under the 42d default must NOT merge once the knob is
    lowered below that gap, proving the threshold is actually read rather
    than hardcoded."""
    cfg = Config(stitch_gap_days=7)
    rec, _, _ = _record_with_two_spells("305 W Gap Ave", gap_days=10)  # merges under default (42d)
    comps = shape_raw_pull([rec], [], cfg, date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 2, "gap=10d must not merge once stitch_gap_days is configured to 7"
