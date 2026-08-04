"""F0-S2 Layer 1 — the derivation graph's stages, called directly.

Developer-authored companion to QA's `test_derivation_graph.py` (which owns
the structural guards, the type-level exclusivity, and the pinned signatures).
This file owns what QA's plan calls for but did not write: the behaviour of
each pure stage, and — the part that matters most for a story whose job is to
build seams rather than statistics — **the seam contracts around the stubs**.

WHY THE STUB TESTS ARE WRITTEN THE WAY THEY ARE
-----------------------------------------------
Every assertion about a stubbed stage is written so that it is *also* true of
the real implementation that will replace it. `select_neighbors` returning no
indices today and seven indices under F11-S1 both satisfy "the result is a set
of distinct, in-range indices, at most `k` of them". So these tests do not
have to be deleted when the stub goes; they become the regression net that
catches the replacement breaking the contract the rest of the graph relies on.

The one test that deliberately *does* track the stubs — the stub inventory —
is written as an equivalence, so it goes green again the moment the last stub
is removed and the pipeline version stops claiming otherwise.
"""

from __future__ import annotations

from datetime import date

import pytest

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.models.requests import DeriveRequest, Filters, Subject
from rentcomp.models.responses import Band
from rentcomp.pipeline.anchor import anchor
from rentcomp.pipeline.buckets import BUCKET_IDS, bucket_of, bucket_stats, premium_bounds
from rentcomp.pipeline.cohorts import cohort_medians, median_by_year
from rentcomp.pipeline.derive import (
    DRIFT_SENSITIVITY_PTS,
    PIPELINE_VERSION,
    DeriveContext,
    derive,
    drift_band,
)
from rentcomp.pipeline.keys import comp_key
from rentcomp.pipeline.membership import classify_membership, distances_mi, haversine_miles
from rentcomp.pipeline.premium import compute_premiums
from rentcomp.pipeline.pricetest import candidate_premium_band, price_test
from rentcomp.pipeline.weights import contribution_shares, effective_weights
from rentcomp.stats.knn import select_neighbors
from rentcomp.storage.config import Config

AS_OF = date(2026, 5, 4)
SUBJECT = Subject(address="1 Subject St", lat=41.9, lng=-87.68, sqft=1000.0, beds=2.0, baths=1.0)


def make_comp(
    address: str = "1200 W Fake St",
    unit: str | None = "1",
    *,
    sqft: float | None = 1000.0,
    ask: float = 2400.0,
    year: int = 2026,
    dom: int = 30,
    censored: bool = False,
    removal_class: str | None = "confirmed",
    lat: float = 41.9,
    lng: float = -87.68,
    withdrawal_suspect: bool = False,
    cuts: tuple[PriceCut, ...] = (),
) -> StitchedComp:
    return StitchedComp(
        address=address,
        unit=unit,
        lat=lat,
        lng=lng,
        beds=2.0,
        baths=1.0,
        sqft=sqft,
        initial_ask=ask,
        effective_dom=dom,
        censored=censored,
        removal_class=None if censored else removal_class,
        cohort_year=year,
        withdrawal_suspect=withdrawal_suspect,
        cut_history=cuts,
    )


# ---------------------------------------------------------------------------
# comp identity (F13-S1)
# ---------------------------------------------------------------------------


def test_comp_key_survives_case_and_whitespace_variation() -> None:
    """A key that changes when RentCast re-cases an address, or inserts a
    second space, cannot survive a refresh — and a curation state that cannot
    survive a refresh silently loses the user's work."""
    assert comp_key("1234 W Fake St", "2") == comp_key("\t1234   w  FAKE st \n", " 2 ")


def test_comp_key_distinguishes_units_and_a_missing_unit() -> None:
    assert comp_key("1234 W Fake St", "2") != comp_key("1234 W Fake St", "3")
    assert comp_key("1234 W Fake St", None) != comp_key("1234 W Fake St", "2")
    # "" and None are the same absence: RentCast omits addressLine2 either way.
    assert comp_key("1234 W Fake St", None) == comp_key("1234 W Fake St", "")


