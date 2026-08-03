"""Record shaping — F4-S2/F4-S3/F4-S4/F4-S8, composed behind one seam.

``shape_raw_pull`` is the one entry point WS-1 pins (QUEUE.md row 6 / the
WS-1 dispatch's "contract item 1"): dedupe -> spell extraction -> 42-day-gap
stitch -> three-state removal classification + withdrawal-suspect -> window
filter + cohort assignment. Internal structure below this seam is a
[DEFAULT] — nothing in the ADR or story docs names five separate stage
functions, so this module keeps everything behind one composed call rather
than inventing importable internals nobody pinned.

``extract_spells``/``Spell`` are the first exception (F4-S2): ADR-002 note 6
recorded that this module owed a Layer-1 seam, and F4-S2's AC is stated in
*spell rows* ("a record whose history holds two prior spells yields three
spell rows") — a claim ``shape_raw_pull`` cannot express, since it exposes
only post-stitch comps. Asserting it through the stitcher would make an
F4-S2 acceptance criterion depend on F4-S3's merge threshold.

``stitch`` is the second (F4-S3), for the mirror-image reason: F4-S3's first
acceptance criterion is "threshold 0 => no merging", and
``Config.stitch_gap_days`` is ``Field(ge=7, le=60)`` — so that criterion is
literally unreachable through ``shape_raw_pull``. The seam takes a plain
``int`` and returns chains rather than comps; see its own docstring.
``shape_raw_pull`` calls it, so it is the real path and not a second
implementation that can drift.

``shape_raw_pull_with_summary`` is the third and last (F4-S4), and it is not
really a fourth seam so much as the same one seen whole: F4-S4's AC requires
that padding-only chains are "dropped **and counted in a pipeline debug
summary**", and a count `shape_raw_pull` computes and throws away is
unassertable. It is the implementation; ``shape_raw_pull`` is a projection of
it that drops the summary, so the two cannot disagree about which comps a
pull shapes to.

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
   into a chain whenever the unit's **days off market** —
   ``max(0, nextListed - the latest removal the chain has observed)`` — is
   *strictly less than* ``config.stitch_gap_days`` (a gap exactly at the
   threshold does NOT merge; an overlap is zero days off market, not a
   negative gap). Each maximal chain becomes one `StitchedComp` candidate.
   See ``stitch``.
4b. **Identity selection** (row 9b): a chain's ``address``/``unit``/``lat``/
   ``lng``/``beds``/``baths``/``sqft`` come from the chain's most recent
   *usable* observation of each field, chosen **per field** — not off
   ``chain[-1]``, which was a positional accident that let a comp publish
   "unknown" while its own chain held a reported square footage. ``lat`` and
   ``lng`` are the one atomic pair. See ``_select_identity``.
5. **Classify** (F4-S8): a chain that is still open is ``censored``
   (removal_class=None), DOM measured to ``as_of``. A closed chain is
   classified off ``as_of - _chain_end(chain)``: < ``provisional_lease_days``
   -> pending (excluded downstream), < 42d -> provisional (counted+marked),
   else -> confirmed. A confirmed spell whose group re-lists 42d-6mo later
   (a *separate* chain, since anything closer would have stitched) is
   flagged ``withdrawal_suspect``.
6. **Window + cohort** (F4-S4): a chain survives only if its *stitched
   start*'s month-day falls inside ``[window_start_mmdd, window_end_mmdd]``
   (year-agnostic, both edges inclusive, wraparound-safe); ``cohort_year`` is
   that start's calendar year. The chains this step throws away are counted,
   not merely dropped — see `shape_raw_pull_with_summary`.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import TypeVar

from rentcomp.models.domain import PriceCut, Spell, StitchedComp
from rentcomp.pipeline.keys import comp_key
from rentcomp.storage.config import Config

__all__ = [
    "ShapingSummary",
    "Spell",
    "extract_spells",
    "shape_raw_pull",
    "shape_raw_pull_with_summary",
    "stitch",
]

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

#: One identity field's value type — see `_latest_usable`, which is generic so
#: the seven fields share one selection loop instead of seven copies of it.
_T = TypeVar("_T")

#: Approximate days/month used to turn ``withdrawal_suspect_months`` (a
#: config knob) into a day count. Not date-arithmetic-exact (no calendar
#: lookup) — "6 months" is already an approximation in the spec text.
_DAYS_PER_MONTH = 30


@dataclass(frozen=True, slots=True)
class ShapingSummary:
    """F4-S4 [INVARIANT], second clause: the padding a pull paid for and this
    stage threw away, counted rather than silently discarded.

    Why this exists at all: spec §3.2 pads every query +/-90 days on both
    sides, because RentCast resets ``listedDate`` on a re-list and a
    June-window comp can only be recognized as one after stitching. The pad is
    therefore *bought* padding — on the committed real pull a 16-day June
    window keeps 28 chains and drops 539, so **95% of what the pull paid for
    is discarded here by design**. A drop that leaves no trace makes "your
    market is thin" and "your window is narrow" indistinguishable, and those
    call for opposite user actions (widen the radius vs. widen the window).
    It also means a query-planner or stitcher regression that quietly widened
    the drop set would look exactly like a thin market.

    COUNTS ARE OF CHAINS, NOT RAW RECORDS (PM ruling A4). The window is
    applied to a stitched chain, so the chain is the thing dropped: three raw
    records that stitch into one padding chain are one drop, not three.
    Counting records would over-report by the re-list count and would never
    reconcile against `kept`, which is a chain count by construction.

    ``kept + dropped_outside_window`` is every chain this pull shaped — the
    same spirit as F7-S1's ``included + excluded + filtered == pulled``,
    one stage earlier. Note what is *outside* both counts and outside that
    identity: a group that produced no spells at all (no parseable
    ``listedDate``) never reaches the window, so it is neither kept nor
    dropped-outside-window. This summary accounts for the window's decisions,
    not for parse losses.

    Deliberately **not** on the wire (PM ruling, this story). `DerivedState.
    breakdown`'s identity is an [INVARIANT] computed over already-post-window
    comps; adding a padding count to it would change what that invariant is
    measured over. Extra fields (per-cohort tallies, the dropped keys) can be
    added later without breaking anything that reads these two.
    """

    #: Chains whose stitched start fell inside the window — always
    #: ``len(comps)`` for the same call.
    kept: int
    #: Chains dropped because their stitched start fell outside it.
    dropped_outside_window: int


def shape_raw_pull(
    active_records: Sequence[dict],
    inactive_records: Sequence[dict],
    config: Config,
    as_of: date,
    window_start_mmdd: str,
    window_end_mmdd: str,
) -> tuple[StitchedComp, ...]:
    """Dedupe -> spells -> stitch -> classify -> window filter + cohort.

    The comps-only entry point. It *delegates* to
    `shape_raw_pull_with_summary` and drops the summary rather than
    re-implementing the chain, so the two can never drift into two shaping runs
    that disagree.

    ⚠ As of F4-S6 this has no production caller: `storage/pulls.py` takes the
    summary form, because `ShapingSummary.dropped_outside_window` now reaches
    the wire (`Breakdown.dropped_outside_window`) so the empty state can name
    the binding constraint. It is kept as the projection a body of tests
    compares tuples against directly, and because dropping it would be a
    refactor this story did not ask for — but it is now a test-facing seam
    rather than the path the product takes. Disclosed to the PM.

    Deterministic: groups are visited in sorted-key order, so two calls over
    the same inputs produce the same tuple in the same order.
    """
    comps, _ = shape_raw_pull_with_summary(
        active_records, inactive_records, config, as_of, window_start_mmdd, window_end_mmdd
    )
    return comps


def shape_raw_pull_with_summary(
    active_records: Sequence[dict],
    inactive_records: Sequence[dict],
    config: Config,
    as_of: date,
    window_start_mmdd: str,
    window_end_mmdd: str,
) -> tuple[tuple[StitchedComp, ...], ShapingSummary]:
    """`shape_raw_pull`, plus the F4-S4 count of what the window discarded.

    The same computation and the same comps — this is the implementation and
    `shape_raw_pull` is the projection of it, not the other way round. Split
    in two only because `shape_raw_pull` has three call sites in
    `storage/pulls.py` and a body of tests comparing its tuple directly, and
    a summary that arrived by changing its return type would be a refactor
    this story did not ask for (PM ruling A4).
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for copies in _dedupe_by_id(active_records, inactive_records):
        groups[_group_key(copies)].extend(copies)

    comps: list[StitchedComp] = []
    dropped = 0
    for key in sorted(groups):
        spells = extract_spells(groups[key])
        if not spells:
            continue
        chains = stitch(spells, config.stitch_gap_days)
        suspects = _withdrawal_suspect_flags(chains, config.withdrawal_suspect_months)
        for chain, suspect in zip(chains, suspects, strict=True):
            start = chain[0].listed
            if not _in_window(start, window_start_mmdd, window_end_mmdd):
                dropped += 1
                continue
            comps.append(_build_comp(chain, suspect, as_of, config))
    return tuple(comps), ShapingSummary(kept=len(comps), dropped_outside_window=dropped)


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


