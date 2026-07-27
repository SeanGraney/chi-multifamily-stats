"""WS-1a — Bug 1 [INVARIANT]: `comp_key` must be unique over one address+unit
producing 2+ disjoint stitched chains. QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

Full background: `rentcomp-pm/docs/rentcomp_technical_stories.md` has no
dedicated story for this (it's a correctness fix born from architecture
checkpoint 2 — see `QUEUE.md` row 6a and the WS-1a dispatch). The invariant
this pins: **one normalized address+unit can legitimately shape into more
than one `StitchedComp` chain** (two genuinely separate vacancy episodes,
e.g. a unit vacant in 2024 and vacant again in 2025 with a gap too large to
stitch — correctly, since a smaller gap would have merged them at F4-S3's
own 42-day threshold). Each such chain must still get its OWN identity.

This is the exhaustive real-data check —
`test_derive_contract.py::test_comp_keys_are_unique_over_the_real_ws1_pull`.
That test alone is not enough to prove a *specific* naive fix is wrong: an
aggregate "571 comps, 537 distinct keys ⇒ FAIL" assertion could, in
principle, still pass by luck against a fix that resolves most-but-not-all
collisions. This file adds a MINIMAL, CONTROLLED reproduction of the single
hardest case in the real data, so the failure (if any) names its own cause
instead of hiding in an aggregate count.

REAL-DATA FINDING FOR THE DEVELOPER'S [DEFAULT] CHOICE (QA note, not an
assertion below — see the QA handoff for the full write-up): of the real
pull's 34 colliding address+unit groups, **7 have both disjoint chains
starting in the SAME cohort year** (e.g. `1821 w cullerton st|unit 1`: two
chains, both `cohort_year=2024`, DOMs 133 and 83). A disambiguator that
appends *only* `cohort_year` to the key is therefore NOT sufficient on real
data — `test_two_disjoint_chains_in_the_same_cohort_year_get_different_keys`
below is the minimal synthetic reproduction of exactly that case.

EXPECTED RED STATE TODAY: every test below FAILS (comp_key collides) with
`rentcomp.pipeline.shape` and `rentcomp.pipeline.derive` both already real
(WS-1 merged) — there is no import-error skip gate here.
"""

from __future__ import annotations

from datetime import date

from rentcomp.models.requests import DeriveRequest, Subject
from rentcomp.pipeline.derive import DeriveContext, derive
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

FULL_YEAR = ("01-01", "12-31")
AS_OF = date(2026, 7, 27)

SUBJECT = Subject(
    address="1234 W Fake St",
    lat=41.83,
    lng=-87.66,
    sqft=1000.0,
    beds=3.0,
    baths=2.0,
)


def _raw(
    *,
    id_: str,
    address: str,
    unit: str | None,
    listed: str,
    removed: str | None,
    price: float = 2000.0,
    sqft: float = 1000.0,
) -> dict:
    """One raw record, shaped like RentCast's real payload — the same helper
    convention as `test_ws1_record_shaping.py`."""
    return {
        "id": id_,
        "formattedAddress": f"{address}, Unit {unit}, Chicago, IL 60609" if unit else address,
        "addressLine1": address,
        "addressLine2": f"Unit {unit}" if unit else None,
        "latitude": 41.83,
        "longitude": -87.66,
        "bedrooms": 3,
        "bathrooms": 2,
        "squareFootage": sqft,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listedDate": f"{listed}T00:00:00.000Z",
        "removedDate": f"{removed}T00:00:00.000Z" if removed else None,
        "history": {},
    }


def _derive_comps(active: list[dict], inactive: list[dict]):
    comps = shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)
    ctx = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    req = DeriveRequest(pull_ref="unit-test", subject=SUBJECT)
    return derive(req, ctx).comps


def test_two_disjoint_chains_at_the_same_address_unit_get_different_keys() -> None:
    """The base case: two chains at the same address+unit, gap well past the
    42-day stitch threshold, DIFFERENT cohort years (2024 vs 2025) — the
    'easy' collision a bare cohort-year disambiguator would already fix."""
    active: list[dict] = []
    inactive = [
        _raw(id_="a", address="500 W Test St", unit="1", listed="2024-01-10", removed="2024-03-01"),
        _raw(id_="b", address="500 W Test St", unit="1", listed="2025-01-10", removed="2025-03-01"),
    ]
    comps = _derive_comps(active, inactive)
    assert len(comps) == 2, f"expected 2 disjoint chains (gap far past the 42d threshold), got {len(comps)}"
    assert comps[0].cohort_year != comps[1].cohort_year  # sanity on the fixture itself
    assert comps[0].key != comps[1].key, (
        f"two genuinely different vacancy chains at the same address+unit collapsed onto one "
        f"comp_key ({comps[0].key!r}) — toggling one's weight would silently move both"
    )


def test_two_disjoint_chains_in_the_same_cohort_year_get_different_keys() -> None:
    """The hard case, reproduced from the real data (see module docstring):
    two disjoint chains at the same address+unit, gap far past the 42-day
    stitch threshold, but BOTH starting in the SAME cohort year. A
    disambiguator that appends only `cohort_year` to the key would still
    collide here — this is a real case in `ws1-real`, not a contrived one."""
    active: list[dict] = []
    inactive = [
        # chain 1: Jan-Mar 2024
        _raw(id_="a", address="700 W Test Ave", unit="2", listed="2024-01-10", removed="2024-03-01"),
        # chain 2: starts 2024-07-01, well past 2024-03-01 + 42 days (2024-04-12) — a
        # separate chain by F4-S3's own stitch rule, and still cohort_year=2024.
        _raw(id_="b", address="700 W Test Ave", unit="2", listed="2024-07-01", removed="2024-09-01"),
    ]
    comps = _derive_comps(active, inactive)
    assert len(comps) == 2, f"expected 2 disjoint chains (gap far past the 42d threshold), got {len(comps)}"
    assert comps[0].cohort_year == comps[1].cohort_year == 2024, (
        "fixture setup error: both chains must land in the same cohort year to reproduce "
        f"the real-data case; got cohort years {comps[0].cohort_year, comps[1].cohort_year}"
    )
    assert comps[0].effective_dom != comps[1].effective_dom  # sanity: genuinely different chains
    assert comps[0].key != comps[1].key, (
        f"two genuinely different vacancy chains at the same address+unit AND the same cohort "
        f"year collapsed onto one comp_key ({comps[0].key!r}) — appending cohort_year alone is "
        "NOT sufficient to disambiguate this real case (see module docstring)"
    )


def test_a_single_chain_still_round_trips_to_one_key() -> None:
    """The disambiguation fix must not fragment a single, non-colliding
    chain into multiple identities — one address+unit with one continuous
    vacancy history is still exactly one comp."""
    active = [_raw(id_="a", address="900 W Test Blvd", unit="3", listed="2024-05-01", removed=None)]
    comps = _derive_comps(active, [])
    assert len(comps) == 1, f"expected exactly one chain, got {len(comps)}"
