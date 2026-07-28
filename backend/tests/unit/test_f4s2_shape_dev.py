"""F4-S2 — developer-authored supplement to QA's
`test_f4s2_grouping_key.py` / `test_f4s2_spell_extraction.py` /
`test_f4s2_duplicate_units_real_pull.py`.

QA's plan pins both acceptance criteria exhaustively. This file covers the
implementation risks that came with satisfying them but weren't QA's job to
pin (AGENT_DEVELOPER.md protocol item 4 — "usually all of Layer 1"):

* the spell-identity rule ("a spell is identified by when the listing
  started; the more complete observation of it wins") stated at the
  `extract_spells` seam itself, not only through `shape_raw_pull`. QA asserts
  the *consequence* of this rule on a duplicated listing id; this file
  asserts the rule, including the branch QA's fixtures cannot reach — two
  copies that both observed a removal, on different dates.
* the normalizer's degenerate inputs: a unit that is *only* a designator, and
  a designator carrying punctuation. Both are cases where a plausible
  implementation silently keys a comp as the whole building.
* the four real duplicate units shape into one comp each, and grouping is
  order-independent over the whole real pull. QA's L2 file asserts the wire
  consequence over `/api/derive`; this is the same claim one layer down,
  where a failure names the shaping stage instead of the endpoint.

Zero live API calls — the fixtures are on disk (D17).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.keys import comp_key
from rentcomp.pipeline.shape import Spell, extract_spells, shape_raw_pull
from rentcomp.storage.config import Config

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

FULL_YEAR = ("01-01", "12-31")
AS_OF = date(2026, 7, 27)

#: The four physical units the committed pull lists twice under two listing
#: ids with different unit designators (QA's real-data analysis).
DUPLICATED_UNITS = [
    ("2453 W 46th Pl", ("# 1", "Unit 1")),
    ("3643 S Hamilton Ave", ("# 3", "Unit 3")),
    ("2350 S Leavitt St", ("# 1R", "Unit 1R")),
    ("2835 W 38th Pl", ("Unit 2", "Apt 2")),
]


def _raw(
    *,
    id_: str,
    address: str,
    unit: str | None,
    listed: str,
    removed: str | None,
    price: float = 2000.0,
) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": unit,
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 1000,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "history": {},
    }


# ---------------------------------------------------------------------------
# the spell-identity rule, at the seam
# ---------------------------------------------------------------------------


def test_two_reports_of_one_listing_start_collapse_to_the_observed_removal() -> None:
    """The rule QA asserts through `shape_raw_pull`, stated where it lives.

    Same unit, same start date, one report still open and one reporting a
    removal: that is one spell observed twice, and the observation that saw
    the end is the complete one. Keeping both would hand F4-S3 a
    zero-length "re-list" that never happened."""
    still_open = _raw(id_="a", address="1 W Seam St", unit="Unit 1", listed="2026-05-01", removed=None)
    removed = _raw(
        id_="b", address="1 W Seam St", unit="Unit 1", listed="2026-05-01", removed="2026-06-10"
    )

    spells = extract_spells([still_open, removed])
    assert [(s.listed, s.removed) for s in spells] == [(date(2026, 5, 1), date(2026, 6, 10))]


def test_the_later_of_two_observed_removals_wins() -> None:
    """The branch QA's fixtures cannot reach: both copies saw a removal, on
    different dates. The later date is the more recent observation of the
    same listing, so it is the one kept — and, being value-derived, it is
    kept whichever order the copies arrive in."""
    early = _raw(id_="a", address="2 W Seam St", unit="Unit 1", listed="2026-05-01", removed="2026-06-01")
    late = _raw(id_="b", address="2 W Seam St", unit="Unit 1", listed="2026-05-01", removed="2026-06-15")

    for records in ([early, late], [late, early]):
        spells = extract_spells(records)
        assert [(s.listed, s.removed) for s in spells] == [(date(2026, 5, 1), date(2026, 6, 15))]


def test_extract_spells_returns_its_rows_in_chronological_order() -> None:
    """`_stitch` walks the sequence once and compares each spell against the
    previous one, so an unsorted return would fabricate or miss chains
    depending on input order alone."""
    record = _raw(id_="c", address="3 W Seam St", unit="Unit 1", listed="2026-05-01", removed=None)
    record["history"] = {
        "2024-01-01": {"listedDate": "2024-01-01T00:00:00.000Z", "removedDate": "2024-02-01T00:00:00.000Z", "price": 1800},
        "2025-01-01": {"listedDate": "2025-01-01T00:00:00.000Z", "removedDate": "2025-02-01T00:00:00.000Z", "price": 1900},
    }
    listed = [spell.listed for spell in extract_spells([record])]
    assert listed == sorted(listed) == [date(2024, 1, 1), date(2025, 1, 1), date(2026, 5, 1)]


def test_a_spell_is_hashable_and_compares_by_value() -> None:
    """`shape_raw_pull`'s own order-independence assertion compares shaped
    output by value, and `Spell` is now public — pin that it stays a value
    object rather than acquiring identity semantics."""
    fields = dict(
        listed=date(2026, 1, 1), removed=None, price=2000.0, address="4 W Seam St", unit="Unit 1",
        lat=41.0, lng=-87.0, beds=3.0, baths=1.0, sqft=1000.0,
    )
    assert Spell(**fields) == Spell(**fields)
    assert len({Spell(**fields), Spell(**fields)}) == 1


# ---------------------------------------------------------------------------
# normalizer degenerate inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("designator_only", ["Unit", "unit", "Apt", "#"])
def test_a_unit_that_is_only_a_designator_is_not_keyed_as_the_whole_building(
    designator_only: str,
) -> None:
    """Stripping the designator unconditionally would turn `addressLine2 =
    "Unit"` into an empty unit — i.e. into the key `comp_key` reserves for a
    listing that never named a unit at all. That would merge a named unit
    into the building's own listing, which `keys.py` explicitly forbids."""
    assert comp_key("5 W Seam St", designator_only) != comp_key("5 W Seam St", None)