def test_comp_key_cannot_be_collided_by_concatenation() -> None:
    """Two different (address, unit) pairs must not produce one key — the
    classic "a|bc" vs "ab|c" failure of naive string joining."""
    assert comp_key("1234 W Fake St A", "B") != comp_key("1234 W Fake St", "A B")


# ---------------------------------------------------------------------------
# weights (F5-S2, ADR-001 §1.1)
# ---------------------------------------------------------------------------


def test_absent_comps_default_to_one_and_no_sqft_comps_to_zero() -> None:
    weights = effective_weights(["a", "b"], [True, False], {})
    assert weights == [1.0, 0.0]


def test_an_explicit_weight_always_wins_including_on_a_no_sqft_comp() -> None:
    """The user's stated intent is echoed rather than silently overridden. The
    comp still contributes nothing to a $/sqft statistic — it has no value to
    contribute — but the response tells the truth about what was asked for."""
    assert effective_weights(["a", "b"], [True, False], {"a": 0.0, "b": 3.0}) == [0.0, 3.0]


def test_weights_do_not_depend_on_the_clients_json_key_order() -> None:
    forward = effective_weights(["a", "b", "c"], [True] * 3, {"a": 1.0, "b": 2.0, "c": 3.0})
    backward = effective_weights(["a", "b", "c"], [True] * 3, {"c": 3.0, "b": 2.0, "a": 1.0})
    assert forward == backward == [1.0, 2.0, 3.0]


def test_contribution_shares_sum_to_one_over_the_included_set() -> None:
    shares = contribution_shares([1.0, 3.0, 5.0], [True, True, False])
    assert shares[2] is None, "a comp that is not contributing has an undefined share, not 0%"
    assert sum(share for share in shares if share is not None) == pytest.approx(1.0)
    assert shares[1] == pytest.approx(0.75)


def test_no_included_weight_means_no_shares_rather_than_a_division_by_zero() -> None:
    assert contribution_shares([0.0, 0.0], [True, True]) == [None, None]


# ---------------------------------------------------------------------------
# membership (F7-S1)
# ---------------------------------------------------------------------------


def test_every_comp_gets_exactly_one_label() -> None:
    comps = [
        make_comp("A", censored=True),
        make_comp("B"),
        make_comp("C"),
    ]
    keys = ["a", "b", "c"]
    states = classify_membership(
        comps,
        keys,
        [1.0, 0.0, 1.0],
        [0.1, 0.2, 9.0],
        Filters(max_distance_mi=1.0, hide_censored=True),
        [],
    )
    assert states == ["filtered", "excluded", "filtered"]
    assert len(states) == len(comps), "the partition must cover the pull exactly once"


def test_an_include_override_beats_every_filter() -> None:
    """F7-S1: a manual re-include survives a filter change, which is the whole
    point of it being a separate list rather than a weight."""
    comps = [make_comp("A", censored=True)]
    states = classify_membership(
        comps, ["a"], [1.0], [99.0], Filters(max_distance_mi=0.1, hide_censored=True), ["a"]
    )
    assert states == ["included"]


def test_leased_only_keeps_the_ladder_and_drops_censored_and_pending() -> None:
    """NORTH_STAR: a pending removal is too recent to trust and a censored comp
    has not been removed at all. Neither is reclassified — both are out of view."""
    comps = [
        make_comp("A", removal_class="confirmed"),
        make_comp("B", removal_class="provisional"),
        make_comp("C", removal_class="pending"),
        make_comp("D", censored=True),
    ]
    states = classify_membership(
        comps, list("abcd"), [1.0] * 4, [0.0] * 4, Filters(leased_only=True), []
    )
    assert states == ["included", "included", "filtered", "filtered"]


def test_distance_is_measured_from_the_subject_in_miles() -> None:
    # ~1 degree of latitude is ~69 miles; a hundredth of that is ~0.69 miles.
    assert haversine_miles(41.9, -87.68, 41.91, -87.68) == pytest.approx(0.69, abs=0.02)
    assert haversine_miles(41.9, -87.68, 41.9, -87.68) == 0.0
    assert distances_mi([make_comp(lat=41.9, lng=-87.68)], SUBJECT) == [0.0]


