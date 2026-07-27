"""WS-1 — F4-S2 [INVARIANT] dedupe + spell extraction. QA-authored, written
RED before any implementation exists (AGENT_QA.md protocol).

WS-1's scope note (QUEUE.md row 6, PM ruling): build F4-S2 "minimal but real"
— get it right for real RentCast records, but T-S1's golden-file exhaustive
pathological-case coverage is F4-S3's own later QA dispatch, not this one.

CONTRACT PINNED HERE (QA-PROPOSED, no owning ADR names it — flagged in the
handoff, needs PM confirmation before the developer builds against it):

    rentcomp.pipeline.shape.shape_raw_pull(
        active_records: Sequence[dict],
        inactive_records: Sequence[dict],
        config: Config,
        as_of: date,
        window_start_mmdd: str,   # "MM-DD", year-agnostic (F4-S4)
        window_end_mmdd: str,     # "MM-DD"
    ) -> tuple[StitchedComp, ...]

One composed entry point over dedupe -> spell extraction -> stitch (F4-S3) ->
classify (F4-S8) -> window filter + cohort assign (F4-S4). QA picked ONE
seam rather than five separate importable stage functions deliberately: the
individual stage *behaviours* (42-day gap threshold, removal ladder,
year-agnostic window) are exhaustively tested through this one entry point
by varying its inputs, per AGENT_QA.md's "test the outcome the formula
requires, not an implementation detail" — pinning five internal function
signatures the developer has not chosen yet would be over-specifying a
[DEFAULT]. Records are plain dicts matching the real RentCast wire shape
(verified against ``fixtures/live-samples/fe9de5158f036802.json`` and
``6327600317b11d16.json`` — see this repo's committed samples), not an
invented shape.

This file owns F4-S2 (dedupe on listing id; group on normalized
address+unit; spell extraction from top-level fields AND ``history``
events). F4-S3/S4/S8 have their own files.

EXPECTED RED STATE TODAY: ``test_shape_module_exists`` FAILS naming the
missing module; every other test SKIPS.
"""

from __future__ import annotations

from datetime import date

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

FULL_YEAR = ("01-01", "12-31")  # window that keeps everything in scope for this file


def _raw(
    *,
    id_: str,
    address: str,
    unit: str | None,
    listed: str,
    removed: str | None,
    price: float = 2000.0,
    sqft: float | None = 1000.0,
    beds: float = 3,
    baths: float = 2,
    status: str | None = None,
    history: dict | None = None,
    lat: float = 41.83,
    lng: float = -87.66,
) -> dict:
    """One raw record, shaped like RentCast's real payload (both fixtures use
    this exact field set — see the module docstring)."""
    formatted = f"{address}, Unit {unit}, Chicago, IL 60609" if unit else f"{address}, Chicago, IL 60609"
    return {
        "id": id_,
        "formattedAddress": formatted,
        "addressLine1": address,
        "addressLine2": f"Unit {unit}" if unit else None,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": lat,
        "longitude": lng,
        "propertyType": "Apartment",
        "bedrooms": beds,
        "bathrooms": baths,
        "squareFootage": sqft,
        "status": status or ("Active" if removed is None else "Inactive"),
        "price": price,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": history or {},
    }


def test_shape_module_exists() -> None:
    assert _IMPORT_ERROR is None, (
        "rentcomp.pipeline.shape.shape_raw_pull is required by WS-1's record-shaping "
        f"contract (see this module's docstring for the proposed signature): {_IMPORT_ERROR}"
    )


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


@needs_shape
def test_two_records_at_the_same_normalized_address_unit_merge_into_one_group() -> None:
    """F4-S2 AC: "two records at the same normalized address+unit merge into
    one group." Two different listing ids, address differs only by casing
    and whitespace — the same underlying unit scraped/re-listed twice."""
    active = [
        _raw(id_="listing-A", address="3651  S  Wood St", unit="2", listed="2024-06-01", removed=None),
        _raw(id_="listing-B", address="3651 s wood st", unit="2", listed="2024-06-01", removed=None),
    ]
    comps = shape_raw_pull(active, [], Config(), date(2024, 7, 1), *FULL_YEAR)
    assert len(comps) == 1, (
        f"expected one merged StitchedComp for the same normalized address+unit, got {len(comps)}"
    )


@needs_shape
def test_dedupe_is_on_listing_id_not_positional_order() -> None:
    """Feeding the same listing id twice (e.g. present in both the Active and
    Inactive pulls because of a status flip mid-pagination) must not double
    the record."""
    rec = _raw(id_="same-id", address="100 W Test St", unit=None, listed="2024-05-01", removed=None)
    comps = shape_raw_pull([rec], [dict(rec)], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 1, f"same listing id appearing twice must dedupe to one comp, got {len(comps)}"


# ---------------------------------------------------------------------------
# spell extraction (top-level + history)
# ---------------------------------------------------------------------------


@needs_shape
def test_two_prior_spells_in_history_yield_three_stitched_comps_when_gaps_exceed_the_stitch_threshold() -> None:
    """F4-S2 AC: "a record whose history holds two prior spells yields three
    spell rows." Asserted at the composed output: with gaps well past the
    42-day stitch threshold and cohort years kept distinct, spell extraction
    having found 3 rows is the only way 3 separate StitchedComps can result.
    A record that only ever reported its *current* spell would yield 1."""
    history = {
        "2022-03-01": {
            "event": "Rental Listing",
            "price": 1800,
            "listingType": "Standard",
            "listedDate": "2022-03-01T00:00:00.000Z",
            "removedDate": "2022-04-01T00:00:00.000Z",
            "daysOnMarket": 31,
        },
        "2023-03-01": {
            "event": "Rental Listing",
            "price": 1850,
            "listingType": "Standard",
            "listedDate": "2023-03-01T00:00:00.000Z",
            "removedDate": "2023-04-01T00:00:00.000Z",
            "daysOnMarket": 31,
        },
    }
    rec = _raw(
        id_="triple-spell",
        address="200 W History Ave",
        unit=None,
        listed="2024-03-01",
        removed=None,
        history=history,
    )
    comps = shape_raw_pull([rec], [], Config(), date(2024, 4, 1), *FULL_YEAR)
    assert len(comps) == 3, (
        f"a record with 2 prior history spells + 1 current spell, all gapped past the "
        f"stitch threshold, must yield 3 separate StitchedComp rows; got {len(comps)}"
    )
    assert sorted(c.cohort_year for c in comps) == [2022, 2023, 2024]