def _completeness(spell: Spell) -> tuple[bool, date, int, float]:
    """Total order over two observations of the same listing start; the
    greater one wins.

    Every component is derived from the spell's own values, so
    `extract_spells` returns the same rows whichever response a duplicated
    listing arrived in. That claim is only true *with* the third component
    below: through F4-S2 this function ordered on ``(removed is not None,
    removed, price)`` alone, and two observations agreeing on both of those
    were resolved first-seen-wins — i.e. by arrival order, in a function
    whose docstring claimed the opposite. (Corrected here, PM ruling P1a.)

    The components, in order:

    1. ``removed is not None`` — an observed removal beats "still active".
    2. ``removed`` — the later of two observed removals is the more recent
       observation of the same listing. `[DEFAULT]`.
    3. **field completeness** — the count of optional attributes this
       observation actually reported. This is the thing the function is
       named for (PM ruling P1b). It can only ever prefer a *reported* value
       over an absent one, never invent one, so it is strictly more evidence
       and never less; and `sqft` is not a cosmetic ride-along, since a
       missing-sqft comp is default-excluded from every cohort median
       (F4-S5) and therefore from `premium`. It sorts **below** both removal
       components deliberately: a completeness score that outranked them
       would let a richly-populated but still-active observation beat one
       that saw the unit removed, censoring a comp that actually leased —
       the distinction NORTH_STAR calls the most important in the system.
    4. ``price`` — a deterministic last-resort tie-break with no semantic
       *justification*, but a real semantic *consequence*: it prefers the
       higher price, which biases `initial_ask`, the premium numerator,
       upward. It is last so that anything with a reason outranks it.
    """
    reported = sum(field is not None for field in (spell.unit, spell.baths, spell.sqft))
    return (spell.removed is not None, spell.removed or date.min, reported, spell.price)


