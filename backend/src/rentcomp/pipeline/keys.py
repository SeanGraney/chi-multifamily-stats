"""Comp identity — F13-S1 [INVARIANT], pinned here as one importable function.

"Re-key selections/weights by normalized address+unit — ids can churn." A
listing id survives a refresh only by luck, and a curation state keyed on luck
silently loses the user's work.

The reason this is a *module* and not three lookalike helpers: the wire keys
(`DerivedComp.key`), the workspace keys (F1-S2), and the refresh re-keying
(F13-S1) must provably be the same function. Anything that computes a comp key
imports this one.

SCOPE: F0-S2 pins the module path, the two-argument signature, and the
minimum normalization a key must survive (case and whitespace). The full
normalization *rules* — what counts as the same street address, e.g.
"W" vs "West", "#3" vs "Unit 3" — are F13-S1's, and they can tighten this
function without changing a single call site.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rentcomp.models.domain import StitchedComp

__all__ = ["comp_key", "disambiguate_keys"]

#: Separator between the address and unit parts. A character that cannot occur
#: in a normalized address part, so two different (address, unit) pairs can
#: never collapse onto one key by concatenation.
_SEPARATOR = "|"


def _normalize(part: str | None) -> str:
    """Casefold, collapse internal runs of whitespace, strip the ends.

    `str.split()` with no argument splits on arbitrary whitespace runs
    (including tabs and newlines), so this is total over the whitespace the
    API and hand-typed addresses actually contain.
    """
    if part is None:
        return ""
    return " ".join(part.split()).casefold()


def comp_key(address: str, unit: str | None) -> str:
    """The stable identity of a comp: normalized address + unit.

    A missing unit is distinct from any present unit — "1234 W Fake St" (the
    whole building, or a listing that never named its unit) is not the same
    listing as "1234 W Fake St #2".
    """
    return f"{_normalize(address)}{_SEPARATOR}{_normalize(unit)}"


def disambiguate_keys(comps: Sequence["StitchedComp"]) -> list[str]:
    """The final derivation/wire identity for a set of comps (WS-1a
    [DEFAULT]) — `comp_key(address, unit)`, extended only where it collides.

    Architecture checkpoint 2 (QUEUE.md row 6a): one normalized address+unit
    can legitimately shape into 2+ disjoint `StitchedComp` chains — genuinely
    separate vacancy episodes whose gap already cleared F4-S3's stitch
    threshold, so they were never merged into one chain in the first place.
    `comp_key`'s own two-argument signature stays exactly as F13-S1 pins it
    (`test_comp_key_is_derived_from_address_and_unit`) — this function wraps
    it rather than changing it.

    `cohort_year` alone does not disambiguate: real data has colliding
    groups where both disjoint chains started in the same cohort year. The
    chain's own stitched-start date (`StitchedComp.first_listed`) does, since
    two disjoint chains started far enough apart to have failed to stitch —
    by construction, not by luck. A key is only ever extended when its base
    `comp_key` actually collides, so a single, non-colliding chain still
    round-trips to exactly `comp_key(address, unit)`.

    A final uniqueness pass guarantees the invariant holds even in the
    (unobserved on real data, but not provably impossible) case where two
    disjoint chains share both a base key and a stitched-start date — this
    function must never return a duplicate, full stop.
    """
    base_keys = [comp_key(comp.address, comp.unit) for comp in comps]
    counts = Counter(base_keys)

    candidates: list[str] = []
    for comp, base in zip(comps, base_keys):
        if counts[base] <= 1:
            candidates.append(base)
            continue
        disambiguator = comp.first_listed.isoformat() if comp.first_listed is not None else "unknown-start"
        candidates.append(f"{base}{_SEPARATOR}{disambiguator}")

    seen: dict[str, int] = {}
    keys: list[str] = []
    for key in candidates:
        seen[key] = seen.get(key, 0) + 1
        keys.append(key if seen[key] == 1 else f"{key}{_SEPARATOR}{seen[key]}")
    return keys
