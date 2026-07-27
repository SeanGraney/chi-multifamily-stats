"""WS-1 — F4-S8 [INVARIANT] removal classification + withdrawal-suspect flag,
minimal (no golden-file exhaustiveness — QUEUE.md row 6 PM scope ruling).
QA-authored, written RED before any implementation exists.

Uses the ``rentcomp.pipeline.shape.shape_raw_pull`` seam pinned in
``test_ws1_record_shaping.py``.

SCOPE TRIM (logged per AGENT_QA.md's boundaries): F4-S8's AC also names a
"provisional -> re-list -> stitched" transition path. That path is about
what happens across TWO separate pulls in time (a comp classified
provisional on pull 1, then re-appearing before pull 2) — not something
expressible through one ``shape_raw_pull`` call over one fixed set of raw
records with one ``as_of``. WS-1's PM scope explicitly permits skipping
T-S1 golden-file exhaustiveness for this story; this file covers the three
ladder states + withdrawal-suspect + the "clean confirmed, no suspect"
control case, and leaves the cross-pull transition to F4-S8's own later QA
dispatch, where it can be tested with two `as_of` values over one store.
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
def test_removed_less_than_7_days_ago_is_pending() -> None:
    as_of = date(2024, 6, 10)
    removed = as_of - timedelta(days=4)
    listed = removed - timedelta(days=20)
    rec = _raw(id_="p1", address="1 W Pend St", listed=listed.isoformat(), removed=removed.isoformat())
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1
    assert comps[0].censored is False
    assert comps[0].removal_class == "pending", (
        f"removed 4d before as_of must classify as pending, got {comps[0].removal_class!r}"
    )


@needs_shape
def test_removed_at_least_7_days_ago_is_provisional() -> None:
    as_of = date(2024, 6, 10)
    removed = as_of - timedelta(days=7)
    listed = removed - timedelta(days=20)
    rec = _raw(id_="p2", address="2 W Prov St", listed=listed.isoformat(), removed=removed.isoformat())
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1
    assert comps[0].removal_class == "provisional", (
        f"removed exactly 7d before as_of (>= the 7d floor) must classify as provisional, "
        f"got {comps[0].removal_class!r}"
    )


@needs_shape
def test_removed_at_least_42_days_ago_with_no_relist_is_confirmed() -> None:
    as_of = date(2024, 6, 10)
    removed = as_of - timedelta(days=42)
    listed = removed - timedelta(days=20)
    rec = _raw(id_="p3", address="3 W Conf St", listed=listed.isoformat(), removed=removed.isoformat())
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1
    assert comps[0].removal_class == "confirmed", (
        f"removed 42d ago with no re-list must classify as confirmed, got {comps[0].removal_class!r}"
    )
    assert comps[0].withdrawal_suspect is False


@needs_shape
def test_a_complete_spell_that_relists_6_weeks_to_6_months_later_is_withdrawal_suspect() -> None:
    """NORTH_STAR: "a listing that appeared to complete its spell but
    re-appeared 6 weeks-6 months later." Gap chosen (90d) is past the 42d
    stitch threshold (so the two spells stay SEPARATE StitchedComps, this is
    not a stitching test) and inside the 6wk(42d)-6mo(~183d) suspect window."""
    as_of = date(2024, 12, 1)
    first_listed = date(2024, 1, 1)
    first_removed = first_listed + timedelta(days=20)
    relist = first_removed + timedelta(days=90)
    relist_removed = relist + timedelta(days=15)
    older = _raw(
        id_="suspect-1", address="4 W Suspect Ave", listed=first_listed.isoformat(), removed=first_removed.isoformat()
    )
    newer = _raw(
        id_="suspect-2", address="4 W Suspect Ave", listed=relist.isoformat(), removed=relist_removed.isoformat()
    )
    comps = shape_raw_pull([older, newer], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 2, f"a 90d gap is past the 42d stitch threshold; must stay two comps, got {len(comps)}"
    original = next(c for c in comps if c.removal_class == "confirmed" and c.effective_dom == 20)
    assert original.withdrawal_suspect is True, (
        "the earlier complete spell must be flagged withdrawal_suspect after a 90d-later re-list"
    )


@needs_shape
def test_a_clean_confirmed_removal_with_no_relist_is_not_withdrawal_suspect() -> None:
    """Control case: nothing re-listed at all -> withdrawal_suspect stays False."""
    as_of = date(2024, 12, 1)
    removed = date(2024, 6, 1)
    listed = removed - timedelta(days=25)
    rec = _raw(id_="clean", address="5 W Clean St", listed=listed.isoformat(), removed=removed.isoformat())
    comps = shape_raw_pull([rec], [], Config(), as_of, *FULL_YEAR)
    assert len(comps) == 1
    assert comps[0].removal_class == "confirmed"
    assert comps[0].withdrawal_suspect is False


@needs_shape
def test_a_relist_more_than_6_months_later_is_not_withdrawal_suspect() -> None:
    """Past the 6-month suspect window, a re-list reads as a genuinely
    separate market cycle, not doubt about whether the original lease
    happened."""
    as_of = date(2025, 6, 1)
    first_listed = date(2024, 1, 1)
    first_removed = first_listed + timedelta(days=20)
    relist = first_removed + timedelta(days=250)  # well past 6mo (~183d)
    older = _raw(
        id_="notsuspect-1", address="6 W NotSuspect Ave", listed=first_listed.isoformat(), removed=first_removed.isoformat()
    )
    newer = _raw(id_="notsuspect-2", address="6 W NotSuspect Ave", listed=relist.isoformat(), removed=None)
    comps = shape_raw_pull([older, newer], [], Config(), as_of, *FULL_YEAR)
    original = next(c for c in comps if c.effective_dom == 20)
    assert original.withdrawal_suspect is False, "a re-list 250d later (past 6mo) must not flag as suspect"