def stitch(spells: Sequence[Spell], stitch_gap_days: int) -> tuple[tuple[Spell, ...], ...]:
    """F4-S3 [INVARIANT]: group ``spells`` into maximal continuous-vacancy
    chains. Returns the chains in listed order — a partition of the input,
    preserving order, losing and duplicating nothing.

    ``stitch_gap_days`` is a plain ``int``, deliberately, and not a `Config`:
    `Config.stitch_gap_days` is ``Field(ge=7, le=60)``, so the story's first
    acceptance criterion ("threshold 0 => no merging") is *unreachable*
    through a `Config`-shaped seam. The chains, not `StitchedComp`s, are the
    return value for the same reason — how spells group is independent of the
    DOM rule applied to a group, and asserting one through the other would
    couple two separate acceptance criteria.

    THE MERGE RULE, AND WHY IT IS ``max(0, gap)``
    ---------------------------------------------
    The story states the rule twice::

        merge consecutive spells where gap = nextListed - prevRemoved < threshold
        Off market >= threshold => prior spell is complete

    Read as raw signed subtraction the first clause is also true of every
    *negative* gap, so at ``threshold = 0`` two overlapping spells still
    merge — contradicting the story's own AC1. The second clause is decisive
    and reconciles them: what the threshold measures is **days off market**,
    and a unit whose next listing began before the previous one ended was
    never off market at all. So::

        days_off_market = max(0, nextListed - the latest removal observed
                                  so far in the chain)
        merge  <=>  days_off_market < threshold

    and every clause lines up: threshold 0 merges nothing (no amount of
    off-market time is strictly less than zero); gap 41 merges and gap 42
    does not at the default; contiguous price-change segments (gap 0) merge;
    and overlapping observations of one unit merge, which is what keeps
    F4-S2's four de-duplicated units from being double-counted again.

    Measured over the committed 539-record pull, the three candidate readings
    give 618/624/624 chains at threshold 0 and 567/573/567 at threshold 42
    for ``gap < t`` / ``0 <= gap < t`` / ``max(0, gap) < t`` respectively.
    Only this one satisfies AC1 *and* leaves the evidence base at the 567
    comps F4-S2 established; the middle one is an F4-S2 regression wearing an
    F4-S3 fix. (PM ruling E1 — disambiguation of a self-contradictory
    `[INVARIANT]`, not a change to it.)

    The comparison is against the chain's *latest observed removal*, not its
    most recently listed spell's removal, for the same reason `_chain_end`
    is: once two spells in a chain overlap, the last spell by listed date is
    not the last spell to end.

    An **open** spell (``removed is None``) ends its chain: a unit still on
    market has no off-market days to measure a successor against. On this
    pull no group ever reports an open spell that is not last by listed date,
    so this is the unchanged pre-existing behaviour rather than new
    behaviour invented for a case that does not occur (PM ruling E6). It is
    also what makes ``censored`` unambiguous below: an open spell is always
    its chain's last spell, so "the chain ends open" and "the chain contains
    an open spell" cannot disagree.
    """
    chains: list[list[Spell]] = []
    for spell in spells:
        off_market = _days_off_market(chains[-1], spell) if chains else None
        if off_market is not None and off_market < stitch_gap_days:
            chains[-1].append(spell)
        else:
            chains.append([spell])
    return tuple(tuple(chain) for chain in chains)


