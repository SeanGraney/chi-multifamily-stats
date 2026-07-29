"""F4-S2 [INVARIANT] — the grouping key: "normalize address+unit (case,
abbreviations, unit designators)". QA-authored, written RED before the
developer starts (AGENT_QA.md protocol).

Layer 1 (pytest unit). Decision procedure Q1: `comp_key` is an importable
pure function called with plain strings — no request body, no assembled
state, no browser. Nothing below needs a higher layer.

WHY THIS FILE POINTS AT `pipeline.keys.comp_key` AND NOT A PRIVATE HELPER
------------------------------------------------------------------------
`pipeline/keys.py`'s own module docstring is explicit: "Anything that
computes a comp key imports this one" — the wire keys (`DerivedComp.key`),
the workspace keys (F1-S2) and the refresh re-keying (F13-S1) must provably
be the same function, not three lookalikes. So F4-S2's normalization rules
belong *inside* `comp_key`, not in a private normalizer in `pipeline/shape.py`.
If they land in `shape.py` instead, grouping would be correct while
`DerivedComp.key`/F13-S1 re-keying stayed un-normalized — a silent split
between "which listings are the same unit" (this story) and "which comp is
the same comp after a refresh" (F13-S1) that no other test would catch.

    AMBIGUITY FLAGGED TO THE PM (QA could not resolve this from the docs
    alone, and is asserting one of the two readings deliberately):
    `keys.py`'s docstring currently defers the abbreviation / unit-designator
    rules to F13-S1 ("The full normalization *rules* — what counts as the
    same street address, e.g. 'W' vs 'West', '#3' vs 'Unit 3' — are
    F13-S1's"). F4-S2's story text names those same rules as *this* story's
    deliverable. QA reads the story text as binding (F4-S2 owns the rules;
    F13-S1 later owns *re-keying with* them) and asserts that here. If the
    PM/owner rules the other way, these tests are QA's to retract, not the
    developer's to work around.

REAL DATA
---------
The real committed pull (`fixtures/live-samples/`, 539 records, zero live
calls — D17) contains **six** address+unit pairs that are the same physical
unit listed twice under different designators. Four of them do NOT merge
today: `# 1`/`Unit 1`, `# 3`/`Unit 3`, `# 1R`/`Unit 1R`, `Unit 2`/`Apt 2`.
That is AC2 failing on real data, not a hypothetical.

The same pull contains **no** spelled-out street types or directionals
("Street", "West") — every address is already abbreviated. So the
abbreviation half of the story's requirement is unfalsifiable against this
fixture and is asserted synthetically below, paired with over-merge guards
drawn from real addresses.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

from rentcomp.pipeline.keys import comp_key

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

ADDR = "3651 S Wood St"


def _real_records() -> list[dict]:
    return json.loads(ACTIVE_FIXTURE.read_text()) + json.loads(INACTIVE_FIXTURE.read_text())


# ---------------------------------------------------------------------------
# already-true invariants — regression guards, must stay green
# ---------------------------------------------------------------------------


def test_the_key_is_case_and_whitespace_insensitive() -> None:
    """Pre-existing (F0-S2/WS-1) behaviour. Restated here because F4-S2 is
    where the normalization rules grow, and growing them must not cost the
    rules already relied on."""
    assert comp_key(ADDR, "2") == comp_key("  3651   s wood st ", "2")


def test_a_missing_unit_is_not_the_same_comp_as_a_present_unit() -> None:
    """Pre-existing (`keys.py`): "1234 W Fake St" (the whole building, or a
    listing that never named its unit) is not "1234 W Fake St #2"."""
    assert comp_key(ADDR, None) != comp_key(ADDR, "2")


def test_distinct_units_at_one_address_keep_distinct_keys() -> None:
    """The failure mode that would make AC2 "pass" for the wrong reason:
    stripping the unit rather than normalizing it."""
    assert comp_key(ADDR, "Unit 1") != comp_key(ADDR, "Unit 2")
    assert comp_key(ADDR, "Unit 1F") != comp_key(ADDR, "Unit 1R")
    assert comp_key(ADDR, "Apt A") != comp_key(ADDR, "Apt B")


# ---------------------------------------------------------------------------
# AC2, half 1 — unit designators (the half real data proves is broken today)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("designator", ["Unit 3", "unit 3", "Apt 3", "APT 3", "# 3", "#3", "3"])
def test_unit_designators_normalize_to_one_key(designator: str) -> None:
    """F4-S2 [INVARIANT]: "normalize address+unit (case, abbreviations, unit
    designators) as the grouping key".

    All seven spellings are the same physical unit. `Unit 3` / `# 3` / `Apt 3`
    are the exact forms the real pull mixes (real prefixes on 539 records:
    Unit x346, Apt x94, # x3)."""
    assert comp_key(ADDR, designator) == comp_key(ADDR, "Unit 3"), (
        f"unit designator {designator!r} keys differently from 'Unit 3' — the same unit "
        "would be counted as two comps in every downstream statistic"
    )


def test_a_suffixed_unit_label_normalizes_across_designators() -> None:
    """The real `2350 S Leavitt St` case: `# 1R` and `Unit 1R`. The unit
    label is not always a bare integer."""
    assert comp_key("2350 S Leavitt St", "# 1R") == comp_key("2350 S Leavitt St", "Unit 1R")


# ---------------------------------------------------------------------------
# AC2, half 2 — address abbreviations (synthetic: the real pull is uniformly
# abbreviated, so it cannot falsify this half)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "canonical"),
    [
        ("3651 S Wood Street", "3651 S Wood St"),
        ("3651 S Wood St.", "3651 S Wood St"),
        ("3651 South Wood St", "3651 S Wood St"),
        ("2652 W 23rd Place", "2652 W 23rd Pl"),
        ("3643 S Hamilton Avenue", "3643 S Hamilton Ave"),
        ("1000 W Test Road", "1000 W Test Rd"),
    ],
)
def test_street_type_and_directional_abbreviations_normalize_to_one_key(
    variant: str, canonical: str
) -> None:
    """F4-S2 [INVARIANT]: "abbreviations". A pull that spells one listing
    "West ... Street" and another "W ... St" must not split one unit in two.

    NOTE the [DEFAULT]/[INVARIANT] split: *which* direction the normalizer
    canonicalizes in (expand or abbreviate) is the developer's call — this
    only asserts the two spellings agree."""
    assert comp_key(variant, "Unit 2") == comp_key(canonical, "Unit 2")


