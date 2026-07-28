"""F2-S2 — bed/bath range parser.

Turns the user-facing hyphen form ("2" / "1-3") into what
``rentcomp.client.query.rentcast_range`` / ``build_listing_params`` already
know how to speak. This module does not carry its own copy of the colon-
syntax rule — every range it emits is produced by calling ``rentcast_range``
directly, so the two can never silently diverge (see
``test_a_range_cannot_drift_from_rentcast_range``).

Bedrooms and bathrooms differ only in whether fractional values are legal
(bathrooms: yes, spec §2.1; bedrooms: no, whole units only — "0" is the
documented studio spelling). Everything else — bare-means-exact-match (the
OPPOSITE of ``daysOld``'s bare-means-maximum rule), degenerate ranges still
emitting colon syntax, `None` meaning "omitted" vs. an empty string being a
caller bug, and rejecting (never swapping) `min > max` — is identical
between the two fields. See ``backend/tests/unit/test_beds_baths_parser.py``
for the pinned contract this implements.
"""

from __future__ import annotations

import re

from rentcomp.client.query import rentcast_range

__all__ = ["parse_bed_bath_field"]

_VALID_FIELDS = ("bedrooms", "bathrooms")

#: A non-negative integer or decimal, e.g. "2", "1.5". No sign, no exponent,
#: no "inf"/"NaN" — those all fail this pattern and fall through to the
#: generic "cannot parse" error.
_NUM = r"\d+(?:\.\d+)?"
_BARE_RE = re.compile(rf"^{_NUM}$")
_RANGE_RE = re.compile(rf"^({_NUM})\s*-\s*({_NUM})$")


def _to_number(text: str) -> int | float:
    """Mirrors the int-vs-float choice QA's round-trip test makes: an
    integer literal stays an int (so `rentcast_range` stringifies it as "1",
    not "1.0"); anything with a decimal point becomes a float."""
    return float(text) if "." in text else int(text)


def parse_bed_bath_field(raw: str | None, *, field: str) -> str | None:
    """Parse a user-facing bedrooms/bathrooms value into RentCast query
    syntax.

    * ``None`` -> ``None`` (field left blank — the signal
      ``build_listing_params`` needs to omit the param entirely).
    * A bare value ("2") -> passed through unchanged (exact match on this
      endpoint; bedrooms/bathrooms bare does NOT mean "maximum" the way
      ``daysOld`` bare does).
    * "min-max" ("1-3") -> ``rentcast_range(min, max)`` colon syntax
      ("1:3"), including degenerate ranges ("2-2" -> "2:2").
    * Anything else unparseable, an empty/whitespace-only string, or
      `min > max` raises ``ValueError`` naming the field and the bad input.

    :param field: "bedrooms" or "bathrooms" — controls whether fractional
        values are legal (bathrooms only).
    """
    if field not in _VALID_FIELDS:
        raise ValueError(f"unknown bed/bath field {field!r}; expected one of {_VALID_FIELDS}")

    if raw is None:
        return None

    stripped = raw.strip()
    if stripped == "":
        raise ValueError(
            f"{field}: empty string is not a valid value (pass None to omit the filter, "
            f"got {raw!r})"
        )

    bare_match = _BARE_RE.match(stripped)
    if bare_match:
        if field == "bedrooms" and "." in stripped:
            raise ValueError(f"{field}: fractional values are not allowed, got {raw!r}")
        return stripped

    range_match = _RANGE_RE.match(stripped)
    if range_match:
        lo_str, hi_str = range_match.group(1), range_match.group(2)
        if field == "bedrooms" and ("." in lo_str or "." in hi_str):
            raise ValueError(f"{field}: fractional values are not allowed, got {raw!r}")
        lo, hi = _to_number(lo_str), _to_number(hi_str)
        if lo > hi:
            raise ValueError(
                f"{field}: min must not be greater than max, got {raw!r} "
                f"({lo} > {hi}) — this parser rejects rather than swaps"
            )
        return rentcast_range(lo, hi)

    raise ValueError(
        f"{field}: cannot parse {raw!r} as a bed/bath value "
        "(expected a bare number like '2' or a range like '1-3')"
    )
