"""F5-S2 — selection & weight state, Layer 2 (the derived-state half).

QA-authored, written before the developer's branch exists (AGENT_QA.md
protocol step 3). Every assertion here goes through ``POST /api/derive`` with
``TestClient`` — no pipeline internal is imported, so these keep holding when
the stages behind the endpoint change.

WHY THIS FILE IS LAYER 2 AND NOT PLAYWRIGHT
-------------------------------------------
F5-S2's AC opens with *"toggling and setting weight 0 produce identical
derived state."*  "Derived state" is a ``DerivedState`` field comparison, so
the decision procedure (AGENT_QA.md, step 2) stops at Layer 2 for the whole
server-side half of the story:

* weight 0 removing a comp from every aggregate,
* the ``included | excluded | filtered`` partition,
* the contribution-share formula across a dozen weight combinations,
* the counts F5-S3's ANALYSIS gate reads.

A browser proves none of that better; it only proves it slower.  What is left
for ``e2e/tests/f5-s2-selection-weight.spec.ts`` is exactly the part a browser
is the only witness to: that the *toggle control* and the *weight control*
write to one piece of React state, that ALL/NONE act over the rendered set,
that the contribution number on screen is the server's and not a division
done in TypeScript, and that above ~40% it turns a warning colour.

WHAT IS DELIBERATELY *NOT* HERE (already covered — do not duplicate)
-------------------------------------------------------------------
``test_derive_contract.py`` (F0-S2, QA-authored) already pins: a ``selections``
field is a 422 (no second source of truth is even expressible),
``contribution_share`` exists / is named ``_share`` / is a ratio in [0, 1],
weight-0 is ``excluded`` and drops out of ``anchor.comp_keys``, absent comps
default to 1.0 (0.0 with no sqft), and negative weights are a 422.  This file
starts where that one stops: the *formula*, the *whole* aggregate set, and the
partition as a property rather than a single spot check.

INVARIANT vs DEFAULT (SEMANTIC_CHANGE_PROTOCOL)
-----------------------------------------------
Two things in this story are ``[INVARIANT]`` and are what this file tests:

1. **One source of truth: the weight.**  Tested as an outcome — "there is no
   state in which a comp's label and its weight disagree" — never as "the
   request model has ``extra='forbid'``" (that is the technique, and it is
   already pinned elsewhere).
2. **contribution share == weight / Σ weights of the included comps.**  Tested
   as the arithmetic identity over many weight vectors, not by asserting which
   function computes it.

Nothing here pins a ``[DEFAULT]``: the membership-precedence rule
(``pipeline/membership.py``'s "filtered beats weight-0"), the haversine
distance, and the defaulting constants are all explicitly swappable, and the
tests below are written to survive a swap of each — see
``test_a_filtered_comp_keeps_the_weight_the_client_sent`` in particular, which
asserts the *weight* survives filtering and says nothing about the *label*.

Fixture mode only (WORKFLOW.md §6): ``RENTCOMP_LIVE`` is never set, the pull
is the committed ``fixtures/synthetic/pulls/synthetic-basic.json``, and the
root conftest's autouse socket kill switch makes a live call impossible.
"""

from __future__ import annotations

from typing import Any

