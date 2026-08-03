"""F4-S2 [INVARIANT] — AC2's consequence where the user actually meets it:
the derived evidence base. QA-authored, written RED before the developer
starts (AGENT_QA.md protocol).

Layer 2 (pytest API contract, `POST /api/derive` over `TestClient`). One
file, three assertions — deliberately thin.

WHY ANYTHING AT L2 AT ALL, WHEN AC2 IS FULLY ASSERTABLE AT L1
------------------------------------------------------------
AGENT_QA.md's split rule: exhaustive below, representative above.
`backend/tests/unit/test_f4s2_grouping_key.py` owns the exhaustive
normalization matrix. What it cannot see is whether the normalization is
the one that reaches the wire.

`pipeline/derive.py` builds `DerivedComp.key` via
`keys.disambiguate_keys` -> `keys.comp_key(comp.address, comp.unit)`, over
the *raw* address/unit strings carried on the `StitchedComp`. So a developer
who normalizes inside `pipeline/shape.py`'s grouping instead of inside
`comp_key` would make every L1 grouping test pass while the wire keys —
the identity F5-S2 weights by, F7-S1 overrides by, F13-S1 re-keys by and
F14 persists — stayed un-normalized. That failure is invisible from L1 and
invisible from a browser. It belongs exactly here.

Decision procedure: "given curation state X the API returns Y" — Q2, L2.

REAL DATA, ZERO LIVE CALLS
--------------------------
`pull_ref="ws1-real"` routes the committed 539-record gate pull
(`fixtures/live-samples/`) through the real shaping chain — the same ref
`test_derive_contract.py::test_comp_keys_are_unique_over_the_real_ws1_pull`
already uses. Nothing here touches the network (D17).
"""

from __future__ import annotations

from collections import Counter

import pytest

REAL_PULL_REF = "ws1-real"

#: Four physical units the committed pull lists twice, under two listing ids
#: with different unit designators. Verbatim from the fixtures; the F4-S7
#: first-pull analysis names two of them (`2453 W 46th Pl` churn record,
#: `2350 S Leavitt St`).
DUPLICATED_UNITS = [
    ("2453 W 46th Pl", ("# 1", "Unit 1")),
    ("3643 S Hamilton Ave", ("# 3", "Unit 3")),
    ("2350 S Leavitt St", ("# 1R", "Unit 1R")),
    ("2835 W 38th Pl", ("Unit 2", "Apt 2")),
]


@pytest.fixture
def real_comps(derive):
    response = derive(pull_ref=REAL_PULL_REF)
    assert response.status_code == 200, (
        f"POST /api/derive with pull_ref={REAL_PULL_REF!r} returned {response.status_code}: "
        f"{response.text[:600]}"
    )
    comps = response.json()["comps"]
    assert comps, "the real ws1-real pull must shape into at least some comps"
    return comps


@pytest.mark.parametrize(("address", "designators"), DUPLICATED_UNITS)
def test_a_unit_listed_twice_reaches_the_wire_once(real_comps, address, designators) -> None:
    """F4-S2 AC2 at the contract boundary: "two records at the same
    normalized address+unit merge into one group."

    Each of these is one physical unit that RentCast returned under two
    listing ids. Today it arrives as two `DerivedComp`s, so the same real
    vacancy is counted twice in the cohort median behind `premium`, twice in
    every bucket count, and twice in the KM risk set — one landlord's
    listing habits weighted double."""
    matches = [
        comp for comp in real_comps if comp["address"] == address and comp["unit"] in designators
    ]
    assert matches, f"oracle drift: no comp at {address!r} with unit in {designators}"

    by_start = Counter(comp["key"] for comp in matches)
    assert len(by_start) == 1, (
        f"{address!r} reaches the wire as {len(by_start)} distinct comps "
        f"{sorted(by_start)} — the same unit, listed once under each of {designators}, is "
        "double-counted in every downstream statistic"
    )


def test_the_wire_key_is_the_normalized_key_not_the_raw_designator(real_comps) -> None:
    """The wire keys must be produced by the same normalization the grouping
    used, or F13-S1's refresh re-keying silently drops the user's curation the
    first time a listing's designator changes from `Apt 2` to `Unit 2`.

    Asserted as a property, not a format: no comp's key may still be
    distinguishable from another comp's key *only* by its unit designator
    spelling. Deliberately says nothing about what the key string looks
    like — that stays a [DEFAULT].

    The one structural assumption made, and it is WS-1a's own: a key is
    `comp_key(address, unit)` (two `|`-joined parts), optionally extended
    with a disambiguator when — and only when — that base actually collides.
    Two disjoint stitched chains of the SAME unit therefore share a base and
    are correctly NOT offenders here: "one address+unit is one group" (this
    story) is not "one address+unit is one comp" (which WS-1a proved false on
    this very pull, 34 real collisions)."""
    stripped_to_keys: dict[tuple[str, str], set[str]] = {}
    for comp in real_comps:
        unit = (comp["unit"] or "").casefold().replace(".", "")
        for prefix in ("unit ", "unit", "apt ", "apt", "# ", "#"):
            if unit.startswith(prefix):
                unit = unit[len(prefix) :]
                break
        signature = (comp["address"].casefold(), unit.strip())
        stripped_to_keys.setdefault(signature, set()).add(comp["key"])

    def base_of(key: str) -> str:
        return "|".join(key.split("|")[:2])

    offenders = {
        signature: sorted(keys)
        for signature, keys in stripped_to_keys.items()
        if len({base_of(k) for k in keys}) > 1
    }
    assert not offenders, (
        "comps that differ only in unit-designator spelling carry different base wire keys — "
        f"the normalization did not reach `comp_key`: {offenders}"
    )


def test_merging_duplicates_does_not_shrink_the_evidence_base(real_comps) -> None:
    """The counterweight to every assertion above: AC2 asks for *fewer*
    comps, which is a direction a bug can also produce. The real pull shapes
    into 571 comps today; merging the four duplicated units removes exactly
    four. Anything below 550 means grouping is eating genuine evidence — most
    likely by conflating WS-1a's disjoint-chain case ("one address+unit can
    produce 2+ separate vacancy episodes", 34 real collisions) with this
    story's duplicate-record case. They are not the same claim."""
    assert len(real_comps) >= 550, (
        f"the real pull derived only {len(real_comps)} comps (571 before this story, 567 "
        "expected after the four duplicate units merge) — grouping is dropping real evidence"
    )
