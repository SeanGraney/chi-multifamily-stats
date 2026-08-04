"""Row 25a verify — pinning the exact `thin` boundary against a real,
now-decided implementation choice.

QA's original test-plan (`test_25a_bucket_thin_evidence_gate.py`) deliberately
avoided pinning an exact threshold value while `thin`'s source was still an
undecided `[DEFAULT]` (QUEUE.md row 25a Ruling 2: "reuse `min_cohort_size` or
introduce a dedicated constant — dev's call"). The dev's implementation
(`buckets.py`) now makes a SPECIFIC, checkable claim: `thin = 0 < leased_count
< min_cohort_size`, reusing the F4-S5 cohort-thinness knob with the same
comparison direction (strictly `<`, so `leased_count == min_cohort_size` is
AT the minimum, not below it — matching `cohorts.py`'s own boundary
language). That claim is now a real behavioural contract, not a free
implementation choice, so it earns a permanent pin — at Layer 1, since
`bucket_stats()` is directly importable and callable with plain values
(AGENT_QA.md decision procedure, item 1).

Verified independently by QA before writing this file (direct
`bucket_stats()` calls, not a re-read of the dev's account):
`leased_count=3` -> `thin=True`, `leased_count=4` -> `thin=False`, for
`min_cohort_size=4`. The `min_cohort_size` value itself is never hardcoded
here as a bare literal in the assertions below — the parametrization is
against a *variable* threshold, so a config change to the knob's default
does not silently invalidate this file; it stays a live test of the boundary
*relationship*, not one baked-in number.
"""

from __future__ import annotations

import pytest

from rentcomp.models.domain import StitchedComp
from rentcomp.pipeline.buckets import bucket_of, bucket_stats


def _leased_comp(dom: int) -> StitchedComp:
    return StitchedComp(
        address="1 test st",
        unit=None,
        lat=41.83,
        lng=-87.66,
        beds=3,
        baths=2.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=False,
        removal_class="confirmed",
        cohort_year=2026,
    )


def _at_bucket_for(n_leased: int, min_cohort_size: int):
    comps = [_leased_comp(10 + i) for i in range(n_leased)]
    keys = [f"key-{i}" for i in range(n_leased)]
    premiums: list[float | None] = [0.0] * n_leased  # every comp lands "at market"
    half_width_pct = 4.0
    buckets = [bucket_of(p, half_width_pct) for p in premiums]
    included = [True] * n_leased
    stats = bucket_stats(comps, keys, premiums, included, buckets, None, half_width_pct, min_cohort_size)
    return next(s for s in stats if s.id == "at")


@pytest.mark.parametrize("min_cohort_size", [2, 4, 7, 10])
class TestThinBoundaryAgainstMinCohortSize:
    """Exhaustive across the knob's own documented range (2-10,
    `storage/config.py`), not just its default — a boundary claim tied to a
    config value should hold at every value that value can take."""

    def test_exactly_min_cohort_size_leased_comps_is_not_thin(self, min_cohort_size: int) -> None:
        bucket = _at_bucket_for(min_cohort_size, min_cohort_size)
        assert bucket.leased_count == min_cohort_size
        assert bucket.thin is False, (
            f"a bucket with exactly leased_count == min_cohort_size ({min_cohort_size}) is "
            "AT the evidence minimum, not below it — `thin` must be False (strictly `<`, "
            "matching CohortStat.thin's own comparison direction in cohorts.py)"
        )

    def test_one_fewer_than_min_cohort_size_is_thin(self, min_cohort_size: int) -> None:
        if min_cohort_size - 1 <= 0:
            pytest.skip(f"min_cohort_size={min_cohort_size}: no room below it for a nonzero probe")
        bucket = _at_bucket_for(min_cohort_size - 1, min_cohort_size)
        assert bucket.leased_count == min_cohort_size - 1
        assert bucket.thin is True, (
            f"leased_count={min_cohort_size - 1} is strictly below min_cohort_size="
            f"{min_cohort_size} and must be flagged thin"
        )

    def test_one_more_than_min_cohort_size_is_not_thin(self, min_cohort_size: int) -> None:
        bucket = _at_bucket_for(min_cohort_size + 1, min_cohort_size)
        assert bucket.leased_count == min_cohort_size + 1
        assert bucket.thin is False


def test_zero_leased_comps_is_not_flagged_thin_it_is_the_pre_existing_empty_case() -> None:
    """The dev's deliberate carve-out (buckets.py: `0 < leased_count < ...`):
    an empty leased set already reports `None` on every statistic — it has
    NO evidence, which is a different, pre-existing condition from SPARSE
    evidence. Flagging it "thin" too would conflate the two (module
    docstring, PM Ruling A)."""
    bucket = _at_bucket_for(0, min_cohort_size=4)
    assert bucket.leased_count == 0
    assert bucket.leased_dom_median is None
    assert bucket.thin is False, (
        "an empty leased set must not be additionally flagged thin — it is the pre-existing "
        "'no evidence at all' case, not 'sparse evidence'"
    )
