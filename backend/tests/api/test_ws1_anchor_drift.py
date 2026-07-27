"""WS-1 — F8-S1 [INVARIANT] anchor computation, replacing `PLACEHOLDER_ANCHOR_PSF`.
QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol).

Tested over HTTP (L2), not by importing `pipeline.anchor.anchor()` directly,
DELIBERATELY: `anchor()`'s current F0-S2 signature has no way to express
"current year" (`cohort_years` and `drift` are received but unused by the
stub, per that module's own docstring), and the drift formula --
`adjPsf_i = initialPsf_i x (1 + drift)^(currentYear - cohortYear_i)` -- needs
one. **QA-PROPOSED RESOLUTION, flagged for PM confirmation in the handoff**:
"currentYear" = the pull's `as_of` year (`DeriveContext.as_of`, already data
rather than a wall-clock read, consistent with owner ruling 1) -- NOT
`datetime.date.today()`, which the architecture forbids inside `pipeline/`.
Whatever the dev's real signature ends up being internally, the endpoint's
observable behaviour is what these tests pin; a signature that reaches this
result some other way still passes.

Fixture: `fixtures/synthetic/pulls/ws1-anchor-drift.json` -- two comps, one
at the pull's own cohort year (2026) and one two years older (2024), with
distinct psf values so the compounding exponent is distinguishable from a
plain undrifted median.
"""

from __future__ import annotations

import math

import pytest

from rentcomp.pipeline.keys import comp_key
from rentcomp.stats.weighted import weighted_median

PULL_REF = "ws1-anchor-drift"
CURRENT_KEY = comp_key("10 W Current Cohort St", None)  # cohort_year=2026, psf=2.00
OLDER_KEY = comp_key("20 W Older Cohort Ave", None)  # cohort_year=2024, psf=1.00

TOL = 1e-6


@pytest.fixture
def anchor_body(make_body):
    def _body(**overrides):
        return make_body(pull_ref=PULL_REF, **overrides)

    return _body


def test_current_cohort_comp_is_unchanged_by_drift(derive, anchor_body) -> None:
    """F8-S1 AC1: "current-cohort comps are unchanged by drift." Exclude the
    older comp entirely; the sole survivor's cohort_year == the pull's own
    year, so the exponent is 0 and adjPsf == psf regardless of drift."""
    response = derive(anchor_body(weights={OLDER_KEY: 0.0}, drift_pct=25.0))
    assert response.status_code == 200, response.text
    payload = response.json()
    anchor = payload["anchor"]
    assert anchor is not None, "one positive-weight comp with a psf must produce an anchor"
    assert anchor["psf"]["mid"] == pytest.approx(2.0, abs=TOL), (
        f"a current-cohort comp's adjusted psf must equal its raw psf (2.00) at ANY drift "
        f"(here 25%); got {anchor['psf']['mid']}"
    )
    assert anchor["rent"]["mid"] == pytest.approx(2.0 * 1000.0, abs=TOL)


def test_drift_zero_mid_equals_the_plain_weighted_median(derive, anchor_body) -> None:
    """F8-S1 AC2: "unit test that anchor with d=0 equals plain weighted
    median." Both comps included, weighted 3:1 toward the CURRENT-cohort
    comp (2.00) deliberately: the equal-weight case's expected median
    (1.00) coincides with `PLACEHOLDER_ANCHOR_PSF`'s stub sentinel and
    would pass vacuously against unimplemented code (caught by mutation
    review of this file, see the handoff) -- this weighting makes the
    expected value (2.00) distinguishable from the stub's constant 1.00."""
    response = derive(anchor_body(weights={CURRENT_KEY: 3.0, OLDER_KEY: 1.0}, drift_pct=0.0))
    assert response.status_code == 200, response.text
    anchor = response.json()["anchor"]
    assert anchor is not None
    expected = weighted_median([2.0, 1.0], [3.0, 1.0])
    assert anchor["psf"]["mid"] == pytest.approx(expected, abs=TOL), (
        f"at drift=0 the mid psf must equal the plain weighted median {expected}, "
        f"got {anchor['psf']['mid']}"
    )


def test_drift_compounds_per_comp_by_cohort_age(derive, anchor_body) -> None:
    """The exponent is `currentYear - cohortYear_i`, PER COMP -- not a flat
    multiplier over the whole set. Weight the older comp to dominate (3x)
    so its adjusted value, not the current-cohort one, determines the
    (lower, weighted) median. At drift=10%, adjPsf_older =
    1.00 * (1.10)^2 = 1.21 -- distinguishable from both the raw 1.00 and
    from an (incorrect) flat-median-then-drift computation."""
    response = derive(anchor_body(weights={CURRENT_KEY: 1.0, OLDER_KEY: 3.0}, drift_pct=10.0))
    assert response.status_code == 200, response.text
    anchor = response.json()["anchor"]
    assert anchor is not None
    expected_adj_older = 1.00 * (1.10 ** 2)
    assert anchor["psf"]["mid"] == pytest.approx(expected_adj_older, rel=1e-6), (
        f"expected the dominant (weight-3) older comp's drift-compounded psf "
        f"{expected_adj_older:.4f} (1.00 x 1.10^2, exponent = currentYear(2026) - "
        f"cohortYear(2024) = 2); got {anchor['psf']['mid']}"
    )
    assert math.isclose(anchor["rent"]["mid"], expected_adj_older * 1000.0, rel_tol=1e-6)


def test_anchor_stub_sentinel_is_gone(derive, anchor_body) -> None:
    """F0-S2's `PLACEHOLDER_ANCHOR_PSF = 1.0` sentinel (deliberately
    unmistakable, per that module's docstring) must not survive WS-1 --
    this fixture's real psf values (2.00 / 1.00) were chosen so a lingering
    $1.00/sqft stub would be indistinguishable from the older comp's raw
    value; the drift-compounded check above is what actually catches it.
    This test additionally asserts the band is no longer degenerate."""
    response = derive(anchor_body(drift_pct=10.0))
    assert response.status_code == 200, response.text
    anchor = response.json()["anchor"]
    assert anchor is not None
    assert anchor["psf"]["low"] != anchor["psf"]["high"], (
        "the sensitivity band must no longer be degenerate (low == mid == high was the "
        "F0-S2 stub's second tell)"
    )
