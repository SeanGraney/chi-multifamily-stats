"""F11-S1 [BE] kNN retrieval — QA-authored, per `AGENT_QA.md`'s protocol.

BACKGROUND: `stats/knn.py::select_neighbors` and `pipeline/pricetest.py`
already exist and are already exercised by `test_ws1_knn_retrieval.py` (L1,
signature/source guards + tie-break determinism) and
`test_derivation_graph.py` / `test_derivation_graph_dev.py` (L1, structural
guards + the `price_test` seam contracts). This module does not repeat that
coverage — it closes three gaps the story's own `[INVARIANT]` text calls out
by name that the existing suite did not yet assert directly:

1. "neighbors returned with their premium distances" — every prior test that
   *uses* `Neighbor.distance` (e.g. the guard-reason honesty tests) never
   checks the distance is actually `|candidate_premium - comp.premium|`. A
   `Neighbor` that echoed a stale, zero, or outcome-derived number would pass
   every test written so far.
2. "User weights are carried into aggregation, never into distance/neighbor
   selection — the specific line a library swap must not cross." No existing
   test perturbs a weight and checks the retrieved SET is unaffected; the
   signature guard on `select_neighbors` catches a weights *parameter*
   appearing, but not a future implementation that reads weight through some
   other channel (e.g. a closure, a comp attribute) before selection.
3. "k default 7 ... test the k-limiting behavior itself" without hardcoding
   the default. `test_ws1_knn_retrieval.py` already proves this at
   `select_neighbors`' own layer; this module proves the same property one
   layer up, through `price_test`'s per-edge retrieval + union, which is
   where a future refactor could reintroduce an unbounded neighbor set even
   with `select_neighbors` itself still correct.

Layer: L1 (pytest unit). `price_test()` is directly importable and callable
with plain values — no HTTP, no assembled `DerivedState` — same as
`test_derivation_graph_dev.py`'s existing seam tests
(AGENT_QA.md decision procedure item 1).
"""

from __future__ import annotations

from datetime import date

import pytest

from rentcomp.models.domain import StitchedComp
from rentcomp.pipeline.anchor import anchor
from rentcomp.pipeline.derive import drift_band
from rentcomp.pipeline.pricetest import price_test

#: A single anchor comp, chosen so anchor_psf == 2.0 exactly and
#: subject_sqft == 1000.0 gives anchor_rent == 2000.0 for every drift edge —
#: `drift_band(0.0, 0.0)` collapses low == mid == high, so all three of
#: `price_test`'s per-edge retrievals share one candidate premium and this
#: module does not have to reason about the owner's band-of-three separately
#: from the retrieval invariants it is actually testing.
ZERO_DRIFT = drift_band(0.0, 0.0)
ANCHOR_VALUE = anchor(["anchor"], [2.0], [2026], [1.0], [True], ZERO_DRIFT, 1000.0, 2026)
assert ANCHOR_VALUE is not None and ANCHOR_VALUE.rent.mid == pytest.approx(2000.0)

#: candidate_rent=2100 against a 2000 anchor rent => candidate premium 0.05
#: at every edge — a deliberately non-zero, non-boundary value.
CANDIDATE_RENT = 2100.0

#: Ten comps at hand-picked premiums spanning the candidate, so the nearest-k
#: set is neither "everything" nor "the first index" by accident. Ties exist
#: on purpose at distance 0.03 (idx 4 & 6) and 0.05 (idx 3 & 7) but this
#: module does not assert a winner between them (that is
#: `test_ws1_knn_retrieval.py`'s job) — it only needs a real pool.
POOL_PREMIUMS = [-0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20]
POOL_KEYS = [f"comp-{i}" for i in range(len(POOL_PREMIUMS))]


def make_pool_comp(index: int, *, dom: int = 30, censored: bool = False) -> StitchedComp:
    return StitchedComp(
        address=f"{100 + index} W Pool St",
        unit=None,
        lat=41.9,
        lng=-87.68,
        beds=2.0,
        baths=1.0,
        sqft=1000.0,
        initial_ask=2000.0,
        effective_dom=dom,
        censored=censored,
        removal_class=None if censored else "confirmed",
        cohort_year=2026,
        first_listed=date(2026, 1, 1),
    )


POOL_COMPS = [make_pool_comp(i) for i in range(len(POOL_PREMIUMS))]


def run_price_test(weights: list[float], knn_k: int):
    result = price_test(
        CANDIDATE_RENT,
        ANCHOR_VALUE,
        POOL_COMPS,
        POOL_KEYS,
        POOL_PREMIUMS,
        weights,
        [True] * len(POOL_PREMIUMS),
        knn_k,
        4.0,
        (14, 30, 45, 60),
    )
    assert result is not None
    return result


# ---------------------------------------------------------------------------
# Gap 1 — Neighbor.distance is genuinely |candidate_premium - comp.premium|
# ---------------------------------------------------------------------------


