"""WS-1 (F4-S2/S4) — developer-authored supplement to QA's
`test_ws1_record_shaping.py` / `test_ws1_window_cohort.py`.

QA's plan pins the dedupe/stitch/classify/window seam contract exhaustively
through representative cases; this file covers two things that are real
risks in `pipeline/shape.py`'s implementation but weren't QA's job to pin
(AGENT_DEVELOPER.md protocol item 4 — "usually all of Layer 1"):

* a record with no `squareFootage` must still shape into a `StitchedComp`
  with `sqft=None` (never dropped, never a fabricated value — NORTH_STAR,
  domain.py's documented promise that 14.7% of the real pull has none).
* the year-agnostic window comparison must handle a WRAPPING range
  (e.g. Nov-Feb), not just the non-wrapping ranges QA's fixtures used.
"""

from __future__ import annotations

from datetime import date

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

FULL_YEAR = ("01-01", "12-31")


def _raw(*, id_: str, address: str, listed: str, removed: str | None, sqft: float | None = 1000.0) -> dict:
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
        "price": 2000.0,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }


def test_a_record_with_no_square_footage_still_shapes_with_sqft_none() -> None:
    rec = _raw(id_="no-sqft", address="1 W No Sqft Ave", listed="2024-05-01", removed="2024-05-20", sqft=None)
    comps = shape_raw_pull([rec], [], Config(), date(2024, 6, 1), *FULL_YEAR)
    assert len(comps) == 1
    assert comps[0].sqft is None
    assert comps[0].psf is None, "no sqft must mean no psf, never a fabricated value"


def test_a_wrapping_window_keeps_a_start_date_inside_the_wrap() -> None:
    """A Nov 15 - Feb 15 window (start month-day > end month-day) must treat
    Dec/Jan/early-Feb starts as inside, and a July start as outside."""
    winter_window = ("11-15", "02-15")
    inside = _raw(id_="inside", address="2 W Wrap Ave", listed="2024-01-10", removed="2024-01-20")
    outside = _raw(id_="outside", address="3 W Wrap Ave", listed="2024-07-01", removed="2024-07-10")
    comps = shape_raw_pull([inside, outside], [], Config(), date(2024, 8, 1), *winter_window)
    assert {c.address for c in comps} == {"2 W Wrap Ave"}


def test_a_wrapping_window_keeps_a_start_date_at_the_late_edge() -> None:
    winter_window = ("11-15", "02-15")
    edge = _raw(id_="edge", address="4 W Wrap Ave", listed="2024-12-01", removed="2024-12-10")
    comps = shape_raw_pull([edge], [], Config(), date(2024, 12, 20), *winter_window)
    assert len(comps) == 1