def _chain_end(chain: Sequence[Spell]) -> date | None:
    """The date a chain's vacancy was last observed to run to, or ``None``
    while it is still open.

    F4-S3 [INVARIANT]: "effectiveDOM = **final removal** - first listed". The
    final removal is the *latest* removal the chain observed, which is not
    ``chain[-1].removed`` once any two of its spells overlap — the last spell
    by listed date can be the first to end. Two real comps were under-
    reported by five to six weeks by that reading (`2350 S Leavitt St 1R`
    91 -> 128, `2453 W 46th Pl 1` 165 -> 205), and `effective_dom` is the
    kNN target and the Kaplan-Meier event time, so a truncated value tells
    the survival curve a unit leased six weeks before it did.
    """
    end = None
    for spell in chain:
        if spell.removed is None:
            return None
        end = spell.removed if end is None else max(end, spell.removed)
    return end


def _days_off_market(chain: Sequence[Spell], nxt: Spell) -> int | None:
    """Days the unit was off market between ``chain`` and ``nxt`` — never
    negative, and measured from the chain's latest observed removal.

    ``None`` — "unmeasurable", not "zero" — while the chain is still open,
    which is what ends it (see `stitch`).
    """
    end = _chain_end(chain)
    if end is None:
        return None
    return max(0, (nxt.listed - end).days)


def _withdrawal_suspect_flags(chains: Sequence[Sequence[Spell]], months: int) -> list[bool]:
    """F4-S8: a completed chain flags suspect iff the group's NEXT chain
    starts 6 weeks-6 months after it ended.

    "After it ended" is `_chain_end` — the same latest-observed-removal rule
    the chain's DOM uses, not ``chains[i][-1].removed``. That expression is
    BUG-2's, and measuring the suspicion window from a truncated end would
    inflate the gap and mis-flag a chain that overlapped its own tail. Not
    reachable on today's pull (no group has both an overlapping chain and a
    6-week-to-6-month re-list), but F4-S8 is the next story to lean on it —
    PM ruling E4, fixed in the same pass rather than inherited.
    """
    flags = [False] * len(chains)
    upper = months * _DAYS_PER_MONTH
    for i in range(len(chains) - 1):
        end = _chain_end(chains[i])
        if end is None:
            continue  # censored, not a "complete" spell
        gap = (chains[i + 1][0].listed - end).days
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