@pytest.mark.parametrize(
    ("left", "right", "why"),
    [
        ("3651 S Wood St", "3651 W Wood St", "S and W are different streets"),
        ("3651 S Wood St", "3651 S Wood Ave", "St and Ave are different streets"),
        ("3651 S Wood St", "3652 S Wood St", "different house numbers"),
        ("100 S St Louis Ave", "100 S Louis Ave", "the 'St' in 'St Louis' is not a street suffix"),
    ],
)
def test_abbreviation_normalization_never_merges_different_addresses(
    left: str, right: str, why: str
) -> None:
    """The over-merge guard that keeps the test above from being satisfied by
    deleting street types outright. `S St Louis Ave` is a real Chicago
    street."""
    assert comp_key(left, "Unit 2") != comp_key(right, "Unit 2"), why


# ---------------------------------------------------------------------------
# AC2 over the REAL committed pull (zero live calls — the files are on disk)
# ---------------------------------------------------------------------------


def test_the_committed_fixtures_are_the_gate_sample() -> None:
    """Guards this file's oracle: if the fixtures move, fail loudly here
    rather than silently weakening every real-data test below."""
    assert len(json.loads(ACTIVE_FIXTURE.read_text())) == 39
    assert len(json.loads(INACTIVE_FIXTURE.read_text())) == 500


@pytest.mark.parametrize(
    ("address", "left", "right"),
    [
        ("2453 W 46th Pl", "# 1", "Unit 1"),
        ("3643 S Hamilton Ave", "# 3", "Unit 3"),
        ("2350 S Leavitt St", "# 1R", "Unit 1R"),
        ("2835 W 38th Pl", "Unit 2", "Apt 2"),
    ],
)
def test_real_duplicate_units_share_one_key(address: str, left: str, right: str) -> None:
    """F4-S2 AC2 on real data: "two records at the same normalized
    address+unit merge into one group."

    Each pair is two distinct RentCast listing ids for one physical unit,
    verbatim from the committed pull. Today all four key differently, so the
    same unit is counted twice in every median, bucket count and KM risk set
    downstream."""
    assert comp_key(address, left) == comp_key(address, right), (
        f"{address!r} {left!r} and {right!r} are the same real unit (two listing ids in the "
        "committed pull) but key differently"
    )