import pytest

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def comps_by_key(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {comp["key"]: comp for comp in payload["comps"]}


def sqft_bearing_keys(payload: dict[str, Any]) -> list[str]:
    """Keys of comps that have a $/sqft, in payload order.

    These are the comps the anchor can actually be computed from, so they are
    the ones whose inclusion/exclusion is observable in every aggregate.
    """
    return [comp["key"] for comp in payload["comps"] if comp["psf"] is not None]


def evidence_lists(payload: dict[str, Any]) -> dict[str, list[str]]:
    """Every "these are the comps this number came from" list in the payload.

    ADR-001 §1.2 makes the evidence-first invariant a schema property: an
    aggregate that cannot name its comps does not typecheck.  That is what
    lets "weight 0 leaves the analysis" be asserted *completely* rather than
    against the one or two aggregates a test author happened to remember.
    """
    lists: dict[str, list[str]] = {}
    anchor = payload.get("anchor")
    if anchor:
        lists["anchor"] = anchor["comp_keys"]
    for cohort in payload["cohorts"]:
        lists[f"cohort[{cohort['year']}]"] = cohort["comp_keys"]
    for bucket in payload["buckets"]:
        lists[f"bucket[{bucket['id']}]"] = bucket["comp_keys"]
    price_test = payload.get("price_test")
    if price_test:
        lists["price_test.neighbors"] = [n["key"] for n in price_test["neighbors"]]
    return lists


def included_keys(payload: dict[str, Any]) -> set[str]:
    return {c["key"] for c in payload["comps"] if c["state"] == "included"}


# --------------------------------------------------------------------------
# Guard — one loud, ungated failure that explains an empty red state
# --------------------------------------------------------------------------


def test_the_fixture_pull_can_express_this_story(derived) -> None:
    """The pull must contain the shapes every test below needs.

    Without this, an inadequate fixture would make half this file vacuously
    green rather than red: "no comp had a $/sqft, so the loop asserted
    nothing" reads exactly like a pass.
    """
    payload = derived
    with_psf = sqft_bearing_keys(payload)
    assert len(with_psf) >= 4, (
        f"the fixture pull has {len(with_psf)} comps with a $/sqft; this story needs at "
        "least 4 to distinguish 'this comp left the analysis' from 'the analysis is empty'"
    )
    assert any(c["psf"] is None for c in payload["comps"]), (
        "the fixture pull has no missing-sqft comp — the 'excluded by default, manual "
        "re-include allowed' edge (F5 epic) cannot be tested"
    )
    assert payload["breakdown"]["included"] >= 4, (
        "fewer than 4 comps start included; the ANALYSIS-gate count test below cannot "
        "cross the 5-comp threshold in either direction"
    )


# --------------------------------------------------------------------------
# AC1 (server half) — toggle-off IS weight 0: one source of truth
# --------------------------------------------------------------------------


def test_weight_zero_removes_a_comp_from_every_aggregate(derive, derived) -> None:
    """[INVARIANT] weight 0 ≡ excluded, and "excluded" means gone from the math.

    ``test_derive_contract.py`` checks the anchor.  This checks *every*
    evidence list in the payload, which is the claim the AC actually makes:
    toggling a comp off and setting its weight to 0 land in the same place
    because there is only one place to land.
    """
    target = sqft_bearing_keys(derived)[0]
    assert target in {k for keys in evidence_lists(derived).values() for k in keys}, (
        f"{target!r} cites no aggregate even at weight 1 — pick a different fixture comp; "
        "this test cannot observe a removal that was never a presence"
    )

    zeroed = derive(weights={target: 0.0}).json()
    comp = comps_by_key(zeroed)[target]

    assert comp["state"] == "excluded"
    assert comp["weight"] == 0.0
    assert comp["contribution_share"] is None, (
        "a weight-0 comp reported a contribution share — a share of an analysis a comp is "
        "not part of is undefined, not 0%"
    )

    still_cited = {
        name: keys for name, keys in evidence_lists(zeroed).items() if target in keys
    }
    assert not still_cited, (
        f"comp {target!r} is at weight 0 but is still cited as evidence by "
        f"{sorted(still_cited)} — 'toggle off' and 'weight 0' would then NOT produce "
        "identical derived state, because one of them left a footprint in the math"
    )

    breakdown = zeroed["breakdown"]
    assert target in breakdown["comp_keys"]["excluded"], (
        "a weight-0 comp is not click-through-reachable from the excluded count"
    )
    assert target not in breakdown["comp_keys"]["included"]


@pytest.mark.parametrize(
    "weights_recipe",
    [
        pytest.param("all_one", id="uniform"),
        pytest.param("half_zero", id="half-excluded"),
        pytest.param("one_dominant", id="one-dominant"),
        pytest.param("fractional", id="fractional-weights"),
        pytest.param("all_zero", id="none-selected"),
    ],
)
def test_a_comps_label_and_its_weight_never_disagree(derive, derived, weights_recipe) -> None:
    """[INVARIANT] the partition property: for every comp that a filter has not
    taken, ``state == "included"`` ⟺ ``weight > 0``.

    This is the "single source of truth" invariant stated as an outcome.  If a
    second selection channel ever appears — a ``selections`` set, a client-side
    "hidden" list, a stale memo — it shows up here as a comp labelled included
    at weight 0 or excluded at weight 2, in at least one of these five shapes.

    No filters are set in any case, so ``filtered`` should not occur at all;
    that is asserted rather than assumed.
    """
    keys = [c["key"] for c in derived["comps"]]
    recipes = {
        "all_one": {k: 1.0 for k in keys},
        "half_zero": {k: (0.0 if i % 2 else 1.0) for i, k in enumerate(keys)},
        "one_dominant": {k: (9.0 if i == 0 else 1.0) for i, k in enumerate(keys)},
        "fractional": {k: round(0.25 * (i + 1), 2) for i, k in enumerate(keys)},
        "all_zero": {k: 0.0 for k in keys},
    }
    payload = derive(weights=recipes[weights_recipe]).json()

    for comp in payload["comps"]:
        assert comp["state"] != "filtered", (
            f"comp {comp['key']!r} is labelled 'filtered' with no filter set"
        )
        expected = "included" if comp["weight"] > 0.0 else "excluded"
        assert comp["state"] == expected, (
            f"comp {comp['key']!r} has weight {comp['weight']!r} but state "
            f"{comp['state']!r} — the weight is the single source of truth for selection "
            "(F5-S2 [INVARIANT]); a label that can disagree with it IS a second source"
        )


def test_selecting_nothing_is_an_honest_empty_state_not_a_zero(derive, derived) -> None:
    """The NONE end state.  Every share undefined, no anchor invented, no NaN.

    NORTH_STAR: a statistic with no evidence behind it is not 0 — it is absent.
    """
    keys = [c["key"] for c in derived["comps"]]
    payload = derive(weights={k: 0.0 for k in keys}).json()

    assert payload["anchor"] is None, (
        "an anchor was produced with zero selected comps — it would be a number with no "
        "evidence behind it"
    )
    assert payload["breakdown"]["included"] == 0
    assert all(c["contribution_share"] is None for c in payload["comps"]), (
        "a contribution share survived with nothing selected: "
        f"{[c['contribution_share'] for c in payload['comps']]}"
    )
    for comp in payload["comps"]:
        for field in ("psf", "premium"):
            value = comp[field]
            assert value is None or value == value, (  # noqa: PLR0124 - NaN check
                f"comp {comp['key']!r} has {field}=NaN with nothing selected"
            )


# --------------------------------------------------------------------------
# The contribution-% formula — [INVARIANT], and the reason D5 exists
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "recipe",
    ["uniform", "one_dominant", "fractional", "half_zero", "single_survivor"],
)
def test_contribution_share_is_weight_over_the_sum_of_included_weights(
    derive, derived, recipe
) -> None:
    """[INVARIANT] the formula, exhaustively — L2's half of the split rule.

    The browser spec asserts *once* that the number on screen is this number.
    Here it is checked across five weight shapes, because a dozen HTTP round
    trips cost milliseconds and a dozen browser sessions cost the suite's
    credibility (AGENT_QA.md, "the urge to parametrize in Playwright").

    Note what the denominator is: Σ over the comps the server labelled
    ``included``.  A filtered or weight-0 comp is not in it — NORTH_STAR
    defines the share against the *selected* set.
    """
    keys = sqft_bearing_keys(derived)
    recipes = {
        "uniform": {k: 1.0 for k in keys},
        "one_dominant": {k: (12.0 if i == 0 else 1.0) for i, k in enumerate(keys)},
        "fractional": {k: round(0.3 + 0.17 * i, 3) for i, k in enumerate(keys)},
        "half_zero": {k: (0.0 if i % 2 else 2.0) for i, k in enumerate(keys)},
        "single_survivor": {k: (1.0 if i == 0 else 0.0) for i, k in enumerate(keys)},
    }
    payload = derive(weights=recipes[recipe]).json()

    included = [c for c in payload["comps"] if c["state"] == "included"]
    assert included, f"recipe {recipe!r} left nothing included — the case is vacuous"
    total = sum(c["weight"] for c in included)

    for comp in included:
        expected = comp["weight"] / total
        assert comp["contribution_share"] == pytest.approx(expected, rel=1e-12), (
            f"comp {comp['key']!r}: contribution_share is {comp['contribution_share']!r}, "
            f"expected weight/Σ = {comp['weight']}/{total} = {expected}. The formula is an "
            "[INVARIANT] definition (NORTH_STAR: 'a selected comp's weight ÷ sum of "
            "selected weights'), not an implementation choice"
        )

    assert sum(c["contribution_share"] for c in included) == pytest.approx(1.0, rel=1e-9), (
        "the included comps' contribution shares do not sum to 1 — the denominator is not "
        "the sum of the selected weights"
    )
    for comp in payload["comps"]:
        if comp["state"] != "included":
            assert comp["contribution_share"] is None, (
                f"comp {comp['key']!r} is {comp['state']!r} but reports a contribution share"
            )


