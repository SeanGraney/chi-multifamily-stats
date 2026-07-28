"""Record shaping — F4-S2/F4-S3/F4-S4/F4-S8, composed behind one seam.

``shape_raw_pull`` is the one entry point WS-1 pins (QUEUE.md row 6 / the
WS-1 dispatch's "contract item 1"): dedupe -> spell extraction -> 42-day-gap
stitch -> three-state removal classification + withdrawal-suspect -> window
filter + cohort assignment. Internal structure below this seam is a
[DEFAULT] — nothing in the ADR or story docs names five separate stage
functions, so this module keeps everything behind one composed call rather
than inventing importable internals nobody pinned.

``extract_spells``/``Spell`` are the one exception (F4-S2): ADR-002 note 6
recorded that this module owed a Layer-1 seam, and F4-S2's AC is stated in
*spell rows* ("a record whose history holds two prior spells yields three
spell rows") — a claim ``shape_raw_pull`` cannot express, since it exposes
only post-stitch comps. Asserting it through the stitcher would make an
F4-S2 acceptance criterion depend on F4-S3's merge threshold. F4-S3's own
``stitch`` seam is its story's to add, not this one's.

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
   listing, not two, and lands in exactly one group.
2. **Group** the deduped records by normalized address+unit
   (`pipeline.keys.comp_key` — the same identity primitive the rest of the
   system uses, not a second normalization rule).
3. **Spell extraction**: every group's record(s) contribute one spell per
   top-level (listedDate/removedDate/price) plus one spell per ``history``
   entry, collapsed to one row per listing start (see ``extract_spells``).
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

__all__ = ["Spell", "extract_spells", "shape_raw_pull"]

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
class Spell:
    """One reported listing interval, before stitching (F4-S2).

    ``removed is None`` means the spell was still open when the pull was
    taken — the censored case NORTH_STAR calls the single most important
    distinction in the system. The fields past ``price`` are the record
    attributes the spell was read from; they ride along so ``_build_comp``
    can populate a `StitchedComp` without a second pass over the raw dicts.
    """

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
    groups: dict[str, list[dict]] = defaultdict(list)
    for copies in _dedupe_by_id(active_records, inactive_records):
        groups[_group_key(copies)].extend(copies)

    comps: list[StitchedComp] = []
    for key in sorted(groups):
        spells = extract_spells(groups[key])
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


def _dedupe_by_id(active: Sequence[dict], inactive: Sequence[dict]) -> list[list[dict]]:
    """F4-S2 "dedupe on listing `id`" — every copy of one id, together.

    A listing whose status flips between the Active call and the Inactive
    call appears in *both* responses, and the two copies disagree: the Active
    copy has no ``removedDate``, the Inactive copy does. Keeping whichever
    copy arrived first (the previous behaviour) made the result depend on
    pagination order — identical input shaped to ``censored=True, dom=87`` or
    ``censored=False, removal_class='confirmed', dom=40`` purely by call
    order.

    Discarding the second copy instead is not an option either: a removal
    observed anywhere in the pull *is* an observation, and censoring a comp
    that actually leased biases every Kaplan-Meier curve toward pessimism
    (NORTH_STAR: censored-vs-leased is "the single most important
    distinction in the whole system").

    So neither copy is thrown away here. Both are carried into one group, and
    `extract_spells` resolves them at the spell level, where the disagreement
    actually lives — which also keeps the case the two copies are *not* in
    conflict (an Active copy re-listed after the Inactive copy's removal is a
    genuine re-list) from silently losing its second spell.
    """
    by_id: dict[object, list[dict]] = defaultdict(list)
    for record in (*active, *inactive):
        rid = record.get("id")
        by_id[rid if rid is not None else id(record)].append(record)
    return list(by_id.values())


def _record_address(record: dict) -> str:
    return record.get("addressLine1") or record.get("formattedAddress") or ""


def _group_key(copies: Sequence[dict]) -> str:
    """The single normalized address+unit that every copy of one listing id
    shares (`pipeline.keys.comp_key`).

    Chosen by value rather than by arrival order, so two copies that spell
    their address differently still land in exactly one group — otherwise
    "dedupe on id" would hold at the record level and break at the group
    level.
    """
    return min(comp_key(_record_address(record), record.get("addressLine2")) for record in copies)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).date()


def extract_spells(records: Sequence[dict]) -> tuple[Spell, ...]:
    """F4-S2 [INVARIANT] "what counts as a prior spell": one spell per
    top-level record, plus one per ``history`` entry, in chronological order.

    A record whose ``history`` holds two prior spells therefore yields three
    spell rows (AC1) — asserted here rather than through the stitcher, whose
    42-day merge threshold is F4-S3's rule, not this story's.

    IDENTITY: a spell is identified by **when the listing started**. Two
    reports of a listing that started on the same day at the same unit are
    two observations of one spell, not two vacancies, so they collapse to one
    row. Three real shapes reach this rule:

    * a record's top-level (listedDate, removedDate) restating its own newest
      ``history`` event — 495 of the 495 real records that have a history;
      counting both would fabricate a zero-length gap on every one of them;
    * the same listing id seen Active in one response and removed in the
      other (see `_dedupe_by_id`);
    * two listing ids for one physical unit, which AC2's normalized grouping
      key now puts in the same group.

    When two observations of one spell disagree, the more complete one wins:
    an observed ``removedDate`` beats "still active", and the later of two
    observed removals beats the earlier ([DEFAULT] tie-break — it is the more
    recent observation of the same listing). The winner is chosen from the
    spells' own values, never from their input order, so shaping cannot
    depend on which response a copy arrived in.

    Note what is deliberately *not* collapsed: consecutive segments whose
    ``removedDate`` equals the next segment's ``listedDate`` (a price change,
    23 real records) are distinct starts and stay distinct rows. Merging
    those is F4-S3's job, and doing it here would destroy the price cut
    `cut_history` is built from.
    """
    raw: list[Spell] = []
    for record in records:
        address = _record_address(record)
        unit = record.get("addressLine2")
        lat = float(record.get("latitude") or 0.0)
        lng = float(record.get("longitude") or 0.0)
        beds = float(record["bedrooms"]) if record.get("bedrooms") is not None else 0.0
        baths = float(record["bathrooms"]) if record.get("bathrooms") is not None else None
        sqft = float(record["squareFootage"]) if record.get("squareFootage") is not None else None

        def _spell(listed_raw: str | None, removed_raw: str | None, price: object) -> Spell | None:
            listed = _parse_date(listed_raw)
            if listed is None:
                return None
            return Spell(
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

    best: dict[date, Spell] = {}
    for spell in raw:
        incumbent = best.get(spell.listed)
        if incumbent is None or _completeness(spell) > _completeness(incumbent):
            best[spell.listed] = spell
    return tuple(best[listed] for listed in sorted(best))


def _completeness(spell: Spell) -> tuple[bool, date, float]:
    """Total order over two observations of the same listing start.

    Value-derived only — never a position in the input — so `extract_spells`
    returns the same rows whichever response a duplicated listing arrived in.
    The trailing ``price`` is a deterministic tie-break with no semantic
    claim behind it; the two components before it are the semantic ones.
    """
    return (spell.removed is not None, spell.removed or date.min, spell.price)


def _stitch(spells: Sequence[Spell], stitch_gap_days: int) -> list[list[Spell]]:
    """F4-S3: merge chronologically adjacent spells whose gap is strictly
    less than ``stitch_gap_days``."""
    chains: list[list[Spell]] = [[spells[0]]]
    for spell in spells[1:]:
        last = chains[-1][-1]
        if last.removed is not None and (spell.listed - last.removed).days < stitch_gap_days:
            chains[-1].append(spell)
        else:
            chains.append([spell])
    return chains


def _withdrawal_suspect_flags(chains: Sequence[Sequence[Spell]], months: int) -> list[bool]:
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


def _build_comp(chain: Sequence[Spell], suspect: bool, as_of: date, config: Config) -> StitchedComp:
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