# ---------------------------------------------------------------------------
# cohorts and premium (F4-S4, F4-S5)
# ---------------------------------------------------------------------------


def test_a_cohort_median_is_taken_over_the_selected_set_only() -> None:
    """F4-S5 [INVARIANT] is why premium is a per-request stage: toggling a comp
    moves the median it is measured against.

    `min_cohort_size=2` so that **both** arms stay at or above the minimum.
    This test demonstrates the selected-set rule, and only that; a cohort below
    the minimum falls back to the pulled set (F4-S5's other half), at which
    point toggling `c` off would move nothing and this test would be quietly
    demonstrating the opposite of what it says. Written at `4` originally
    because the fallback did not exist yet — the `basis` assertions below are
    what keep the two arms honest now that it does, and
    `test_f4s5_cohort_fallback.py` covers the other side.
    """
    keys = ["a", "b", "c"]
    psfs = [2.0, 3.0, 100.0]
    years = [2026, 2026, 2026]
    all_in = cohort_medians(keys, psfs, years, [1.0] * 3, [True] * 3, 2)
    without_outlier = cohort_medians(keys, psfs, years, [1.0, 1.0, 0.0], [True, True, False], 2)
    assert all_in[0].basis == "selected" and without_outlier[0].basis == "selected"
    assert all_in[0].median_psf == 3.0  # lower weighted median of 2/3/100
    assert without_outlier[0].median_psf == 2.0
    assert without_outlier[0].comp_keys == ["a", "b"]


def test_a_comp_with_no_psf_is_not_cohort_evidence_but_is_not_lost() -> None:
    stats = cohort_medians(["a", "b"], [None, 2.0], [2026, 2026], [1.0, 1.0], [True, True], 4)
    assert stats[0].comp_keys == ["b"]
    assert stats[0].selected_count == len(stats[0].comp_keys) == 1
    assert stats[0].pulled_count == 1


def test_cohort_counts_always_equal_their_evidence_lists() -> None:
    stats = cohort_medians(
        ["a", "b", "c"], [1.0, 2.0, 3.0], [2025, 2026, 2026], [1.0, 1.0, 0.0], [True, True, False], 4
    )
    for stat in stats:
        assert stat.selected_count == len(stat.comp_keys)


def test_cohorts_are_emitted_in_a_stable_ascending_order() -> None:
    stats = cohort_medians(
        ["a", "b", "c"], [1.0] * 3, [2026, 2024, 2025], [1.0] * 3, [True] * 3, 4
    )
    assert [stat.year for stat in stats] == [2024, 2025, 2026]


def test_thin_is_a_direct_reading_of_the_min_cohort_size_knob() -> None:
    stats = cohort_medians(["a"], [2.0], [2026], [1.0], [True], 4)
    assert stats[0].thin is True
    assert cohort_medians(["a"], [2.0], [2026], [1.0], [True], 1)[0].thin is False


def test_a_cohort_with_no_evidence_has_no_median_and_no_basis() -> None:
    stats = cohort_medians(["a"], [None], [2026], [1.0], [True], 4)
    assert stats[0].median_psf is None and stats[0].basis is None
    assert median_by_year(stats) == {}, "a year with no median must not reach the premium stage"


def test_premium_is_a_ratio_against_the_comps_own_cohort() -> None:
    premiums = compute_premiums([2.2, 2.0, 3.0], [2026, 2026, 2025], {2026: 2.0, 2025: 2.0})
    assert premiums[0] == pytest.approx(0.10)
    assert premiums[1] == pytest.approx(0.0)
    assert premiums[2] == pytest.approx(0.50)


def test_premium_is_none_rather_than_zero_wherever_it_is_unknown() -> None:
    """A fabricated 0.0 reads as "priced exactly at market" — a claim about a
    comp we know nothing about."""
    assert compute_premiums([None], [2026], {2026: 2.0}) == [None]
    assert compute_premiums([2.0], [2026], {}) == [None], "no cohort median ⇒ no premium"
    assert compute_premiums([2.0], [2026], {2026: 0.0}) == [None], "never divide by a zero median"