def test_a_weight_three_comp_contributes_exactly_like_three_weight_one_comps(
    derive, derived
) -> None:
    """The weight scale means what F0-S3 locked it to mean.

    Written as the observable outcome ("3× the share of a weight-1 peer"), not
    as "``weighted_median`` was called with a replicated list" — the latter
    would break on any legitimate ``[DEFAULT]`` swap of the statistics module
    for no real reason.
    """
    keys = sqft_bearing_keys(derived)
    heavy, *rest = keys
    payload = derive(weights={heavy: 3.0, **{k: 1.0 for k in rest}}).json()

    by_key = comps_by_key(payload)
    heavy_share = by_key[heavy]["contribution_share"]
    peer_share = by_key[rest[0]]["contribution_share"]
    assert heavy_share == pytest.approx(3.0 * peer_share, rel=1e-12), (
        f"a weight-3 comp's share is {heavy_share!r} against a weight-1 peer's "
        f"{peer_share!r} — weight 3 must mean exactly three weight-1 entries"
    )


def test_a_dominant_comp_is_reported_not_capped(derive, derived) -> None:
    """F5 epic edge: "one comp >~40% contribution → warning colour, **no hard
    cap by design**".

    The colour is the view's job (browser spec).  The half that belongs here is
    that the server hands over the real, uncapped number — a silent clamp at
    0.4 would make the warning unreachable *and* would misstate how much the
    analysis is leaning on one comp, which is the opposite of what the warning
    exists to say.
    """
    keys = sqft_bearing_keys(derived)
    dominant, *rest = keys
    payload = derive(weights={dominant: 99.0, **{k: 1.0 for k in rest}}).json()

    share = comps_by_key(payload)[dominant]["contribution_share"]
    expected = 99.0 / (99.0 + len(rest))
    assert share == pytest.approx(expected, rel=1e-12), (
        f"dominant comp's share is {share!r}, expected {expected} — a value clamped to a "
        "threshold (0.4) or to 1.0 means the share is being capped; the design says report "
        "it and colour it, never cap it"
    )
    assert share > 0.4, (
        "this fixture cannot produce a >40% share — the browser spec's warning-colour case "
        "would have no server-side counterpart"
    )