def test_every_returned_neighbors_distance_is_the_absolute_premium_gap_from_the_candidate() -> None:
    result = run_price_test(weights=[1.0] * len(POOL_PREMIUMS), knn_k=3)

    # The mid-edge candidate premium this test's own fixed anchor/candidate
    # produce — read off the response rather than hardcoded, so a change to
    # the anchor formula (F8-S1) cannot silently make this assertion vacuous.
    mid_candidate_premium = result.candidate_premium.mid
    assert mid_candidate_premium == pytest.approx(0.05)

    premium_by_key = dict(zip(POOL_KEYS, POOL_PREMIUMS, strict=True))
    assert result.neighbors, "the pool and k are both non-empty; there must be neighbors to check"
    for neighbor in result.neighbors:
        expected_distance = abs(mid_candidate_premium - premium_by_key[neighbor.key])
        assert neighbor.distance == pytest.approx(expected_distance), (
            f"neighbor {neighbor.key!r} reports distance={neighbor.distance!r}, but "
            f"|{mid_candidate_premium} - {premium_by_key[neighbor.key]}| = {expected_distance!r} "
            "— F11-S1's AC requires neighbors carry their real premium distance for display "
            "and for the guard (F11-S3), not a placeholder or stale value"
        )
        assert neighbor.premium == pytest.approx(premium_by_key[neighbor.key]), (
            "neighbor.premium does not match the pool premium it was retrieved from"
        )


# ---------------------------------------------------------------------------
# Gap 2 — weights never enter distance/selection, only aggregation
# ---------------------------------------------------------------------------


def test_changing_a_neighbors_weight_never_changes_which_neighbors_are_retrieved() -> None:
    """The exact invariant named in the AC: 'the specific line a library swap
    must not cross.' A `KNeighborsRegressor`-style implementation that let a
    `weights` array influence distance would change WHICH indices are nearest
    once one comp's weight dwarfs the others — this asserts that does not
    happen, for a comp inside the k-set AND one outside it."""
    baseline_weights = [1.0] * len(POOL_PREMIUMS)
    baseline = run_price_test(weights=baseline_weights, knn_k=3)
    baseline_keys = {n.key for n in baseline.neighbors}

    heavied_weights = list(baseline_weights)
    heavied_weights[1] = 50.0  # idx 1: premium -0.05, distance 0.10 — outside k=3's set
    heavied_weights[4] = 0.1  # idx 4: premium 0.02, distance 0.03 — inside k=3's set
    heavied = run_price_test(weights=heavied_weights, knn_k=3)
    heavied_keys = {n.key for n in heavied.neighbors}

    assert heavied_keys == baseline_keys, (
        f"changing comp weights (never touching premiums) changed the retrieved neighbor set: "
        f"baseline={sorted(baseline_keys)} heavied={sorted(heavied_keys)} — weights must be "
        "aggregation-only (D19a's companion invariant)"
    )

    baseline_distance_by_key = {n.key: n.distance for n in baseline.neighbors}
    for neighbor in heavied.neighbors:
        assert neighbor.distance == pytest.approx(baseline_distance_by_key[neighbor.key]), (
            f"neighbor {neighbor.key!r}'s distance changed when only weights changed"
        )

    # The other half of the invariant: weights DO reach the response (for
    # aggregation/display) — a `weight` field that never changed at all would
    # mean weights were dropped entirely rather than merely kept out of
    # selection, which is a different (and also real) defect.
    heavied_weight_by_key = {n.key: n.weight for n in heavied.neighbors}
    assert heavied_weight_by_key[POOL_KEYS[4]] == pytest.approx(0.1)
    if POOL_KEYS[1] in heavied_weight_by_key:  # only present if idx1 ever entered a k-set
        assert heavied_weight_by_key[POOL_KEYS[1]] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Gap 3 — k bounds the neighbor set one layer above `select_neighbors` itself
# ---------------------------------------------------------------------------


def test_a_smaller_k_returns_a_subset_of_a_larger_ks_neighbors() -> None:
    """Not pinned to the F0-S5 default of 7 (a [DEFAULT], not this AC's to
    assert) — two arbitrary, distinct values instead, proving the config knob
    itself changes `price_test`'s retrieved set through the union-of-edges
    plumbing, not just through `select_neighbors` in isolation."""
    weights = [1.0] * len(POOL_PREMIUMS)
    small = run_price_test(weights=weights, knn_k=3)
    large = run_price_test(weights=weights, knn_k=10)

    small_keys = {n.key for n in small.neighbors}
    large_keys = {n.key for n in large.neighbors}

    assert len(small_keys) == 3, f"knn_k=3 over a 10-comp pool should retrieve exactly 3, got {small_keys}"
    assert len(large_keys) == 10, f"knn_k=10 over a 10-comp pool should retrieve all 10, got {large_keys}"
    assert small_keys <= large_keys, (
        "the k=3 neighbor set is not a subset of the k=10 set — increasing k must only ever "
        "add neighbors, never swap the nearer ones out for farther ones"
    )