@pytest.mark.parametrize("spelling", ["Unit 3", "Unit #3", "#3.", "Apt. 3", " apt   3 "])
def test_punctuation_and_spacing_around_a_designator_do_not_change_the_unit(
    spelling: str,
) -> None:
    assert comp_key("6 W Seam St", spelling) == comp_key("6 W Seam St", "3")


def test_a_designator_inside_the_address_line_is_left_alone() -> None:
    """14 real records put the designator in `addressLine1` with a null
    `addressLine2` — `2122 S Hoyne Ave Unit A/B/C/D` are four separate units.
    Applying the unit rules to the address line would collapse them into one
    group of four, which is the over-merge QA's property guard exists for."""
    keys = {comp_key(f"2122 S Hoyne Ave Unit {label}", None) for label in "ABCD"}
    assert len(keys) == 4


# ---------------------------------------------------------------------------
# the real pull, one layer below QA's L2 file
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def real_pull() -> tuple[list[dict], list[dict]]:
    return json.loads(ACTIVE_FIXTURE.read_text()), json.loads(INACTIVE_FIXTURE.read_text())


@pytest.fixture(scope="module")
def real_comps(real_pull):
    active, inactive = real_pull
    return shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)


@pytest.mark.parametrize(("address", "designators"), DUPLICATED_UNITS)
def test_a_real_duplicated_unit_shapes_into_exactly_one_comp(
    real_comps, address: str, designators: tuple[str, str]
) -> None:
    """AC2's consequence at the shaping stage: one physical unit, listed
    under two ids, is one comp — not two rows in the cohort median, two
    bucket members and two entries in the Kaplan-Meier risk set."""
    matches = [c for c in real_comps if c.address == address and c.unit in designators]
    assert len(matches) == 1, (
        f"{address} {designators} shaped into {len(matches)} comps: "
        f"{[(c.unit, c.first_listed, c.effective_dom) for c in matches]}"
    )


def test_shaping_the_real_pull_does_not_depend_on_which_response_a_record_arrived_in(
    real_pull, real_comps
) -> None:
    """The whole-pull version of QA's two-record order-independence test.
    Order is not evidence, at 539 records either."""
    active, inactive = real_pull
    swapped = shape_raw_pull(inactive, active, Config(), AS_OF, *FULL_YEAR)
    assert set(swapped) == set(real_comps)


def test_shaping_the_real_pull_is_deterministic(real_pull, real_comps) -> None:
    active, inactive = real_pull
    assert shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR) == real_comps
