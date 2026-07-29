"""F4-S3 — the three F4-S2 pickups this story inherits, plus one open
question it surfaces. QA-authored, written RED before the developer starts.

Layer 1 throughout: every assertion imports a function or a class and calls
it with plain values.

WHAT IS IN HERE AND WHAT IS NOT
-------------------------------
Pickup 1a — the two provably false docstrings in `_completeness` — has **no
test row**, on purpose. A docstring is not observable from a test, and the
claim it makes ("value-derived only, never a position in the input") is
*already* pinned false by `test_f4s2_spell_extraction.py`'s strict xfail. It
is a diff-review item at QA verify, not a spec.

Pickup 3 — the Layer-1 seam — is specified in
`test_f4s3_stitch_properties.py`, where it is used.

That leaves pickup 1b and pickup 2 here, plus the `relist_count` question
below, which nobody assigned but which this story's own field list raises.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import extract_spells, shape_raw_pull
from rentcomp.storage.config import Config

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")


def _record(id_: str, address: str, unit: str | None, listed: str, removed: str | None, **over) -> dict:
    record = {
        "id": id_,
        "formattedAddress": f"{address}, {unit}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": unit,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 1000,
        "status": "Active" if removed is None else "Inactive",
        "price": 2000.0,
        "listingType": "Standard",
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed}T00:00:00.000Z",
        "lastSeenDate": f"{removed or listed}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }
    record.update(over)
    return record


# ---------------------------------------------------------------------------
# PICKUP 2 — `Spell` moves to `models/domain.py`
# ---------------------------------------------------------------------------


def test_spell_lives_in_the_domain_model_layer() -> None:
    """`models/domain.py`'s own docstring says so: "`Spell` is intentionally
    absent: it is the stitcher's intermediate artifact and nothing in the
    derivation graph consumes it. **F4-S3 adds it.**"

    This is the layer rule from CLAUDE.md, not housekeeping: `dto.py` is wire
    truth, `domain.py` is our truth, `responses.py` is the contract. A `Spell`
    defined inside `pipeline/` is a domain type living in the derivation
    layer, which is how DTOs start leaking past the pipeline boundary."""
    from rentcomp.models import domain

    assert hasattr(domain, "Spell"), (
        "F4-S3 owes `rentcomp.models.domain.Spell` — `domain.py`'s docstring already promises it"
    )
    assert "Spell" in getattr(domain, "__all__", ()), (
        "`Spell` is defined in `models/domain.py` but missing from its `__all__`"
    )


def test_the_old_import_path_still_resolves_to_the_same_class() -> None:
    """Moving a type is not a licence to break the specs that import it.

    `tests/unit/test_f4s2_spell_extraction.py` does
    `from rentcomp.pipeline.shape import Spell, extract_spells`, and
    AGENT_QA.md forbids weakening or editing an existing spec to get to green.
    A re-export costs one line and keeps that file untouched. Asserted with
    `is` so the re-export is the same class object, not a second definition
    that can drift."""
    from rentcomp.models.domain import Spell as DomainSpell
    from rentcomp.pipeline import shape

    assert getattr(shape, "Spell", None) is DomainSpell, (
        "`rentcomp.pipeline.shape.Spell` must re-export `rentcomp.models.domain.Spell` "
        "(the same object), so F4-S2's spec keeps importing it unchanged"
    )


# ---------------------------------------------------------------------------
# PICKUP 1b — should `_completeness` break ties on field completeness?
#
# CONTINGENT ON A PM RULING. QA's recommendation is YES and this test embodies
# it; if the ruling is NO, delete this test and leave F4-S2's strict xfail in
# place. It is written now rather than after the ruling so the developer has
# something concrete to implement against either way.
# ---------------------------------------------------------------------------


def test_the_more_complete_of_two_observations_of_one_spell_wins() -> None:
    """Pickup 1b, as QA reads it.

    `_completeness` orders two observations of the same listing start on
    `(removed is not None, removed, price)`. Those two leading components are
    the semantic ones and they are right: an observed removal beats "still
    active", and the later of two observed removals is the more recent
    observation. What is missing is the thing the function is *named* for.

    When two observations agree on removal and price but one reports
    `squareFootage` and the other does not, the winner today is whichever was
    seen first — arrival order, in a function whose docstring claims to be
    "value-derived only, never a position in the input". And `sqft` is not a
    cosmetic ride-along: a missing-sqft comp is default-excluded from every
    cohort median (F4-S5), so the field most likely to differ between two
    reports of one unit is the one that decides whether the comp counts at
    all.

    Preferring the more complete observation cannot invent a value — it can
    only prefer a reported one over an absent one — so it is strictly more
    evidence, never less.

    Reachable only because of F4-S2: before it, two listing ids at one address
    were separate comps and were never compared. It is not reachable on the
    committed pull (no two ids in one group report the same spell start
    today), which is why it did not fail F4-S2 and why it is a ruling rather
    than a defect."""
    without_sqft = _record("obs-a", "300 W Order St", "Unit 1", "2026-05-01", "2026-06-10")
    with_sqft = _record("obs-b", "300 W Order St", "Apt 1", "2026-05-01", "2026-06-10")
    without_sqft["squareFootage"] = None
    with_sqft["squareFootage"] = 1000

    for order in ([without_sqft, with_sqft], [with_sqft, without_sqft]):
        spells = extract_spells(order)
        assert len(spells) == 1, "these are two observations of one spell, not two vacancies"
        assert spells[0].sqft == 1000.0, (
            "the observation that reported a squareFootage lost to the one that did not "
            f"(input order: {[r['id'] for r in order]}). The surviving spell has "
            f"sqft={spells[0].sqft!r}, so this comp is default-excluded from every cohort "
            "median even though the pull reported its size."
        )


def test_a_completeness_tie_break_does_not_change_which_removal_wins() -> None:
    """The guard on the ruling above: whatever gets added to `_completeness`
    must sort *below* the removal components, never above them.

    A completeness score that outranked `removed is not None` would let a
    richly-populated but still-active observation beat an observation that saw
    the unit removed — censoring a comp that actually leased, which NORTH_STAR
    calls the single most important distinction in the system and which
    biases every Kaplan-Meier curve toward pessimism."""
    active_rich = _record("rich-active", "301 W Order St", "Unit 1", "2026-05-01", None)
    removed_sparse = _record("sparse-removed", "301 W Order St", "Apt 1", "2026-05-01", "2026-06-10")
    removed_sparse["squareFootage"] = None
    removed_sparse["bathrooms"] = None

    for order in ([active_rich, removed_sparse], [removed_sparse, active_rich]):
        spells = extract_spells(order)
        assert len(spells) == 1
        assert spells[0].removed == date(2026, 6, 10), (
            "an observation with more fields populated beat one that observed the removal — "
            "a leased unit is now censored evidence"
        )


# ---------------------------------------------------------------------------
# AN OPEN QUESTION THIS STORY SURFACES — `relistCount`
#
# Pinned as a strict xfail, the mechanism F4-S2's QA invented: a known gap
# that announces its own repair. If a ruling lands and the behaviour changes,
# this XPASSes and fails, demanding its own marker be removed. Do not delete
# the marker silently, and do not "fix" the code to keep it red.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN QUESTION referred to the PM, not an AC violation. F4-S3 lists `relistCount` among "
        "the merged record's fields but never defines it. It counts merged *spells* today, so a "
        "contiguous price change badges as a re-list: 27 comps in the committed pull carry "
        "relist_count > 0 with gap_days == 0, including `2411 W 34th Pl Unit 2` (relist_count 2, "
        "0 days off market) which the F4-S7 report names explicitly as one spell with two price "
        "changes. Pinned strict so it converts to a green test the moment a ruling lands."
    ),
)
def test_a_comp_that_was_never_off_market_reports_no_relists() -> None:
    """The definitional question, stated as the property it would imply.

    A re-list badge (`[RELIST] x1, 6d gap`, spec §6.4) tells the user this
    unit came off the market and went back on. `gap_days == 0` says it never
    did. The two cannot both be true of the same row, and today 27 real comps
    say both — 49 comps report a re-list while only 22 report any off-market
    time at all.

    QA's reading, offered as a recommendation and not asserted as an
    invariant: a re-list is a merge across *days off market*, and a contiguous
    or overlapping segment is a price change, which `cut_history` already
    records. The alternative reading — `relistCount` means "how many reported
    listing segments were stitched into this record" — is defensible too, but
    then the row badge is showing a data-plumbing count to a user who reads it
    as market behaviour."""
    active = json.loads((FIXTURES_DIR / "fe9de5158f036802.json").read_text())
    inactive = json.loads((FIXTURES_DIR / "6327600317b11d16.json").read_text())
    comps = shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)

    phantom = [
        (c.address, c.unit, c.relist_count, len(c.cut_history))
        for c in comps
        if c.relist_count > 0 and c.gap_days == 0
    ]
    assert not phantom, (
        f"{len(phantom)} comps badge a re-list without a single day off market, e.g. "
        f"{phantom[:5]}"
    )
