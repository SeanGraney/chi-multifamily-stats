"""RentCast wire syntax — the exact param spelling the API actually accepts.

**This module is the single source of the syntax that spends money.** It was
learned the expensive way: T-S3 round 1 sent commas and bare numeric values,
got an empty result set back, and that empty result looked exactly like a thin
market rather than like a malformed query (QUEUE log 2026-07-26). Eight live
calls out of a 50/month cap bought the two rules below, so they live in one
place and both callers import them:

    scripts/gate.py            the live go/no-go harness (re-exports these)
    rentcomp/client/rentcast.py  the product's client

The two rules, both [INVARIANT] as of F0-S4:

1. **Numeric ranges are ONE param, colon-separated.** There is no `daysOldMin`
   / `daysOldMax`. `1:1095`, `2000:*`, `*:10`. A *bare* value is read by
   RentCast as the range MAXIMUM, not as an exact match — so a degenerate
   range must still be written `5:5`.
2. **Multiple values are ONE param, pipe-separated.** `Multi-Family|Apartment`.
   A comma matches nothing at all.

Parsing user input *into* these strings is F2-S2's; this module only emits.
Deciding *which* windows to ask for is F4-S1's. Nothing here does I/O, imports
httpx, or reads the environment — it is pure string assembly, which is why
`scripts/gate.py` can import it without dragging the backend package's runtime
dependencies into a standalone script.
"""

from __future__ import annotations

from collections.abc import Iterable

__all__ = [
    "MAX_LIMIT",
    "build_listing_params",
    "rentcast_multi",
    "rentcast_range",
]

#: The `/listings` schema caps `limit` at 500. Asking for more does not raise —
#: it silently truncates, which is the failure mode `includeTotalCount` exists
#: to make visible.
MAX_LIMIT = 500


def rentcast_range(min_value: object | None, max_value: object | None) -> str:
    """RentCast numeric-range syntax: ONE param, colon-separated — `min:max`,
    open-ended with `*` (`2000:*`, `*:10`).

    A BARE single value (e.g. `daysOld=5`) is treated by RentCast as the range
    MAXIMUM, not an exact match — so a degenerate range must still be emitted
    as colon syntax (`5:5`), never bare. Fully unpinned (None, None) is a
    caller bug, not a query: raise rather than emit `*:*`, which would silently
    widen a pull to everything the radius contains.

    Shared syntax: bedrooms/bathrooms ranges use this identically (F2-S2
    inherits it).
    """
    if min_value is None and max_value is None:
        raise ValueError("rentcast_range requires at least one bound (got None, None)")
    lo = "*" if min_value is None else str(min_value)
    hi = "*" if max_value is None else str(max_value)
    return f"{lo}:{hi}"


def rentcast_multi(values: Iterable[object]) -> str:
    """RentCast multiple-value syntax: ONE param, pipe-separated —
    `Multi-Family|Apartment|Townhouse`. Commas do not match anything.
    Ranges and multi-values cannot be combined in one param."""
    values = list(values)
    if not values:
        raise ValueError("rentcast_multi requires at least one value")
    return "|".join(str(v) for v in values)


def build_listing_params(
    *,
    address: str,
    radius: float,
    bedrooms: str,
    bathrooms: str | None = None,
    property_types: Iterable[str],
    status: str,
    days_old: str,
    offset: int | None = None,
) -> dict:
    """The wire params for ONE `/listings/rental/long-term` call.

    One call is one (status x daysOld-range x offset) query — `status` is
    single-valued on this endpoint (spec §3.2), so a full pull is at least two
    calls per year window.

    The dict this returns is *also* the fixture-lookup key
    (`fixture_signature`), so three things that look like tidying are load-
    bearing and must not be "fixed":

    * `includeTotalCount` is the STRING `"true"`, not a bool. That is what the
      gate sent for all 8 committed responses; a bool re-keys the signature and
      orphans every fixture in `fixtures/live-samples/`.
    * `limit` is an int (`500`), not `"500"`. Same reason.
    * An omitted `bathrooms` means NO `bathrooms` key at all — not `""` and not
      `"*:*"`. T-S3 round 2 found that sending an empty bathrooms filter makes
      the server *exclude* records whose bathroom count is missing, which is a
      silent narrowing of the comp set. Likewise `offset` is absent on page 0,
      so page 0 hashes to the same signature the gate's un-offset call did.

    `bedrooms` and `bathrooms` pass through verbatim: bare means exact match,
    colon syntax means a range, and only the caller knows which it wanted.
    `days_old` is the exception — a bare value there is never what anyone meant
    (see `rentcast_range`), so it is refused.
    """
    if ":" not in str(days_old):
        raise ValueError(
            f"days_old must be RentCast colon-range syntax (e.g. '1:1095'), got {days_old!r}. "
            "RentCast reads a bare daysOld value as the range MAXIMUM, so a bare value would "
            "silently widen the pull to 'everything listed in the last N days'."
        )

    params: dict = {
        "address": address,
        "radius": radius,
        "bedrooms": bedrooms,
        "propertyType": rentcast_multi(property_types),
        "status": status,
        "daysOld": days_old,
        "limit": MAX_LIMIT,
        # How the client learns it was truncated: the gate's broad Inactive
        # pull returned 500 records with X-Total-Count: 690 (F4-S7 §3). Without
        # this there is no way to tell a complete pull from a capped one.
        "includeTotalCount": "true",
    }
    if bathrooms is not None:
        params["bathrooms"] = bathrooms
    if offset:
        # Falsy (None or 0) => absent, so page 0 is byte-identical to a
        # single-page query and hits the same fixture.
        params["offset"] = offset
    return params
