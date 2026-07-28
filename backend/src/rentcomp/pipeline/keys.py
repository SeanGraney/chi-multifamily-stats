"""Comp identity — F13-S1 [INVARIANT], pinned here as one importable function.

"Re-key selections/weights by normalized address+unit — ids can churn." A
listing id survives a refresh only by luck, and a curation state keyed on luck
silently loses the user's work.

The reason this is a *module* and not three lookalike helpers: the wire keys
(`DerivedComp.key`), the workspace keys (F1-S2), and the refresh re-keying
(F13-S1) must provably be the same function. Anything that computes a comp key
imports this one.

SCOPE: F0-S2 pins the module path, the two-argument signature, and the
minimum normalization a key must survive (case and whitespace). **F4-S2
[INVARIANT] owns the normalization rules themselves** — "normalize
address+unit (case, abbreviations, unit designators) as the grouping key",
because F4-S2 is where "which listings are the same unit" is decided. F13-S1
owns *re-keying curation state with* those rules after a refresh, not
authoring them.

(Erratum, corrected under F4-S2: this docstring previously deferred the
abbreviation and unit-designator rules — its own "'W' vs 'West', '#3' vs
'Unit 3'" examples — to F13-S1. That contradicted F4-S2's story text, which
is the spec. PM ruling, logged on F4-S2.)
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

#: F4-S2 [INVARIANT], the "abbreviations" half: one spelling per address
#: token, so a pull that writes "3651 South Wood Street" on one listing and
#: "3651 S Wood St" on another does not split one unit into two comps.
#:
#: Every entry is a *spelling* canonicalization — a long form mapped onto its
#: already-standard short form. Nothing is ever deleted, which is what keeps
#: this from over-merging: "100 S St Louis Ave" and "100 S Louis Ave" stay
#: distinct because the "St" in "St Louis" survives normalization as a token
#: even though it is not a street suffix. Deleting suffixes (rather than
#: canonicalizing them) would collapse those two real Chicago addresses.
_ADDRESS_ABBREVIATIONS = {
    # street types (USPS standard suffix abbreviations)
    "street": "st",
    "avenue": "ave",
    "av": "ave",
    "boulevard": "blvd",
    "road": "rd",
    "drive": "dr",
    "lane": "ln",
    "court": "ct",
    "place": "pl",
    "terrace": "ter",
    "parkway": "pkwy",
    "highway": "hwy",
    "circle": "cir",
    "square": "sq",
    "trail": "trl",
    "plaza": "plz",
    # directionals
    "north": "n",
    "south": "s",
    "east": "e",
    "west": "w",
    "northeast": "ne",
    "northwest": "nw",
    "southeast": "se",
    "southwest": "sw",
}

#: F4-S2 [INVARIANT], the "unit designators" half: the leading word that
#: labels a unit carries no identity — `Unit 3`, `Apt 3`, `# 3`, `#3` and a
#: bare `3` are one physical unit. Observed on the committed 539-record pull:
#: `Unit` x346, `Apt` x94, `#` x3.
#:
#: The designator is only ever dropped when it is the *whole first token*, so
#: `Units 1-2` (a label, not a designator) is left alone, and the label that
#: follows it is never touched — `Unit 1F` and `Unit 1R` stay two units.
_UNIT_DESIGNATORS = frozenset({"unit", "apt", "apartment", "#"})

#: Punctuation that carries no identity at a token's edge: "St." is "St",
#: "#3" is unit "3".
_TRIM = ".,"


def _tokens(part: str | None) -> list[str]:
    """Casefold, split on arbitrary whitespace runs, trim edge punctuation.

    `str.split()` with no argument splits on any whitespace run (including
    tabs and newlines), so this is total over the whitespace the API and
    hand-typed addresses actually contain.
    """
    if part is None:
        return []
    return [trimmed for token in part.casefold().split() if (trimmed := token.strip(_TRIM))]


def _normalize_address(address: str | None) -> str:
    """Case/whitespace/punctuation-insensitive, one spelling per token."""
    return " ".join(_ADDRESS_ABBREVIATIONS.get(token, token) for token in _tokens(address))


def _normalize_unit(unit: str | None) -> str:
    """The unit label with its designator stripped, or "" if there is none."""
    tokens = _tokens(unit)
    if not tokens:
        return ""

    first = tokens[0]
    if first in _UNIT_DESIGNATORS:
        rest = tokens[1:]
    elif first.startswith("#"):
        rest = [first.lstrip("#"), *tokens[1:]]
    else:
        rest = tokens

    # A designator with nothing after it *is* the label (a listing whose
    # `addressLine2` is literally "Unit"); dropping it would key that comp as
    # the whole building.
    label = " ".join(token for token in rest if token)
    return label or " ".join(tokens)


def comp_key(address: str, unit: str | None) -> str:
    """The stable identity of a comp: normalized address + unit.

    A missing unit is distinct from any present unit — "1234 W Fake St" (the
    whole building, or a listing that never named its unit) is not the same
    listing as "1234 W Fake St #2".
    """
    return f"{_normalize_address(address)}{_SEPARATOR}{_normalize_unit(unit)}"


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