def _is_reported_text(value: str | None) -> bool:
    """A text attribute is evidence only if it is a string with content.

    `_record_address` returns ``""`` for a record carrying neither
    ``addressLine1`` nor ``formattedAddress``, and a blank ``addressLine2`` is
    an absent unit designator rather than a unit named ``""``. Neither is an
    observation, so neither may outrank one.
    """
    return value is not None and value.strip() != ""


def _is_reported_value(value: object) -> bool:
    """The plain "the response carried this field" test, for attributes whose
    domain type can still express absence (``baths``).

    Deliberately *not* used for ``sqft`` (see `_is_measured_area`) and
    unusable for ``lat``/``lng``/``beds``, which `extract_spells` coerces to
    ``0.0`` — the difference between "reported 0" and "not reported" is
    already gone by the time a `Spell` exists.
    """
    return value is not None


def _is_measured_area(value: float | None) -> bool:
    """A square footage is evidence only if it is a positive number.

    Not a new judgment about the data: this is exactly the precondition
    `StitchedComp.psf` already applies (``sqft is None or sqft <= 0.0 ->
    None``). The domain model has always held that a non-positive area is not
    a usable measurement; restating it here is what stops the selector
    preferring a reported ``0`` over a reported ``700`` and handing the comp a
    number that excludes it from every median anyway — strictly worse than the
    positional accident this row removes.

    Judging a *plausible* area (700 sqft for a 3-bed/3-bath) is a different
    question and a different story: F5-S1's ``sqft_suspect``, ">30% off the
    cohort median". This stage's job is to admit what the pull reported.
    """
    return value is not None and value > 0.0


def _latest_usable(
    chain: Sequence[Spell],
    value_of: Callable[[Spell], _T],
    usable: Callable[[_T], bool],
) -> _T | None:
    """The most recent spell's value of one field that is usable evidence, or
    ``None`` if no spell in the chain reported one.

    "Most recent" is by listed date, which is the chain's own order — and it
    is well defined, because `extract_spells` keys its winners by listing
    start, so no chain ever holds two spells with the same ``listed`` (see
    `stitch`). ``None`` means *no* spell in the chain reported anything usable
    for this field; nothing is ever invented to fill the gap.
    """
    for spell in reversed(chain):
        value = value_of(spell)
        if usable(value):
            return value
    return None


def _latest_reported_point(chain: Sequence[Spell]) -> tuple[float, float]:
    """The chain's coordinate — ``lat`` and ``lng`` taken from ONE spell.

    ⚠ THE ONE EXCEPTION TO PER-FIELD SELECTION (PM ruling, row 9b). Latitude
    and longitude are not two attributes, they are one datum in two columns.
    Choosing them independently could compose A's latitude with B's longitude
    — a point **no observation ever reported**, which is precisely the
    NORTH_STAR failure mode the rest of this selector exists to avoid. The
    "strictly more evidence" argument does not carry here, because the
    composed result is not evidence at all.

    "Reported" is ``lat != 0 and lng != 0``, and that is a reconstruction, not
    a test: `extract_spells` does ``float(record.get("latitude") or 0.0)``, so
    a record with no coordinates arrives as ``(0.0, 0.0)`` — an actual place in
    the Gulf of Guinea — and the `Spell` type cannot tell it from a reported
    zero. No Chicago comp sits on the equator or the prime meridian, so
    treating an exact zero as "absent" is the best available reading of a
    distinction the domain boundary already threw away. **The real repair is to
    make `Spell.lat`/`lng` optional**, which changes `models/domain.py` and the
    wire type and is therefore not this row's (disclosed to the PM).
    """
    for spell in reversed(chain):
        if spell.lat != 0.0 and spell.lng != 0.0:
            return (spell.lat, spell.lng)
    return (chain[-1].lat, chain[-1].lng)