def test_no_two_distinct_units_collapse_over_the_real_pull() -> None:
    """The global over-merge guard, stated as a property rather than a magic
    total so a *legitimately* larger merge set does not fail it.

    Across all 539 real records, the largest set of records sharing one key
    is 2 — the six duplicate-unit pairs above. Anything bigger means the
    normalizer swallowed a real unit distinction: `2122 S Hoyne Ave Unit
    A/B/C/D` (four separate units, all with `addressLine2 = null`) collapses
    to a group of 4 under naive "strip the designator from the address line",
    and `2835 W 38th Pl` Unit 1 + Unit 2 + Apt 2 collapses to 3 under "drop
    the unit entirely"."""
    groups: dict[str, list[str]] = defaultdict(list)
    for record in _real_records():
        address = record.get("addressLine1") or record.get("formattedAddress") or ""
        groups[comp_key(address, record.get("addressLine2"))].append(record["id"])

    oversized = {key: ids for key, ids in groups.items() if len(ids) > 2}
    assert not oversized, (
        "the grouping key merged more than two real records onto one identity — a real unit "
        f"distinction was normalized away: {oversized}"
    )


def test_the_real_pull_groups_into_exactly_the_known_merge_set() -> None:
    """Characterization of the whole pull, kept alongside the property test
    above because the two fail differently: this one names the *count*.

    539 records -> 533 groups: 6 duplicate-unit pairs (4 designator
    mismatches + 2 that already merge on case/whitespace alone). It is 537
    today.

    If a developer's normalizer legitimately merges MORE than this — e.g.
    ruling that `4431 S Sacramento Ave Unit Gdn` and `Unit G` are one unit,
    which QA deliberately did not decide — this number moves and QA re-blesses
    it via the PM. Do not adjust it in place to reach green."""
    keys = {
        comp_key(record.get("addressLine1") or record.get("formattedAddress") or "", record.get("addressLine2"))
        for record in _real_records()
    }
    assert len(keys) == 533, (
        f"the real 539-record pull normalizes to {len(keys)} distinct address+unit groups; "
        "expected 533 (537 = today's under-merging key, see this test's docstring)"
    )


# ---------------------------------------------------------------------------
# 4431 S Sacramento Ave — the case QA deliberately did NOT decide
# ---------------------------------------------------------------------------


def test_the_sacramento_garden_unit_ambiguity_stays_unmerged_and_visible() -> None:
    """Characterization, not a ruling — pinned at QA verify so the decision
    stays a decision.

    The committed pull has three listings at `4431 S Sacramento Ave`, all
    listed **2026-03-02 at $1,400**, removed 2026-04-09 / 04-26 / 04-29:

        `4431 S Sacramento Ave Unit Gdn`  (addressLine2 null, sqft 1000)
        `4431 S Sacramento Ave Unit G`    (addressLine2 null, sqft 1012)
        `4431 S Sacramento Ave` + `Unit 1`            (sqft 1012)

    "Unit Gdn" and "Unit G" are both plausibly *the garden unit*, and two of
    the three agree on sqft to the foot. This smells like one vacancy counted
    up to three times — which would over-weight one landlord in the cohort
    median. It is not provable from the data, so nothing here merges them.

    WHY IT IS PINNED ANYWAY: this address is the concrete instance of the
    `listed`-only spell-identity risk that
    `test_f4s2_spell_extraction.py::test_no_two_listing_ids_in_one_group_report_the_same_spell_start`
    guards abstractly. All three share the start date 2026-03-02, so a future
    normalizer that ruled `Gdn` == `G` == `1` would not just merge groups — it
    would hand `extract_spells` three observations of one start and **silently
    drop two of the five spells**, keeping the latest removal (2026-04-29) and
    the sqft of whichever copy arrived first. Merging here is therefore a
    change that must be made deliberately, with that consequence understood,
    never as a side effect of loosening the normalizer."""
    keys = {
        comp_key(record["addressLine1"], record.get("addressLine2"))
        for record in _real_records()
        if "4431 S Sacramento" in (record.get("addressLine1") or "")
    }
    assert len(keys) == 3, (
        "the three 4431 S Sacramento Ave listings no longer key distinctly. If that was "
        "deliberate, note that all three share the 2026-03-02 start date, so spell extraction "
        f"now silently drops two spells — re-bless via the PM, do not adjust in place: {keys}"
    )