def test_premium_rejects_mismatched_inputs_rather_than_silently_truncating() -> None:
    with pytest.raises(ValueError):
        compute_premiums([1.0, 2.0], [2026], {})


# ---------------------------------------------------------------------------
# drift band (F8-S2)
# ---------------------------------------------------------------------------


def test_the_drift_band_brackets_the_assumption_symmetrically() -> None:
    band = drift_band(7.0, DRIFT_SENSITIVITY_PTS)
    assert (band.low, band.mid, band.high) == (5.0, 7.0, 9.0)


def test_a_negative_drift_band_edge_is_legal() -> None:
    assert drift_band(0.0, 2.0).low == -2.0, "markets go down"


# ---------------------------------------------------------------------------
# buckets (F10-S1)
# ---------------------------------------------------------------------------


def test_bucket_boundaries_convert_the_knob_from_points_to_a_ratio_once() -> None:
    assert premium_bounds("below", 4.0) == (None, -0.04)
    assert premium_bounds("at", 4.0) == (-0.04, 0.04)
    assert premium_bounds("above", 4.0) == (0.04, None)


def test_bucket_membership_is_by_premium_and_the_edges_are_at_market() -> None:
    assert bucket_of(-0.05, 4.0) == "below"
    assert bucket_of(-0.04, 4.0) == "at"
    assert bucket_of(0.0, 4.0) == "at"
    assert bucket_of(0.04, 4.0) == "at"
    assert bucket_of(0.05, 4.0) == "above"


def test_a_comp_with_no_premium_is_in_no_bucket_rather_than_at_market() -> None:
    assert bucket_of(None, 4.0) is None


def test_bucket_counts_and_evidence_cannot_disagree() -> None:
    comps = [make_comp("A"), make_comp("B"), make_comp("C", censored=True, dom=44)]
    keys = list("abc")
    premiums = [-0.10, 0.0, 0.20]
    buckets = [bucket_of(premium, 4.0) for premium in premiums]
    stats = bucket_stats(comps, keys, premiums, [True] * 3, buckets, None, 4.0, 4)
    assert [stat.id for stat in stats] == list(BUCKET_IDS)
    for stat in stats:
        assert stat.count == len(stat.comp_keys)
    assert stats[2].censored_floors == [44], (
        "a censored comp's DOM-so-far is a floor and is listed as one — never mixed "
        "into a leased statistic"
    )


def test_an_excluded_comp_populates_no_bucket() -> None:
    stats = bucket_stats(
        [make_comp("A")], ["a"], [0.0], [False], ["at"], None, 4.0, 4
    )
    assert all(stat.count == 0 for stat in stats)


def test_bucket_dollar_boundaries_are_absent_without_an_anchor() -> None:
    stats = bucket_stats([], [], [], [], [], None, 4.0, 4)
    assert all(stat.dollar_min is None and stat.dollar_max is None for stat in stats)


def test_bucket_dollar_boundaries_track_the_anchor_band() -> None:
    """The premium definition is stable; the dollar figures are live, and they
    arrive as a band because the anchor is a band. This is the arithmetic the
    view is forbidden to do (D5)."""
    anchor_value = anchor(
        ["a"], [2.0], [2026], [1.0], [True], drift_band(7.0, DRIFT_SENSITIVITY_PTS), 1000.0, 2026
    )
    assert anchor_value is not None
    stats = bucket_stats([], [], [], [], [], anchor_value, 4.0, 4)
    at_bucket = next(stat for stat in stats if stat.id == "at")
    assert at_bucket.dollar_min is not None and at_bucket.dollar_max is not None
    assert at_bucket.dollar_min.mid == pytest.approx(anchor_value.rent.mid * 0.96)
    assert at_bucket.dollar_max.mid == pytest.approx(anchor_value.rent.mid * 1.04)


# ---------------------------------------------------------------------------
# STUB SEAMS — assertions true of the stub AND of the story that replaces it
# ---------------------------------------------------------------------------