# --------------------------------------------------------------------------
# AC2 (server half) — what ALL / NONE rely on the server to preserve
# --------------------------------------------------------------------------


def test_a_filtered_comp_keeps_the_weight_the_client_sent(derive, derived) -> None:
    """ALL/NONE act on the *visible* set; the invisible set must therefore be
    inert — including across a filter reset.

    This is the server-side precondition for AC2: if a filter silently
    rewrote a filtered comp's weight, then "NONE, then clear the filter" would
    resurrect comps at the wrong weight no matter how correct the client's
    bulk-set was.  Deliberately asserts the *weight* only, never the *label*:
    ``pipeline/membership.py`` marks its filtered-beats-weight-0 precedence
    ``[DEFAULT]`` and F7-S1 may reverse it.
    """
    keys = [c["key"] for c in derived["comps"]]
    sent = {k: round(1.0 + 0.5 * i, 2) for i, k in enumerate(keys)}

    tight = derive(
        weights=sent,
        filters={"max_distance_mi": 0.5, "hide_censored": False, "leased_only": False},
    ).json()
    filtered_keys = [c["key"] for c in tight["comps"] if c["state"] == "filtered"]
    assert filtered_keys, (
        "max_distance_mi=0.5 filtered nothing in this fixture — the case is vacuous"
    )

    for comp in tight["comps"]:
        assert comp["weight"] == pytest.approx(sent[comp["key"]]), (
            f"comp {comp['key']!r} was sent weight {sent[comp['key']]} and came back with "
            f"{comp['weight']!r} — a filter rewrote a weight"
        )

    cleared = derive(weights=sent).json()
    for comp in cleared["comps"]:
        assert comp["weight"] == pytest.approx(sent[comp["key"]]), (
            f"comp {comp['key']!r} lost its weight when the filter was cleared"
        )
    for key in filtered_keys:
        assert comps_by_key(cleared)[key]["state"] == "included", (
            f"comp {key!r} was filtered out at a positive weight and did not come back "
            "included when the filter cleared"
        )


