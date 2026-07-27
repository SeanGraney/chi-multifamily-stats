"""Record shaping — F4-S2/F4-S3/F4-S4/F4-S8, composed behind one seam.

``shape_raw_pull`` is the one entry point WS-1 pins (QUEUE.md row 6 / the
WS-1 dispatch's "contract item 1"): dedupe -> spell extraction -> 42-day-gap
stitch -> three-state removal classification + withdrawal-suspect -> window
filter + cohort assignment. Internal structure below this seam is a
[DEFAULT] — nothing in the ADR or story docs names five separate stage
functions, so this module keeps everything behind one composed call rather
than inventing importable internals nobody pinned.

INPUT SHAPE
-----------
``active_records``/``inactive_records`` are the raw parsed JSON lists from
RentCast's ``/listings/rental/long-term`` response shape (verified against
the committed ``fixtures/live-samples/`` samples) — plain dicts, not a
`dto.py` model. Every field but ``id`` is treated as optional/best-effort:
this module never raises on a record missing ``bedrooms``/``bathrooms`` (the
real pull never omits them, per the gate's own fixtures) or
``squareFootage`` (14.7% of the real pull has none — those comps still
shape, with ``sqft=None``, exactly like `models/domain.py` promises).

ALGORITHM (one pass, four stages, all inside this function/its helpers)
-------------------------------------------------------------------------
1. **Dedupe** on ``id`` across both lists (F4-S2) — a listing id repeated
   across the Active/Inactive pulls (a status flip mid-pagination) is one
   record, not two.
2. **Group** the deduped records by normalized address+unit
   (`pipeline.keys.comp_key` — the same identity primitive the rest of the
   system uses, not a second normalization rule).
3. **Spell extraction**: every group's record(s) contribute one spell per
   top-level (listedDate/removedDate/price) plus one spell per ``history``
   entry. Spells that describe the exact same (listed, removed) pair
   (a record's current top-level spell almost always duplicates its own
   last ``history`` entry, in the real data) collapse to one.
4. **Stitch** (F4-S3): spells within one group, sorted chronologically, merge
   into a chain whenever the next spell's ``listedDate`` is *less than*
   ``config.stitch_gap_days`` after the previous spell's ``removedDate``
   (strict ``<`` — a gap exactly at the threshold does NOT merge). Each
   maximal chain becomes one `StitchedComp` candidate.
5. **Classify** (F4-S8): a chain ending in an active spell is ``censored``
   (removal_class=None), DOM measured to ``as_of``. A chain ending in a
   removal is classified off ``as_of - removed_date``: < ``provisional_lease_days``
   -> pending (excluded downstream), < 42d -> provisional (counted+marked),
   else -> confirmed. A confirmed spell whose group re-lists 42d-6mo later
   (a *separate* chain, since anything closer would have stitched) is
   flagged ``withdrawal_suspect``.
6. **Window + cohort** (F4-S4): a chain survives only if its *stitched
   start*'s month-day falls inside ``[window_start_mmdd, window_end_mmdd]``
   (year-agnostic, wraparound-safe); ``cohort_year`` is that start's
   calendar year.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.pipeline.keys import comp_key
from rentcomp.storage.config import Config

__all__ = ["shape_raw_pull"]

#: F4-S8 [INVARIANT]: the confirmed rung of the removal ladder — a fixed
#: 42 days, from the story text, not a configurable knob (only
#: ``provisional_lease_days``, the pending/provisional boundary, is one).
CONFIRMED_REMOVAL_DAYS = 42

#: F4-S8 / NORTH_STAR: "6 weeks" — the withdrawal-suspect window's floor.
#: Fixed, like ``CONFIRMED_REMOVAL_DAYS`` above: two chains in the same group
#: only exist as *separate* StitchedComps because their gap already cleared
#: ``config.stitch_gap_days``, but the suspect window's own definition is a
#: real-world "6 weeks", independent of that knob.
WITHDRAWAL_SUSPECT_MIN_DAYS = 42

#: Approximate days/month used to turn ``withdrawal_suspect_months`` (a
#: config knob) into a day count. Not date-arithmetic-exact (no calendar
#: lookup) — "6 months" is already an approximation in the spec text.
_DAYS_PER_MONTH = 30


@dataclass(frozen=True, slots=True)
class _Spell:
    """One reported listing interval, before stitching."""

    listed: date
    removed: date | None
    price: float
    address: str
    unit: str | None
    lat: float
    lng: float
    beds: float
    baths: float | None
    sqft: float | None


def shape_raw_pull(
    active_records: Sequence[dict],
    inactive_records: Sequence[dict],
    config: Config,
    as_of: date,
    window_start_mmdd: str,
    window_end_mmdd: str,
) -> tuple[StitchedComp, ...]:
    """Dedupe -> spells -> stitch -> classify -> window filter + cohort.

    Deterministic: groups are visited in sorted-key order, so two calls over
    the same inputs produce the same tuple in the same order.
    """
    records = _dedupe_by_id(active_records, inactive_records)

    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        address = record.get("addressLine1") or record.get("formattedAddress") or ""
        key = comp_key(address, record.get("addressLine2"))
        groups[key].append(record)

    comps: list[StitchedComp] = []
    for key in sorted(groups):
        spells = _extract_spells(groups[key])
        if not spells:
            continue
        chains = _stitch(spells, config.stitch_gap_days)
        suspects = _withdrawal_suspect_flags(chains, config.withdrawal_suspect_months)
        for chain, suspect in zip(chains, suspects, strict=True):
            start = chain[0].listed
            if not _in_window(start, window_start_mmdd, window_end_mmdd):
                continue
            comps.append(_build_comp(chain, suspect, as_of, config))
    return tuple(comps)


def _dedupe_by_id(active: Sequence[dict], inactive: Sequence[dict]) -> list[dict]:
    """F4-S2: dedupe on listing id, not positional order or list membership."""
    seen: dict[object, dict] = {}
    for record in (*active, *inactive):
        rid = record.get("id")
        seen.setdefault(rid if rid is not None else id(record), record)
    return list(seen.values())


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def _extract_spells(records: Sequence[dict]) -> list[_Spell]:
    """F4-S2: one spell per top-level record, plus one per ``history`` entry.

    Deduplicated on ``(listed, removed)`` — a record's current top-level
    spell almost always restates its own latest ``history`` entry.
    """
    raw: list[_Spell] = []
    for record in records:
        address = record.get("addressLine1") or record.get("formattedAddress") or ""
        unit = record.get("addressLine2")
        lat = float(record.get("latitude") or 0.0)
        lng = float(record.get("longitude") or 0.0)
        beds = float(record["bedrooms"]) if record.get("bedrooms") is not None else 0.0
        baths = float(record["bathrooms"]) if record.get("bathrooms") is not None else None
        sqft = float(record["squareFootage"]) if record.get("squareFootage") is not None else None

        def _spell(listed_raw: str | None, removed_raw: str | None, price: object) -> _Spell | None:
            listed = _parse_date(listed_raw)
            if listed is None:
                return None
            return _Spell(
                listed=listed,
                removed=_parse_date(removed_raw),
                price=float(price) if price is not None else 0.0,
                address=address,
                unit=unit,
                lat=lat,
                lng=lng,
                beds=beds,
                baths=baths,
                sqft=sqft,
            )

        top = _spell(record.get("listedDate"), record.get("removedDate"), record.get("price"))
        if top is not None:
            raw.append(top)

        for event in (record.get("history") or {}).values():
            entry = _spell(event.get("listedDate"), event.get("removedDate"), event.get("price"))
            if entry is not None:
                raw.append(entry)

    seen: set[tuple[date, date | None]] = set()
    deduped: list[_Spell] = []
    for spell in sorted(raw, key=lambda s: (s.listed, s.removed or date.max)):
        sig = (spell.listed, spell.removed)
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(spell)
    return deduped


def _stitch(spells: Sequence[_Spell], stitch_gap_days: int) -> list[list[_Spell]]:
    """F4-S3: merge chronologically adjacent spells whose gap is strictly
    less than ``stitch_gap_days``."""
    chains: list[list[_Spell]] = [[spells[0]]]
    for spell in spells[1:]:
        last = chains[-1][-1]
        if last.removed is not None and (spell.listed - last.removed).days < stitch_gap_days:
            chains[-1].append(spell)
        else:
            chains.append([spell])
    return chains


def _withdrawal_suspect_flags(chains: Sequence[Sequence[_Spell]], months: int) -> list[bool]:
    """F4-S8: a completed chain flags suspect iff the group's NEXT chain
    starts 6 weeks-6 months after it ended."""
    flags = [False] * len(chains)
    upper = months * _DAYS_PER_MONTH
    for i in range(len(chains) - 1):
        last = chains[i][-1]
        if last.removed is None:
            continue  # censored, not a "complete" spell
        gap = (chains[i + 1][0].listed - last.removed).days
        if WITHDRAWAL_SUSPECT_MIN_DAYS <= gap < upper:
            flags[i] = True
    return flags


def _classify_removal(removed: date, as_of: date, provisional_lease_days: int) -> str:
    days_since = (as_of - removed).days
    if days_since < provisional_lease_days:
        return "pending"
    if days_since < CONFIRMED_REMOVAL_DAYS:
        return "provisional"
    return "confirmed"


def _in_window(day: date, start_mmdd: str, end_mmdd: str) -> bool:
    """F4-S4: year-agnostic month-day window, inclusive, wraparound-safe."""
    start = _mmdd(start_mmdd)
    end = _mmdd(end_mmdd)
    key = (day.month, day.day)
    if start <= end:
        return start <= key <= end
    return key >= start or key <= end  # e.g. Nov 15 - Feb 15


def _mmdd(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return (int(month), int(day))


def _build_comp(chain: Sequence[_Spell], suspect: bool, as_of: date, config: Config) -> StitchedComp:
    first, last = chain[0], chain[-1]
    censored = last.removed is None
    end_date = last.removed if last.removed is not None else as_of
    removal_class = (
        None if censored else _classify_removal(last.removed, as_of, config.provisional_lease_days)
    )

    cut_history: list[PriceCut] = []
    gap_days_total = 0
    for prev, nxt in zip(chain, chain[1:]):
        if prev.removed is not None:
            gap_days_total += max(0, (nxt.listed - prev.removed).days)
        if nxt.price < prev.price:
            cut_history.append(PriceCut(on=nxt.listed, from_price=prev.price, to_price=nxt.price))

    return StitchedComp(
        address=last.address,
        unit=last.unit,
        lat=last.lat,
        lng=last.lng,
        beds=last.beds,
        baths=last.baths,
        sqft=last.sqft,
        initial_ask=first.price,
        effective_dom=(end_date - first.listed).days,
        censored=censored,
        removal_class=removal_class,
        cohort_year=first.listed.year,
        first_listed=first.listed,
        withdrawal_suspect=suspect,
        sqft_suspect=False,
        cut_history=tuple(cut_history),
        relist_count=len(chain) - 1,
        gap_days=gap_days_total,
    )