def test_seam_neighbour_retrieval_returns_distinct_in_range_indices() -> None:
    """F11-S1 replaces the body. Whatever it returns must still be indices
    into the premium sequence it was handed, distinct, and at most `k` of
    them — the caller maps them back to comps and would otherwise IndexError
    or double-count a comp as evidence."""
    premiums = [-0.05, 0.0, 0.02, 0.11]
    for k in (1, 3, 7):
        indices = select_neighbors(premiums, 0.01, k)
        assert isinstance(indices, list)
        assert len(indices) <= k
        assert len(set(indices)) == len(indices)
        assert all(0 <= index < len(premiums) for index in indices)


def test_seam_retrieval_over_an_empty_pool_is_empty_not_an_error() -> None:
    assert select_neighbors([], 0.0, 7) == []


def test_seam_the_anchor_is_none_when_no_selected_comp_has_a_psf() -> None:
    """The evidence gate is real in F0-S2 and must survive F8-S1: "no
    evidence" is a state, not a zero."""
    drift = drift_band(7.0, DRIFT_SENSITIVITY_PTS)
    assert anchor(["a"], [None], [2026], [1.0], [True], drift, 1000.0, 2026) is None
    assert anchor(["a"], [2.0], [2026], [0.0], [True], drift, 1000.0, 2026) is None
    assert anchor(["a"], [2.0], [2026], [1.0], [False], drift, 1000.0, 2026) is None


def test_seam_the_anchor_cites_only_comps_that_could_have_produced_it() -> None:
    drift = drift_band(7.0, DRIFT_SENSITIVITY_PTS)
    value = anchor(
        ["a", "b", "c", "d"],
        [2.0, None, 3.0, 4.0],
        [2026] * 4,
        [1.0, 1.0, 0.0, 2.0],
        [True, True, True, False],
        drift,
        1000.0,
        2026,
    )
    assert value is not None
    assert value.comp_keys == ["a"], (
        "the anchor cited a comp with no $/sqft, a weight-0 comp, or a comp that is not "
        "in the analysis at all"
    )
    assert value.n_comps == len(value.comp_keys)


def test_seam_the_anchor_echoes_the_drift_it_was_asked_for() -> None:
    value = anchor(["a"], [2.0], [2026], [1.0], [True], drift_band(7.0, 2.0), 1234.0, 2026)
    assert value is not None
    assert value.drift_pct == 7.0
    assert value.drift_sensitivity_pts == pytest.approx(2.0)
    assert value.subject_sqft == 1234.0
    assert value.rent.mid == pytest.approx(value.psf.mid * 1234.0), (
        "anchor rent must be the anchor $/sqft times the subject's sqft"
    )


def test_seam_the_price_test_needs_both_a_candidate_and_an_anchor() -> None:
    drift = drift_band(7.0, DRIFT_SENSITIVITY_PTS)
    anchor_value = anchor(["a"], [2.0], [2026], [1.0], [True], drift, 1000.0, 2026)
    args = ([make_comp()], ["a"], [0.0], [1.0], [True], 7, 4.0, (14, 30, 45, 60))
    assert price_test(None, anchor_value, *args) is None
    assert price_test(2100.0, None, *args) is None, (
        "with no anchor there is no premium scale to express the candidate against; "
        "emitting a 0.0 premium band would fabricate a position for it"
    )


def test_seam_the_candidate_premium_is_a_band_over_the_anchor_band() -> None:
    """Owner ruling: the guard trips if it trips at ANY edge, so the candidate
    premium must reach the decision as a band. Survives F11-S3 unchanged."""
    anchor_value = anchor(
        ["a"], [2.0], [2026], [1.0], [True], drift_band(7.0, DRIFT_SENSITIVITY_PTS), 1000.0, 2026
    )
    assert anchor_value is not None
    band = candidate_premium_band(2100.0, anchor_value)
    assert isinstance(band, Band)
    for point, rent in ((band.low, anchor_value.rent.low), (band.mid, anchor_value.rent.mid)):
        assert point == pytest.approx(2100.0 / rent - 1.0)