def test_a_filtered_comp_contributes_to_nothing_while_it_is_filtered(derive, derived) -> None:
    """The other half: invisible must also mean out of the math, or ALL/NONE
    operating on the visible set would still move numbers it never touched."""
    payload = derive(
        filters={"max_distance_mi": 0.5, "hide_censored": False, "leased_only": False}
    ).json()
    filtered_keys = {c["key"] for c in payload["comps"] if c["state"] == "filtered"}
    assert filtered_keys, "nothing was filtered — the case is vacuous"

    for name, keys in evidence_lists(payload).items():
        leaked = filtered_keys.intersection(keys)
        assert not leaked, f"filtered comps {sorted(leaked)} are still cited by {name}"

    for key in filtered_keys:
        assert comps_by_key(payload)[key]["contribution_share"] is None, (
            f"filtered comp {key!r} reports a contribution share"
        )


# --------------------------------------------------------------------------
# F5 epic edges that live in the derived state
# --------------------------------------------------------------------------


@pytest.mark.parametrize("zero_count", [0, 1, 3, 4, 5])
def test_the_included_count_is_exact_as_weights_change(derive, derived, zero_count) -> None:
    """F5 epic edge: "below 5 included comps → ANALYSIS button disables with
    reason" (F5-S3 owns the button; this owns the number it reads).

    A count that drifts from the labels would disable the gate on a set that
    is actually fine, or — worse — enable an analysis over 3 comps.
    """
    keys = [c["key"] for c in derived["comps"]]
    payload = derive(weights={k: (0.0 if i < zero_count else 1.0) for i, k in enumerate(keys)}).json()

    labelled = included_keys(payload)
    breakdown = payload["breakdown"]
    assert breakdown["included"] == len(labelled), (
        f"breakdown.included is {breakdown['included']} but {len(labelled)} comps are "
        "labelled 'included' — the count and the labels come from one classification "
        "or F5-S3's gate is reading a different number than the list shows"
    )
    assert set(breakdown["comp_keys"]["included"]) == labelled
    assert breakdown["included"] + breakdown["excluded"] + breakdown["filtered"] == breakdown["pulled"], (
        "included + excluded + filtered != pulled (F7-S1 [INVARIANT]) after a weight edit"
    )


def test_a_missing_sqft_comp_starts_excluded_and_can_be_re_included_by_hand(
    derive, derived
) -> None:
    """F5 epic edge: "missing-sqft row → excluded by default, ``no sqft`` badge,
    manual re-include allowed."

    The badge is F5-S1's (blocked, not this story).  The two state facts are
    this story's: the default is 0, and an explicit positive weight wins — the
    user's judgement is echoed back rather than silently overridden.
    """
    no_sqft = [c for c in derived["comps"] if c["sqft"] is None]
    assert no_sqft, "the fixture has no missing-sqft comp — the edge is untestable"
    target = no_sqft[0]["key"]

    assert no_sqft[0]["weight"] == 0.0
    assert no_sqft[0]["state"] == "excluded"
    assert target in derived["breakdown"]["comp_keys"]["missing_sqft"], (
        "a missing-sqft comp is not reachable from the missing_sqft count (T-S2: every "
        "count clicks through to its comps)"
    )
    assert no_sqft[0]["psf"] is None and no_sqft[0]["premium"] is None, (
        "a comp with no sqft was given a $/sqft — NORTH_STAR: never a fabricated number"
    )

    re_included = derive(weights={target: 1.0}).json()
    comp = comps_by_key(re_included)[target]
    assert comp["weight"] == 1.0, (
        "an explicit weight on a no-sqft comp was overridden — the defaulting rule applies "
        "only when the client said nothing"
    )
    assert comp["state"] == "included", (
        "a manually re-included no-sqft comp is still labelled excluded; the F5 epic says "
        "manual re-include is allowed"
    )
    assert comp["psf"] is None, (
        "re-including a no-sqft comp invented a $/sqft for it"
    )
