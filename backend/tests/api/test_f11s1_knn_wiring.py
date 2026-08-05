"""F11-S1 [BE] kNN retrieval — Layer 2 wiring, QA-authored.

`test_f11s1_retrieval_invariants.py` (L1) already proves, exhaustively and
with a controlled pool that holds `premiums` fixed, that (1)
`Neighbor.distance` is genuinely `|candidate_premium - comp.premium|`, (2) a
neighbor's weight cannot change which neighbors are retrieved when its
premium is unchanged, and (3) `knn_k` bounds the retrieved set through
`price_test`'s own plumbing. This module adds the ONE wiring assertion that
is genuinely only provable through the real endpoint (per AGENT_QA.md's
split rule: exhaustive at the lower layer, once at the higher one) —
`knn_k` reaching retrieval all the way from the persisted config file.

QA FINDING, NOT PURSUED FURTHER HERE — flagged to the PM: an equivalent
"weight never changes the retrieved set" assertion does **not** hold over
the real endpoint, and should not be written there. `POST /api/derive`
recomputes premium per request as `psf / cohort_weighted_median(selected) -
1` (F4-S5 [INVARIANT]) — the cohort median is itself weighted over the
*selected* set, so editing one comp's weight can legitimately move every
comp's premium in its cohort, which then legitimately moves who is nearest.
That is F4-S5's own weighted-median invariant operating correctly, not a
D19a violation — D19a is about `select_neighbors`/`price_test` consuming
`weight` directly, which is what the L1 module isolates by holding
`premiums` constant while only `weight` varies. An L2 version of that same
assertion would either need to hand-pick a fixture-specific weight delta
small enough never to move a real weighted median (fragile, coupled to
`ws1-real`'s exact comp counts, and exactly the "fixture-luck" AGENT_QA.md
warns against) or risk a false failure like the one this file's earlier
draft produced. The L1 coverage is treated as sufficient for this AC.
"""

from __future__ import annotations

import json


def test_knn_k_config_change_flows_through_to_the_returned_neighbor_set(
    derive, rentcomp_home, clear_caches
) -> None:
    """F0-S5's `knn_k` knob must reach retrieval, not stop at
    `meta.config` (already covered by
    `test_derive_idempotence.py::test_a_knob_change_re_derives_like_any_other_input`).
    Asserted once, over the real endpoint and real data, at two values within
    the knob's own §2.3 range (3-15) — never the [DEFAULT] 7, which this
    story does not own."""
    anchor_response = derive(pull_ref="ws1-real", candidate_rent=None)
    assert anchor_response.status_code == 200, anchor_response.text[:600]
    anchor = anchor_response.json()["anchor"]
    assert anchor is not None
    at_market_rent = anchor["rent"]["mid"]

    counts: dict[int, int] = {}
    for k in (3, 15):
        (rentcomp_home / "config.json").write_text(json.dumps({"knn_k": k}), encoding="utf-8")
        clear_caches()
        response = derive(pull_ref="ws1-real", candidate_rent=at_market_rent)
        assert response.status_code == 200, response.text[:600]
        price_test = response.json()["price_test"]
        assert price_test is not None
        counts[k] = len(price_test["neighbors"])

    assert counts[15] > counts[3], (
        f"increasing the persisted knn_k config from 3 to 15 did not increase the retrieved "
        f"neighbor count at the pull's own anchor rent (${at_market_rent}): got {counts} — the "
        "config knob is not reaching pipeline.pricetest's retrieval"
    )