def test_seam_the_price_test_result_only_cites_comps_that_are_in_the_analysis() -> None:
    comps = [make_comp("A"), make_comp("B", sqft=None)]
    anchor_value = anchor(
        ["a", "b"], [2.0, None], [2026, 2026], [1.0, 0.0], [True, False],
        drift_band(7.0, DRIFT_SENSITIVITY_PTS), 1000.0, 2026,
    )
    result = price_test(2100.0, anchor_value, comps, ["a", "b"], [0.0, None], [1.0, 0.0],
                        [True, False], 7, 4.0, (14, 30, 45, 60))
    assert result is not None
    assert {neighbor.key for neighbor in result.neighbors} <= {"a"}


def test_seam_the_price_test_guards_on_genuinely_thin_evidence() -> None:
    """F11-S1 replaced the stub retrieval with the real one: a single-comp
    pool now genuinely retrieves that one comp (real evidence, not an empty
    stub result), and the guard still trips because one neighbour is still
    fewer than F11-S3's usable-neighbour floor."""
    anchor_value = anchor(
        ["a"], [2.0], [2026], [1.0], [True], drift_band(7.0, DRIFT_SENSITIVITY_PTS), 1000.0, 2026
    )
    result = price_test(
        2100.0, anchor_value, [make_comp()], ["a"], [0.0], [1.0], [True], 7, 4.0, (14, 30, 45, 60)
    )
    assert result is not None
    assert result.state == "insufficient_evidence"
    assert result.reason == "too_few_in_range"
    assert {neighbor.key for neighbor in result.neighbors} == {"a"}


# ---------------------------------------------------------------------------
# the orchestrator
# ---------------------------------------------------------------------------


def make_request(**overrides) -> DeriveRequest:
    payload: dict = {
        "pull_ref": "unit-test",
        "subject": SUBJECT,
        "weights": {},
        "include_overrides": [],
        "filters": Filters(),
        "drift_pct": 7.0,
        "candidate_rent": None,
    }
    payload.update(overrides)
    return DeriveRequest(**payload)


def make_context(comps: tuple[StitchedComp, ...], config: Config | None = None) -> DeriveContext:
    return DeriveContext(
        config=config or Config(),
        comps=comps,
        as_of=AS_OF,
        pull_ref="unit-test",
        pull_digest="digest",
        config_digest="config-digest",
    )


PULL = (
    make_comp("1200 W Fake St", "1", sqft=1000.0, ask=2400.0, year=2026),
    make_comp("1200 W Fake St", "2", sqft=900.0, ask=2250.0, year=2026, censored=True, dom=47),
    make_comp("77 E Placeholder Ct", "2W", sqft=None, ask=2600.0, year=2026),
    make_comp("980 N Notional Rd", "1S", sqft=950.0, ask=2090.0, year=2025, lat=41.92),
)


def test_derive_is_a_pure_function_of_its_two_arguments() -> None:
    request, context = make_request(candidate_rent=2100.0), make_context(PULL)
    assert derive(request, context) == derive(request, context)


def test_derive_mutates_nothing_it_was_handed() -> None:
    """ADR-001 §4.3: a stage that cannot mutate an upstream artifact cannot
    leave a footprint for the next request."""
    request, context = make_request(weights={"1200 w fake st|1": 2.0}), make_context(PULL)
    before = (request.model_dump(), [comp.model_dump() for comp in PULL])
    derive(request, context)
    assert (request.model_dump(), [comp.model_dump() for comp in PULL]) == before


def test_derive_partitions_the_pull_however_it_is_curated() -> None:
    for request in (
        make_request(),
        make_request(weights={"1200 w fake st|1": 0.0}),
        make_request(filters=Filters(hide_censored=True, max_distance_mi=0.5)),
        make_request(filters=Filters(leased_only=True)),
    ):
        breakdown = derive(request, make_context(PULL)).breakdown
        assert breakdown.included + breakdown.excluded + breakdown.filtered == breakdown.pulled
        assert breakdown.pulled == len(PULL)