def _select_unit(chain: Sequence[Spell]) -> str | None:
    """The comp's displayed unit designator.

    ⚠ ISOLATED ON PURPOSE — this is the one identity field whose rule is an
    **open question for the PM** (row 9b dispatch, 2026-07-30), and it is a
    whole function for a field that is one expression so that a ruling can
    change it without touching the numeric path above.

    ``unit`` is the only identity field that reaches nothing but the screen:
    `pipeline.keys.comp_key` normalizes ``Unit 1R``, ``# 1R`` and ``1r`` to the
    same token, so F13-S1's curation keys — and therefore every saved weight —
    are identical under any choice here. What it decides is purely which
    spelling the row prints.

    The rule implemented is "the most recent observation that reported one",
    for the same reason the numeric fields use it (below): it is the smallest
    possible deviation from the pre-row behaviour, changing the printed string
    only where the last spell reported no unit at all. It is **not** claimed to
    be the right selector for a display string — "most complete" is a poor
    argument about formatting — and the alternatives (the first observation's
    spelling; the most frequent; the longest/most explicit) are all one line
    here.
    """
    return _latest_usable(chain, lambda spell: spell.unit, _is_reported_text)


@dataclass(frozen=True, slots=True)
class _Identity:
    """The seven identity/geometry fields a chain resolves to. See
    `_select_identity` — this exists so the selection is one named, testable
    step rather than seven expressions inlined into a constructor call."""

    address: str
    unit: str | None
    lat: float
    lng: float
    beds: float
    baths: float | None
    sqft: float | None


def _select_identity(chain: Sequence[Spell]) -> _Identity:
    """Row 9b [INVARIANT-in-effect]: a comp reports the evidence its own chain
    holds, not the evidence its last spell happens to hold.

    THE DEFECT THIS REPLACES
    ------------------------
    Through F4-S8 these seven fields were read off ``chain[-1]`` — the last
    spell *by listed date*. Nobody chose "the most recent observation wins";
    that is what indexing the end of a sorted list does. F4-S3 corrected the
    same positional accident for the chain's *end* (`_chain_end`) and left
    these deliberately alone as its own out-of-scope note (PM ruling E2).

    It is not cosmetic. On the committed pull `2350 S Leavitt St 1R` stitches
    to a three-spell chain whose **middle** spell is the only one that reported
    a ``squareFootage``, so the comp published ``sqft=None`` while the pull held
    ``700`` for the same unit in the same group in the same fixture. Via F4-S5
    ("missing-sqft comps ... excluded from every median") that removed it from
    every cohort median behind `premium`, from every bucket, and from every
    neighbour set — on the strength of discarded evidence rather than absent
    evidence.

    THE RULE: PER FIELD, MOST RECENT USABLE OBSERVATION
    ---------------------------------------------------
    For each field independently, the chain's **most recent spell that actually
    reported a usable value** supplies it; if no spell did, the field is absent
    (or, for the fields that cannot express absence, the last spell's value).

    Per-*field* rather than per-*observation* is the PM's ruling (row 9b), and
    the reason is that whole-observation selection contradicts this row's own
    justification in exactly the case the row was written for. Two observations
    of one unit each reporting what the other omits —

        A: sqft 950, baths absent
        B: sqft absent, baths 2.5

    — **tie** on any whole-observation completeness score, so "take all seven
    fields from the most complete observation" must discard a value some
    observation genuinely reported, whichever way the tie breaks. The whole
    argument for this row is "preferring a reported value over an absent one is
    strictly more evidence and cannot invent a number"; a rule that violates its
    own stated reason is the wrong rule. Per-field keeps both. The one place the
    argument does not carry is the coordinate — see `_latest_reported_point`,
    which is why ``lat``/``lng`` are an atomic pair and not two fields.

    "Most recent" as the tie-break, where two observations both report a usable
    value and disagree, is a `[DEFAULT]`. Neither value is more evidence than
    the other, so no honesty argument decides it; what recommends this one is
    that it is the **smallest possible deviation** from the pre-row behaviour —
    it returns exactly ``chain[-1]``'s value whenever ``chain[-1]`` reported the
    field, so the change is provably confined to fields the last spell said
    nothing about. It also has a story of its own (the freshest description of
    a unit that may have been re-measured or re-configured between listings).
    Unreachable on the committed pull: exactly one chain disagrees about `sqft`
    at all and one side of that disagreement is absent.

    WHAT IS DELIBERATELY UNCHANGED
    -------------------------------
    ``beds`` still comes from ``chain[-1]``, and that is not an oversight.
    `extract_spells` coerces a missing ``bedrooms`` to ``0.0``, and ``0`` is
    *also* a legitimate bedroom count (a studio), so no predicate can separate
    "not reported" from "reported zero" — and a "prefer a non-zero" heuristic
    would silently promote studios into one-bedrooms. The domain boundary has
    to change before this field can be selected honestly (same disclosure as
    the coordinate). Zero chains in the committed pull disagree about ``beds``.

    Nothing about the chain's dates, prices or classification is touched here:
    `effective_dom`, `censored`, `removal_class`, `initial_ask`, `cut_history`,
    `relist_count`, `gap_days` and `cohort_year` are all functions of dates and
    prices, and none of the seven fields below is either.
    """
    last = chain[-1]
    lat, lng = _latest_reported_point(chain)
    return _Identity(
        # `address` cannot be absent on the wire, so a chain in which no spell
        # reported one falls back to the last spell's (blank) value.
        address=_latest_usable(chain, lambda spell: spell.address, _is_reported_text)
        or last.address,
        unit=_select_unit(chain),
        lat=lat,
        lng=lng,
        # See the docstring: `beds` has no expressible absence to prefer over.
        beds=last.beds,
        baths=_latest_usable(chain, lambda spell: spell.baths, _is_reported_value),
        sqft=_latest_usable(chain, lambda spell: spell.sqft, _is_measured_area),
    )


