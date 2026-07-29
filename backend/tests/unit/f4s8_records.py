"""F4-S8 — shared raw-record builders for the QA specs. Not a test module.

Every F4-S8 acceptance criterion is stated over *spells*, and the only seam
that turns raw records into classified comps is
``rentcomp.pipeline.shape.shape_raw_pull`` (see that module's docstring: the
internal stages are a `[DEFAULT]` and are deliberately not importable). So
these helpers build the raw RentCast-shaped dicts that seam consumes, and the
specs vary only ``as_of`` / ``Config`` / the spell dates — which is exactly
the AC's "state transitions are pure functions of (spells, today, config)"
expressed against the contract that exists, rather than against a
``classify(spells, today, config)`` signature QA would be inventing.

Field shapes here are copied from the committed real pull
(``fixtures/live-samples/``), not imagined: ISO-8601 ``...T00:00:00.000Z``
timestamps, ``addressLine1``/``addressLine2``, a ``history`` object keyed by
date. Zero network (D17) — these are literals.
"""

from __future__ import annotations

from datetime import date, timedelta

__all__ = [
    "FULL_YEAR",
    "closed_record",
    "iso",
    "open_record",
    "record",
    "spell_pair_with_gap",
]

#: A year-agnostic window that keeps every record (F4-S4's filter is a
#: different story's rule and must not silently drop this story's fixtures).
FULL_YEAR = ("01-01", "12-31")


def iso(day: date) -> str:
    """A date as RentCast writes it in the committed fixtures."""
    return f"{day.isoformat()}T00:00:00.000Z"


def record(
    *,
    id_: str,
    address: str,
    listed: date,
    removed: date | None,
    price: float = 2000.0,
    unit: str | None = None,
    sqft: float | None = 1000.0,
    history: dict | None = None,
) -> dict:
    """One raw listing record in the real pull's shape."""
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": unit,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 2,
        "bathrooms": 1,
        "squareFootage": sqft,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listingType": "Standard",
        "listedDate": iso(listed),
        "removedDate": iso(removed) if removed is not None else None,
        "createdDate": iso(listed),
        "lastSeenDate": iso(removed or listed),
        "daysOnMarket": None,
        "history": history or {},
    }


def closed_record(
    *, id_: str, address: str, listed: date, removed: date, **kwargs
) -> dict:
    return record(id_=id_, address=address, listed=listed, removed=removed, **kwargs)


def open_record(*, id_: str, address: str, listed: date, **kwargs) -> dict:
    return record(id_=id_, address=address, listed=listed, removed=None, **kwargs)


def spell_pair_with_gap(
    *,
    address: str,
    first_listed: date,
    first_dom: int,
    gap_days: int,
    second_dom: int = 15,
) -> list[dict]:
    """Two listings of one unit, separated by exactly ``gap_days`` off market.

    The gap is measured the way the story measures it — from the first
    listing's removal to the second's listing date — so a spec can put a
    number directly on the 6-week floor and the 6-month ceiling without
    arithmetic in the test body.
    """
    first_removed = first_listed + timedelta(days=first_dom)
    relisted = first_removed + timedelta(days=gap_days)
    return [
        closed_record(
            id_=f"{address}-a", address=address, listed=first_listed, removed=first_removed
        ),
        closed_record(
            id_=f"{address}-b",
            address=address,
            listed=relisted,
            removed=relisted + timedelta(days=second_dom),
        ),
    ]
