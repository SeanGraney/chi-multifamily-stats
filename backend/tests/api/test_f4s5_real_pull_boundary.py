"""F4-S5 — the min-cohort boundary on the REAL committed pull.

QA-authored, written RED before any implementation exists.

This is a supplement to `test_f4s5_premium_basis.py`, never a substitute for
it: the synthetic fixture is what walks the boundary deliberately (below / at
/ above 4), because F4-S8 established that coverage *inherited* from whatever a
real pull happens to contain is worth nothing — the shape has to be able to
see the off-by-one, and usually it cannot.

Measured here before writing anything (2026-07-29), so this file's dependence
on real data is a fact rather than a hope. `ws1-real` — the two committed T-S3
gate responses, shaped over the full calendar year — holds:

    cohort 2023 : 6 comps, of which exactly **4 carry a $/sqft**
    cohort 2024 : 148 comps, 123 with a $/sqft
    cohort 2025 : 255 comps, 215 with a $/sqft
    cohort 2026 : 158 comps, 142 with a $/sqft

So the real evidence sits **exactly on** `min_cohort_size` (4) in one cohort
and nowhere near it in the other three. Two things follow, and both are tested
below:

1. The default derive of the real pull is the boundary's *at* arm. Excluding
   one 2023 comp is the entire distance between "selected" and "pulled".
2. The cohort is at the boundary only because the two missing-sqft comps in it
   are **not** evidence for a $/sqft median. Count them and the cohort looks 6
   strong and the fallback never fires on real data at all — which is the
   quiet failure mode of obligation 5 ("excluded from every median"), visible
   here and nowhere else.

The comp excluded in the third test is `1759 W 21st St Apt 3F` — the same
listing that is the *whole* of cohort 2023 under a June window (QUEUE row
11a). Nothing here asserts anything about that; it is noted because it is the
comp a reader will recognise.
"""

from __future__ import annotations

import pytest

from rentcomp.pipeline.keys import comp_key

PULL_REF = "ws1-real"

#: The four cohort-2023 comps that carry a $/sqft, and their measured values.
PSF_1759_W_21ST = 1700.0 / 1100.0
PSF_939_W_34TH = 1.85
PSF_2037_W_23RD = 2.3
PSF_2039_W_23RD = 2.3

KEY_1759_W_21ST = comp_key("1759 W 21st St", "Apt 3F")
KEY_939_W_34TH = comp_key("939 W 34th St", None)

#: The two cohort-2023 comps with no `squareFootage`.
NO_SQFT_2023 = {comp_key("2706 S Wallace St", "Fl 2"), comp_key("2734 S Bonfield St", "2")}

#: lower weighted median of {1.545…, 1.85, 2.3, 2.3} — all four pulled comps.
PULLED_MEDIAN_2023 = PSF_939_W_34TH
#: lower weighted median of {1.85, 2.3, 2.3} — the three left after excluding
#: the cheapest. Deliberately different from the pulled median, which is what
#: makes the third test decisive.
SELECTED_ONLY_MEDIAN_2023 = PSF_2037_W_23RD

TOL = 1e-9


@pytest.fixture
def real(derive, make_body):
    def _derive(**overrides):
        response = derive(make_body(pull_ref=PULL_REF, **overrides))
        assert response.status_code == 200, (
            f"POST /api/derive against the real pull returned {response.status_code}: "
            f"{response.text[:400]}"
        )
        return response.json()

    return _derive


def cohort_2023(derived: dict) -> dict:
    for stat in derived["cohorts"]:
        if stat["year"] == 2023:
            return stat
    raise AssertionError(
        f"the real pull must hold a 2023 cohort; got {[c['year'] for c in derived['cohorts']]}"
    )


def test_the_real_pulls_2023_cohort_sits_exactly_on_the_minimum(real) -> None:
    """The census this file rests on, asserted rather than assumed.

    A failing precondition, not a skip: if the committed fixtures or the
    shaping chain ever move this cohort off the boundary, the two tests below
    stop meaning what they say, and that must be loud.
    """
    derived = real()
    stat = cohort_2023(derived)
    assert stat["pulled_count"] == 4, (
        f"cohort 2023 must hold exactly four comps with a $/sqft (six comps, two with no "
        f"squareFootage); got pulled_count={stat['pulled_count']}. If this is 6, the "
        f"missing-sqft comps are being counted as evidence for a $/sqft median — "
        f"obligation 5 of F4-S5, and the reason this cohort is on the boundary at all"
    )
    assert stat["selected_count"] == 4
    keys_2023 = {row["key"] for row in derived["comps"] if row["cohort_year"] == 2023}
    assert NO_SQFT_2023 <= keys_2023, (
        f"expected the two no-sqft 2023 comps to still be in the payload: {NO_SQFT_2023}"
    )
    assert not (NO_SQFT_2023 & set(stat["comp_keys"])), (
        "a comp with no $/sqft is not in the cohort's evidence list"
    )
    assert NO_SQFT_2023 <= set(derived["breakdown"]["comp_keys"]["missing_sqft"])


def test_the_real_pull_at_the_boundary_uses_the_selected_median(real) -> None:
    """Four selected == min_cohort_size, so no fallback (the "at" arm, on real
    evidence). The median is 1.85 either way here, so the *flag* is the
    assertion — which is exactly what a `<=` off-by-one would get wrong while
    every number on screen stayed correct."""
    stat = cohort_2023(real())
    assert stat["thin"] is False
    assert stat["basis"] == "selected", (
        f"4 selected is not below min_cohort_size 4; basis={stat['basis']}"
    )
    assert stat["median_psf"] == pytest.approx(PULLED_MEDIAN_2023, abs=TOL)


def test_excluding_one_real_comp_drops_the_cohort_onto_the_pulled_median(real) -> None:
    """One toggle is the entire distance between the two bases on real data.

    Excluding `1759 W 21st St Apt 3F` leaves three selected comps whose own
    median is 2.30; the fallback's median over all four pulled comps is 1.85.
    A selected-only computation and a fallback computation therefore give
    visibly different premiums for every comp in the cohort, including the
    excluded one — whose premium must still be measured against the pulled
    median, since an excluded comp keeps its row and its number.
    """
    derived = real(weights={KEY_1759_W_21ST: 0.0})
    stat = cohort_2023(derived)
    assert stat["selected_count"] == 3, (
        f"precondition: one exclusion must leave three selected; got {stat['selected_count']}"
    )
    assert stat["thin"] is True
    assert stat["basis"] == "pulled"
    assert stat["median_psf"] == pytest.approx(PULLED_MEDIAN_2023, abs=TOL), (
        f"the fallback median is over all four PULLED comps -> {PULLED_MEDIAN_2023}; got "
        f"{stat['median_psf']} ({SELECTED_ONLY_MEDIAN_2023} means the three selected comps "
        f"were used and the fallback never fired)"
    )

    rows = {row["key"]: row for row in derived["comps"] if row["cohort_year"] == 2023}
    excluded = rows[KEY_1759_W_21ST]
    assert excluded["state"] == "excluded"
    assert excluded["premium"] == pytest.approx(
        PSF_1759_W_21ST / PULLED_MEDIAN_2023 - 1.0, abs=TOL
    ), (
        "an excluded comp keeps its row, so it keeps a premium -- and that premium is "
        "measured against the same fallback median as every other comp in its cohort"
    )
    assert excluded["premium_basis"] == "pulled"
    assert rows[KEY_939_W_34TH]["premium"] == pytest.approx(0.0, abs=TOL), (
        "the comp whose own $/sqft IS the pulled median has premium 0 by definition"
    )