def test_derive_reports_the_as_of_it_was_given_and_nothing_else() -> None:
    """Owner ruling 1 at the orchestrator: `as_of` is data, arriving through
    exactly one argument, never the wall clock. Post-F8-S1, `as_of.year` is a
    DECLARED input to the anchor's drift-compounding term (`currentYear`,
    PM ruling) — so two contexts a calendar year apart are legitimately
    expected to differ beyond `meta.as_of` (covered by
    `test_ws1_anchor_drift.py`). What this test still protects: within the
    SAME year, nothing else in the payload moves — proof there is no
    OTHER, undeclared use of `as_of` (or the wall clock) anywhere below."""
    request = make_request()
    first = derive(request, make_context(PULL))
    later = derive(
        request,
        DeriveContext(
            config=Config(),
            comps=PULL,
            as_of=date(AS_OF.year, 12, 31),
            pull_ref="unit-test",
            pull_digest="digest",
            config_digest="config-digest",
        ),
    )
    assert first.meta.as_of == AS_OF
    assert later.meta.as_of == date(AS_OF.year, 12, 31)
    assert first.model_dump(exclude={"meta"}) == later.model_dump(exclude={"meta"})


def test_a_knob_change_moves_the_derivation() -> None:
    """F0-S5's load-bearing clause, at the graph rather than over HTTP: config
    reaches the math as a passed-in value, so changing it changes the output."""
    request = make_request()
    wide = derive(request, make_context(PULL, Config(bucket_half_width_pct=10.0)))
    narrow = derive(request, make_context(PULL, Config(bucket_half_width_pct=2.0)))
    assert [bucket.premium_min for bucket in wide.buckets] != [
        bucket.premium_min for bucket in narrow.buckets
    ]


def test_every_comp_key_cited_anywhere_belongs_to_a_comp_in_the_payload() -> None:
    state = derive(make_request(candidate_rent=2100.0), make_context(PULL))
    known = {comp.key for comp in state.comps}
    cited: set[str] = set()
    for cohort in state.cohorts:
        cited |= set(cohort.comp_keys)
    for bucket in state.buckets:
        cited |= set(bucket.comp_keys)
    if state.anchor:
        cited |= set(state.anchor.comp_keys)
    for keys in state.breakdown.comp_keys.values():
        cited |= set(keys)
    assert cited <= known


def test_a_warning_that_points_at_a_count_points_at_a_real_one() -> None:
    """Warnings click through to `breakdown.comp_keys` rather than carrying a
    second list of their own — two lists could disagree."""
    state = derive(make_request(), make_context(PULL))
    for warning in state.warnings:
        if warning.breakdown_ref is not None:
            assert warning.breakdown_ref in state.breakdown.comp_keys


def test_the_stub_inventory_and_the_pipeline_version_agree() -> None:
    """The self-cleaning half of the stub bookkeeping: while any stage is a
    placeholder, every payload says so *and* the pipeline version admits it.
    When the last stub goes and the version is bumped, this passes again with
    nothing to delete."""
    state = derive(make_request(), make_context(PULL))
    stubbed = [warning for warning in state.warnings if warning.code == "stub_stage"]
    assert bool(stubbed) == ("stubs" in PIPELINE_VERSION), (
        f"pipeline version {PIPELINE_VERSION!r} and {len(stubbed)} stub warning(s) disagree "
        "about whether this pipeline is still provisional"
    )


def test_the_missing_sqft_comp_is_none_all_the_way_down_but_still_counted() -> None:
    state = derive(make_request(), make_context(PULL))
    comp = next(comp for comp in state.comps if comp.sqft is None)
    assert (comp.psf, comp.premium, comp.bucket, comp.weight) == (None, None, None, 0.0)
    assert comp.key in state.breakdown.comp_keys["missing_sqft"]
    assert state.breakdown.missing_sqft == 1


def test_a_censored_comp_never_carries_a_removal_class() -> None:
    """The single most important distinction in the system (NORTH_STAR)."""
    state = derive(make_request(), make_context(PULL))
    for comp in state.comps:
        assert (comp.removal_class is None) == comp.censored