def _build_comp(chain: Sequence[Spell], suspect: bool, as_of: date, config: Config) -> StitchedComp:
    """Fold one chain into the comp the derivation graph consumes.

    The chain's *end* is `_chain_end`, its latest observed removal (F4-S3
    "effectiveDOM = final removal - first listed"). The identity/geometry
    fields (address, unit, lat, lng, beds, baths, sqft) are `_select_identity`
    — per field, the chain's most recent *usable* observation of it (row 9b,
    which F4-S3 split out so that one story did not make two independent
    changes to the population behind `premium`).
    """
    first = chain[0]
    identity = _select_identity(chain)
    end_date = _chain_end(chain)
    censored = end_date is None
    removal_class = (
        None if end_date is None else _classify_removal(end_date, as_of, config.provisional_lease_days)
    )

    # ``gap_days`` is the off-market time the chain merged over, so it is
    # summed through `_days_off_market` — literally the predicate `stitch`
    # merged on, not a second copy of it that could drift from it. An overlap
    # therefore contributes nothing: the unit was never off market.
    cut_history: list[PriceCut] = []
    gap_days_total = 0
    for index, nxt in enumerate(chain[1:], start=1):
        gap_days_total += _days_off_market(chain[:index], nxt) or 0
        prev = chain[index - 1]
        if nxt.price < prev.price:
            cut_history.append(PriceCut(on=nxt.listed, from_price=prev.price, to_price=nxt.price))

    return StitchedComp(
        address=identity.address,
        unit=identity.unit,
        lat=identity.lat,
        lng=identity.lng,
        beds=identity.beds,
        baths=identity.baths,
        sqft=identity.sqft,
        initial_ask=first.price,
        # A censored chain's DOM is a floor measured to the PULL date (owner
        # ruling 1), never the wall clock.
        effective_dom=((as_of if end_date is None else end_date) - first.listed).days,
        censored=censored,
        removal_class=removal_class,
        cohort_year=first.listed.year,
        first_listed=first.listed,
        withdrawal_suspect=suspect,
        cut_history=tuple(cut_history),
        relist_count=len(chain) - 1,
        gap_days=gap_days_total,
    )
