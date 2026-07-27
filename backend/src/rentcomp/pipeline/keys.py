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

__all__ = ["comp_key"]

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
