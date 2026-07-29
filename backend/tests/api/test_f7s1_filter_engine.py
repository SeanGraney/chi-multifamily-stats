"""F7-S1 [INVARIANT] — the filter engine, Layer 2.

QA-authored, written before the developer's branch exists (AGENT_QA.md
protocol step 3). Every assertion goes through ``POST /api/derive`` with
``TestClient``; no pipeline internal is imported, so these keep holding when
the stages behind the endpoint change.

THE STORY, DECOMPOSED
---------------------
The story is one sentence with three clauses, and it is the model the PM
holds up for what an ``[INVARIANT]`` tag should look like — pure outcomes,
no technique::

    filtered comps leave the list AND ALL CALCULATIONS but stay rust on the
    map; manual INCLUDE overrides survive filter resets; reconciliation
    invariant `included + excluded + filtered = pulled` asserted in dev builds

Split by the decision procedure:

* **"leave all calculations"** — a ``DerivedState`` field comparison, so it
  stops at Layer 2 (step 2). It is also the load-bearing half: a comp that
  vanishes from the list but still moves the anchor is precisely the honesty
  failure ``NORTH_STAR.md`` exists to prevent, and it is invisible from a
  browser, because the browser can no longer see the comp that is doing it.
* **"leave the list"** — a render fact (which rows mount). Layer 3,
  ``e2e/tests/f7-s1-filter-engine.spec.ts``.
* **"stay rust on the map"** — also Layer 3, and F6-S1's map does not exist
  yet; it is a **strict xfail** in the spec above, never a skip.
* **"overrides survive filter resets"** — two halves. The server half (an
  override beats every filter, and is inert when no filter is on, so a reset
  cannot lose it) is here. The client half (a reset does not *clear* the
  override list) is a React-state claim and is Layer 3.
* **the reconciliation identity** — "an invariant over derived counts" is the
  decision procedure's own worked example of Layer 2.

WHAT "ASSERTED IN DEV BUILDS" IS AND IS NOT TESTED AS
----------------------------------------------------
The ``[INVARIANT]`` is the **identity**. "asserted in dev builds" is a
delivery mechanism for it, and AGENT_QA.md is explicit that a test which pins
a suggested technique instead of the required outcome is a defect in the test.
So nothing below looks for an ``assert`` statement, a debug flag or a
``__debug__`` guard: the identity is asserted directly, across a matrix of
filter / override / weight states, which is what a dev-build assertion would
be *for*. If the developer also adds a runtime guard, its own unit test is
theirs to write; it must not be able to 500 a legitimate request.

WHAT IS DELIBERATELY *NOT* HERE (already covered — do not duplicate)
-------------------------------------------------------------------
* ``test_derive_contract.py::test_filters_are_parameters_not_a_client_side_prefiltered_list``
  — filters travel as parameters and never shrink the payload (the seam).
* ``test_derive_contract.py::test_breakdown_partitions_the_pull`` — the
  identity for the default body.
* ``test_f5s2_selection_weight.py::test_a_filtered_comp_keeps_the_weight_the_client_sent``
  and ``::test_a_filtered_comp_contributes_to_nothing_while_it_is_filtered``
  — the weight survives a filter, and a filtered comp is cited by no evidence
  list. Both are *citation* checks over ``comp_keys``.
* ``test_f4s4_window_on_the_wire.py::test_no_padding_record_is_counted_anywhere_in_the_breakdown``
  — ``pulled`` counts POST-WINDOW comps, so the +/-90d pad cannot inflate the
  identity. That ruling is F4-S4's and this file extends it to the case F4-S4
  did not cover: the same claim with a filter switched on.

This file starts where those stop. In particular, "a filtered comp is not
*cited*" is weaker than "a filtered comp did not *move a number*" — a
statistic can be computed over a comp and simply forget to name it. The
central test here compares the numbers themselves.

INVARIANT vs DEFAULT (SEMANTIC_CHANGE_PROTOCOL)
-----------------------------------------------
Nothing below pins a ``[DEFAULT]``. Specifically it is silent about:

* ``pipeline/membership.py``'s filtered-beats-weight-0 **precedence** — that
  module marks it ``[DEFAULT]`` and says F7-S1 may reverse it. The tests here
  assert the *partition* (every comp gets exactly one label, the three counts
  reconcile), never which label a comp lands on when both rules fire. Two
  tests do read the ``filtered`` label, and both drive comps that are at a
  positive weight, where no precedence question arises.
* the distance metric (haversine vs projected) and the inclusivity of the
  ``max_distance_mi`` boundary — bounds below are chosen well clear of any
  comp's distance so a ``>`` / ``>=`` swap cannot flip a result here. The
  boundary itself is a Layer-1 claim and is the developer's to pin.
* whether ``leased_only`` counts ``provisional`` (it does today, per the
  removal ladder) — the tests derive their expectation from the payload's own
  ``removal_class`` / ``censored`` fields rather than from a hard-coded list.

Fixture mode only (WORKFLOW.md §6): ``RENTCOMP_LIVE`` is never set, the pull
is the committed ``fixtures/synthetic/pulls/synthetic-basic.json``, and the
root conftest's autouse socket kill switch makes a live call impossible.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Filter vocabulary. Every body below is built from `make_body`, so the three
# named filters are the only thing these dicts vary.
# ---------------------------------------------------------------------------

NO_FILTER: dict[str, Any] = {
    "max_distance_mi": None,
    "hide_censored": False,
    "leased_only": False,
}

#: 0.5mi over `synthetic-basic` keeps 3 of 9 comps and takes 6; no comp sits
#: within 0.14mi of it, so the `>` / `>=` boundary is not in play.
NEAR_ONLY = {**NO_FILTER, "max_distance_mi": 0.5}
HIDE_CENSORED = {**NO_FILTER, "hide_censored": True}
LEASED_ONLY = {**NO_FILTER, "leased_only": True}
COMBO = {"max_distance_mi": 1.2, "hide_censored": True, "leased_only": True}
#: Takes everything: no comp is 0.0mi from the subject.
EVERYTHING = {**NO_FILTER, "max_distance_mi": 0.0}

NAMED_FILTERS = [
    ("near_only", NEAR_ONLY),
    ("hide_censored", HIDE_CENSORED),
    ("leased_only", LEASED_ONLY),
    ("combo", COMBO),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def comps_by_key(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {comp["key"]: comp for comp in payload["comps"]}


def keys_in_state(payload: dict[str, Any], state: str) -> list[str]:
    return [comp["key"] for comp in payload["comps"] if comp["state"] == state]


def selectable(payload: dict[str, Any]) -> set[str]:
    """Comps at a positive weight — the ones whose label is unambiguous.

    ``pipeline/membership.py`` marks the precedence between "filtered" and
    "weight 0" a ``[DEFAULT]`` and says F7-S1 may reverse it, so a comp that
    is *both* filtered out and weight-0 may honestly carry either label. Every
    "this filter took exactly these comps" assertion below is therefore scoped
    to comps where only one rule can fire.

    Found by mutation-testing this file: reversing that precedence killed two
    tests here, which meant they were pinning a ``[DEFAULT]`` — a defect in
    the test, not in the code (AGENT_QA.md's fifth smell).
    """
    return {comp["key"] for comp in payload["comps"] if comp["weight"] > 0.0}


def statistics_only(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with every *label* removed — only the numbers are left.

    Three things are dropped, and each is dropped because it is a statement
    about *how a comp was treated*, not about a derived quantity:

    * ``comps[].state`` — the label under test;
    * ``comps[].weight`` — the response echoes what the client sent, and a
      filtered comp deliberately keeps its weight (F5-S2), so a filtered comp
      and a zeroed one must differ here;
    * ``breakdown`` — the counts of those labels.

    Everything that survives is a number the user reads and acts on: the
    anchor and its band, cohort medians and thin flags, bucket statistics and
    dollar boundaries, the price test (guard or curve, its neighbours and its
    expected vacancy), each comp's psf / premium / bucket / contribution
    share, and the warnings.
    """
    stripped = copy.deepcopy(payload)
    for comp in stripped["comps"]:
        comp.pop("state")
        comp.pop("weight")
    stripped.pop("breakdown")
    return stripped


def first_difference(left: dict[str, Any], right: dict[str, Any]) -> str:
    """A readable name for where two payloads diverge, for the failure text."""
    for section in sorted(set(left) | set(right)):
        if left.get(section) != right.get(section):
            if section == "comps":
                for a, b in zip(left["comps"], right["comps"]):
                    for field in sorted(set(a) | set(b)):
                        if a.get(field) != b.get(field):
                            return f"comps[{a.get('key')!r}].{field}: {a.get(field)!r} != {b.get(field)!r}"
            return f"{section}: {left.get(section)!r} != {right.get(section)!r}"
    return "(no difference)"


# ---------------------------------------------------------------------------
# Harness guards — two loud, ungated failures that keep the red state readable
# ---------------------------------------------------------------------------


def test_the_derive_under_test_is_this_worktrees_source_tree() -> None:
    """WORKFLOW.md §2: in a worktree the wrong source tree loads *silently*.

    The editable install's ``.pth`` points at the MAIN checkout's
    ``backend/src``, and a ``PYTHONPATH`` that was mis-separated (``:``
    instead of ``;`` on Windows) collapses to nothing without erroring — the
    run then answers confidently about code that is not on this branch. A
    bare full run is rescued only by collection order, which is an accident,
    not a guarantee.

    So: assert the loaded module sits under the same repo root as this file.
    Cheap, and it turns the most deceptive failure mode in this project into
    a named test rather than a wrong PASS report.
    """
    from rentcomp.pipeline import membership

    loaded = Path(membership.__file__).resolve()
    expected_root = Path(__file__).resolve().parents[3]  # <root>/backend/tests/api/..
    assert loaded.is_relative_to(expected_root), (
        f"the derive pipeline was imported from {loaded}, which is NOT under this "
        f"checkout ({expected_root}). The run is testing another tree's code — set "
        "PYTHONPATH=<this worktree>/backend/src (';'-separated on Windows) and rerun. "
        "Any result from this run is about the wrong branch."
    )


def test_the_fixture_pull_can_express_this_story(derived) -> None:
    """Fixture adequacy, asserted once and loudly.

    Every test below reasons "this filter takes these comps"; against a pull
    with no censored comp, no pending removal or no spread of distances, half
    of them would pass by filtering nothing at all. A vacuous green is the
    failure mode this project has hit four times in a different costume
    (WORKFLOW.md §2's skip-that-exits-0), so it gets its own named failure.
    """
    comps = derived["comps"]
    assert len(comps) >= 5, f"the fixture pull has only {len(comps)} comps"
    assert any(c["censored"] for c in comps), (
        "no censored comp — `hide_censored` would filter nothing and its tests would be vacuous"
    )
    assert any(not c["censored"] and c["removal_class"] == "pending" for c in comps), (
        "no pending removal — `leased_only` would then take exactly the censored comps and "
        "its test could not tell the two rules apart"
    )
    assert any(c["removal_class"] in ("provisional", "confirmed") for c in comps), (
        "no comp reached the removal ladder — `leased_only` would take everything"
    )
    distances = sorted(c["distance_mi"] for c in comps)
    assert distances[0] < NEAR_ONLY["max_distance_mi"] < distances[-1], (
        f"max_distance_mi={NEAR_ONLY['max_distance_mi']} does not split this pull "
        f"(distances {distances[0]:.3f}..{distances[-1]:.3f}) — the distance tests would "
        "either filter everything or nothing"
    )
    assert min(abs(d - NEAR_ONLY["max_distance_mi"]) for d in distances) > 0.05, (
        "a comp sits within 0.05mi of the test bound, so a `>` vs `>=` boundary swap could "
        "flip these results — this file must not be sensitive to a [DEFAULT]"
    )
    assert any(c["psf"] is None for c in comps), (
        "no comp is missing sqft — the override-does-not-resurrect test would be vacuous"
    )


# ---------------------------------------------------------------------------
# AC1 — a filter re-labels; it never removes evidence
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "filters"), NAMED_FILTERS + [("everything", EVERYTHING)])
def test_a_filter_relabels_every_comp_and_removes_none(derive, derived, name, filters) -> None:
    """The precondition for "stay rust on the map" (F6-S1 renders what the
    payload carries) and for every click-through in F9.

    A filter that *dropped* comps would make the map honest-looking and empty:
    the epic's promise is "thin the list without deleting anything", and the
    map "never hides evidence".
    """
    payload = derive(filters=filters).json()

    assert [c["key"] for c in payload["comps"]] == [c["key"] for c in derived["comps"]], (
        f"filter {name!r} changed which comps the payload carries. A filter re-labels; it "
        "never removes evidence — the map must still be able to draw the filtered comps rust "
        "(F7 epic: 'thin the list without deleting anything')"
    )
    assert payload["breakdown"]["pulled"] == derived["breakdown"]["pulled"], (
        f"filter {name!r} changed `pulled`, which is the top of the reconciliation identity"
    )
    for comp in payload["comps"]:
        assert comp["state"] in ("included", "excluded", "filtered"), (
            f"comp {comp['key']!r} carries state {comp['state']!r}"
        )


# ---------------------------------------------------------------------------
# AC2 — each filter takes the comps it names, and only those
# ---------------------------------------------------------------------------


def test_max_distance_takes_exactly_the_comps_beyond_it(derive, derived) -> None:
    """Expectation derived from the payload's own ``distance_mi``, never from
    a hard-coded address list, so the metric stays a ``[DEFAULT]``."""
    bound = NEAR_ONLY["max_distance_mi"]
    payload = derive(filters=NEAR_ONLY).json()
    scope = selectable(derived)

    expected = {c["key"] for c in derived["comps"] if c["distance_mi"] > bound} & scope
    assert expected, "vacuous: no selectable comp is beyond the bound"
    assert set(keys_in_state(payload, "filtered")) & scope == expected, (
        f"max_distance_mi={bound} filtered "
        f"{sorted(set(keys_in_state(payload, 'filtered')) & scope)}, expected exactly the "
        f"comps further away than that: {sorted(expected)}"
    )


def test_hide_censored_takes_exactly_the_still_active_comps(derive, derived) -> None:
    """NORTH_STAR's most important distinction, as a filter. `hide_censored`
    must key on ``censored`` and on nothing that correlates with it — a comp
    with a long DOM is not a censored comp."""
    payload = derive(filters=HIDE_CENSORED).json()
    scope = selectable(derived)

    expected = {c["key"] for c in derived["comps"] if c["censored"]} & scope
    assert expected, "vacuous: no censored comp in the pull"
    assert set(keys_in_state(payload, "filtered")) & scope == expected, (
        f"hide_censored filtered {sorted(set(keys_in_state(payload, 'filtered')) & scope)}, "
        f"expected exactly the still-active comps {sorted(expected)}"
    )


def test_leased_only_keeps_the_comps_that_reached_the_removal_ladder(derive, derived) -> None:
    """`leased_only` is the UI's word; the meaning is "removed with enough
    confidence to count".

    A censored comp is not a slow lease and a `pending` removal is too recent
    to trust (NORTH_STAR: pendings are *excluded*, not merely marked) — so
    both go, and neither is being re-classified by going. The expectation is
    read off ``removal_class`` so that if the ladder's rungs are ever
    re-tuned, this test follows rather than fossilising a list.
    """
    payload = derive(filters=LEASED_ONLY).json()
    scope = selectable(derived)

    kept = {c["key"] for c in derived["comps"] if c["removal_class"] in ("provisional", "confirmed")}
    expected = ({c["key"] for c in derived["comps"]} - kept) & scope
    assert expected and kept, "vacuous: leased_only keeps everything or nothing in this pull"
    assert set(keys_in_state(payload, "filtered")) & scope == expected, (
        f"leased_only filtered {sorted(set(keys_in_state(payload, 'filtered')) & scope)}, "
        f"expected exactly the comps that did NOT reach provisional/confirmed: {sorted(expected)}.\n"
        "  a censored comp still present => 'still active' is being read as 'leased'\n"
        "  a pending comp still present  => a <7d removal is being counted (NORTH_STAR)"
    )


def test_filters_compose_as_a_union_of_what_each_would_take(derive, derived) -> None:
    """Three filters on at once take the union, not the intersection — each
    is an independent reason a comp is out of view."""
    payload = derive(filters=COMBO).json()
    scope = selectable(derived)

    expected = {
        c["key"]
        for c in derived["comps"]
        if c["distance_mi"] > COMBO["max_distance_mi"]
        or c["censored"]
        or c["removal_class"] not in ("provisional", "confirmed")
    } & scope
    assert set(keys_in_state(payload, "filtered")) & scope == expected, (
        "with all three filters on, the filtered set is not the union of what each takes "
        "individually — an intersection would leave comps visible that the user asked to "
        f"hide.\n  got     : {sorted(set(keys_in_state(payload, 'filtered')) & scope)}\n"
        f"  expected: {sorted(expected)}"
    )


def test_a_comp_no_filter_matches_is_never_labelled_filtered(derive, derived) -> None:
    """The other side of the same coin, and it holds under either precedence:
    whatever a filter does to the comps it matches, a comp it does not match
    must never be labelled ``filtered``. Otherwise the "N filtered · show"
    footer would offer the user comps nothing hid, and the rust pins on the
    map would be telling them their filter did something it did not.
    """
    for name, filters in NAMED_FILTERS:
        payload = derive(filters=filters).json()
        for comp in payload["comps"]:
            reference = comps_by_key(derived)[comp["key"]]
            matched = (
                (filters["max_distance_mi"] is not None
                 and reference["distance_mi"] > filters["max_distance_mi"])
                or (filters["hide_censored"] and reference["censored"])
                or (filters["leased_only"]
                    and reference["removal_class"] not in ("provisional", "confirmed"))
            )
            if not matched:
                assert comp["state"] != "filtered", (
                    f"[{name}] comp {comp['key']!r} is labelled 'filtered' but no active "
                    "filter matches it"
                )


# ---------------------------------------------------------------------------
# AC3 — "...and ALL CALCULATIONS". The load-bearing half.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "filters"), NAMED_FILTERS)
def test_a_filtered_comp_moves_no_number_that_a_weight_zero_comp_would_not(
    derive, name, filters
) -> None:
    """**The story's central claim, in its provable form.**

    "Filtered comps leave the list AND ALL CALCULATIONS." The strongest way to
    say that is not "no aggregate names them" — an aggregate can be computed
    over a comp and simply forget to cite it — but: *filtering a comp is
    numerically identical to excluding it by hand*. Weight-0 exclusion is
    already pinned to leave every aggregate (F5-S2), so equality with it
    transfers that guarantee to filtering, across every statistic in the
    payload at once, including ones no test enumerates by name today.

    What must still differ, and is therefore excluded from the comparison, is
    only the bookkeeping: the label, the echoed weight (a filtered comp keeps
    the weight the client sent, so it comes back at that weight when the
    filter clears), and the breakdown counts.

    Why this matters more than it looks: a comp that has left the list but is
    still moving the anchor is the exact honesty failure NORTH_STAR exists to
    prevent, and it is *unobservable from the browser* — the user cannot see
    the comp that is doing it. There is nowhere else this can be caught.
    """
    filtered = derive(filters=filters).json()
    taken = keys_in_state(filtered, "filtered")
    assert taken, f"filter {name!r} took nothing — this case is vacuous"

    by_hand = derive(weights={key: 0.0 for key in taken}).json()

    assert statistics_only(filtered) == statistics_only(by_hand), (
        f"under filter {name!r} the derived numbers differ from the same comps excluded by "
        f"hand at weight 0.\n  first difference: {first_difference(statistics_only(filtered), statistics_only(by_hand))}\n"
        "A filtered comp must leave ALL CALCULATIONS, not just the list — if it still moves "
        "the anchor, a bucket, a cohort median or the price test, the number on screen rests "
        "on evidence the user believes they removed (NORTH_STAR)."
    )


def test_filtering_a_comp_the_anchor_leans_on_actually_moves_the_anchor(derive, derived) -> None:
    """The control for the test above.

    Equality with "excluded by hand" would also hold if filters were applied
    *nowhere* and both derives were simply the unfiltered one. This proves the
    filter is doing something to the numbers at all — and it is deliberately
    the distance filter, because on this fixture the censored comps sit near
    the median and hiding them leaves the anchor unchanged. A test that
    demanded every filter move the anchor would be asserting something about
    the fixture, not about the code.
    """
    filtered = derive(filters=NEAR_ONLY).json()
    assert derived["anchor"] is not None and filtered["anchor"] is not None
    assert filtered["anchor"]["rent"]["mid"] != derived["anchor"]["rent"]["mid"], (
        "restricting the comp set to a 0.5mi radius left the anchor unchanged, so the "
        "equality test above could be passing because filters reach no calculation at all"
    )
    assert filtered["anchor"]["n_comps"] < derived["anchor"]["n_comps"], (
        "the anchor still rests on as many comps after a filter took most of them"
    )


def test_a_filter_that_takes_everything_leaves_no_anchor_and_no_price_test(derive) -> None:
    """The degenerate edge, which is also the honesty edge.

    With no evidence left there is nothing to state, and the payload says so
    with nulls rather than with a number computed over an empty set. A guard
    or an empty state is a different render, never a curve with wide bars
    (NORTH_STAR).
    """
    payload = derive(filters=EVERYTHING, candidate_rent=2400.0).json()
    breakdown = payload["breakdown"]

    assert breakdown["included"] == 0, (
        "a 0.0mi radius left comps included"
    )
    # Precedence-agnostic: a comp that is both filtered out and weight-0 may
    # honestly carry either label ([DEFAULT]), so what is asserted is that
    # every *selectable* comp was taken and that nothing is left included.
    # (a filtered comp keeps the weight the client sent, so `selectable` reads
    # the same set off this payload as off an unfiltered one)
    assert selectable(payload) <= set(keys_in_state(payload, "filtered")), (
        f"comps {sorted(selectable(payload) - set(keys_in_state(payload, 'filtered')))} "
        "were at a positive weight and a 0.0mi radius did not take them"
    )
    assert payload["anchor"] is None, (
        f"an anchor ({payload['anchor']}) was derived with every comp filtered out — it "
        "would be a number resting on no evidence at all"
    )
    assert payload["price_test"] is None, (
        "a price test was returned with no evidence behind it"
    )
    for bucket in payload["buckets"]:
        assert bucket["count"] == 0, f"bucket {bucket['id']!r} counts filtered comps"
        assert bucket["leased_dom_median"] is None, (
            f"bucket {bucket['id']!r} reports a median DOM with everything filtered — an "
            "empty bucket renders a dash, never an estimate (NORTH_STAR)"
        )


# ---------------------------------------------------------------------------
# AC6 / AC7 (server half) — manual INCLUDE overrides
# ---------------------------------------------------------------------------


def test_a_manual_include_override_beats_every_filter(derive, derived) -> None:
    """The mechanism the whole "overrides survive filter resets" clause rests
    on, and the rust-pin-click path in the F6 epic ("rust pin click → tooltip
    offers INCLUDE override (un-filters it)").

    Asserted over all three filters at once, since an override that beat only
    one of them would be a partial answer that looks complete.
    """
    target = next(
        c["key"]
        for c in derived["comps"]
        if c["psf"] is not None and c["distance_mi"] > COMBO["max_distance_mi"]
    )
    payload = derive(filters=COMBO, include_overrides=[target]).json()
    comp = comps_by_key(payload)[target]

    assert comp["state"] == "included", (
        f"comp {target!r} is in include_overrides and at a positive weight, but came back "
        f"{comp['state']!r}. A manual INCLUDE must beat every filter — otherwise the user "
        "cannot re-admit a comp they can see is right, and F7-S2's per-row INCLUDE has no "
        "effect"
    )
    assert payload["anchor"] is not None and target in payload["anchor"]["comp_keys"], (
        f"comp {target!r} was overridden back in but is not cited as evidence for the "
        "anchor — re-included must mean re-included in the math, not just in the list"
    )
    assert comp["contribution_share"] is not None, (
        "an overridden-in comp reports no contribution share, so the user cannot see how "
        "much the comp they just re-admitted is influencing the analysis"
    )


def test_an_override_does_not_override_the_weight(derive, derived) -> None:
    """F5-S2 ``[INVARIANT]``: **the weight is the single source of truth** for
    selection. ``include_overrides`` exempts a comp from the *filters*; it
    must not be a second channel that can make a weight-0 comp included.

    The two obvious ways to get this wrong point opposite directions and both
    are silent: an override that forced inclusion would resurrect comps the
    user had deselected (and would give a no-sqft comp — which contributes
    nothing to a $/sqft anchor — a contribution share); an override that also
    zeroed something would lose curation. So this pins both a no-sqft comp
    (weight 0 by the defaulting rule) and a hand-zeroed one.
    """
    no_sqft = next(c["key"] for c in derived["comps"] if c["psf"] is None)
    zeroed = next(c["key"] for c in derived["comps"] if c["psf"] is not None)

    payload = derive(
        filters=EVERYTHING,
        weights={zeroed: 0.0},
        include_overrides=[no_sqft, zeroed],
    ).json()
    by_key = comps_by_key(payload)

    for key, why in (
        (no_sqft, "it has no $/sqft and so defaults to weight 0 (F5 epic edge)"),
        (zeroed, "the client sent it weight 0"),
    ):
        assert by_key[key]["state"] == "excluded", (
            f"comp {key!r} came back {by_key[key]['state']!r} after being named in "
            f"include_overrides, but {why}. An override un-filters a comp; it does not "
            "select one. Selection IS the weight (F5-S2 [INVARIANT]) — a second channel "
            "into inclusion is exactly the hidden state that invariant exists to forbid"
        )
        assert by_key[key]["contribution_share"] is None, (
            f"comp {key!r} is not included but reports a contribution share"
        )


def test_an_override_is_inert_when_no_filter_is_active(derive, derived) -> None:
    """"Overrides survive filter resets", server half.

    A reset is just the default ``Filters``, so surviving means: carrying the
    override list through a reset must be indistinguishable from never having
    had one. If an override changed anything with no filter on, then a reset
    would not restore the pre-filter view and the user's earlier curation
    would have been silently rewritten by a filter they have since cleared.
    """
    everything = [c["key"] for c in derived["comps"]]
    after_reset = derive(include_overrides=everything).json()

    assert after_reset == derived, (
        "carrying include_overrides through a filter reset changed the derived state.\n"
        f"  first difference: {first_difference(after_reset, derived)}\n"
        "With no filter active there is nothing to override, so the response must be "
        "identical to one with no overrides at all — otherwise 'reset' does not return the "
        "user to where they were"
    )


def test_re_applying_a_filter_with_the_same_overrides_restores_the_same_state(
    derive, derived
) -> None:
    """The round trip the clause actually describes: filter on, override, reset,
    filter on again — the comp is still there.

    Stateless server (F0-S2), so this is really a statement that nothing in
    the request is *consumed*: the override list is a durable field of the
    curation state, not a one-shot instruction that a subsequent filter change
    can spend.
    """
    target = next(
        c["key"] for c in derived["comps"] if c["psf"] is not None and c["distance_mi"] > 0.5
    )
    on = derive(filters=NEAR_ONLY, include_overrides=[target]).json()
    reset = derive(filters=NO_FILTER, include_overrides=[target]).json()
    on_again = derive(filters=NEAR_ONLY, include_overrides=[target]).json()

    assert comps_by_key(on)[target]["state"] == "included"
    assert comps_by_key(reset)[target]["state"] == "included"
    assert on_again == on, (
        "re-applying the same filter with the same overrides produced a different derived "
        f"state.\n  first difference: {first_difference(on_again, on)}\n"
        "The override was consumed by the intervening reset"
    )


def test_an_override_naming_a_comp_this_pull_does_not_contain_is_harmless(derive) -> None:
    """A stale workspace is a normal condition, not an error.

    F13-S1 keys curation by address+unit precisely because listing ids churn;
    a refreshed pull can still legitimately lose a comp entirely (it aged out
    of the window). The saved override list then names a comp that is not
    here, and that must not cost the user their whole workspace.
    """
    response = derive(filters=NEAR_ONLY, include_overrides=["no such comp|9z"])
    assert response.status_code == 200, (
        f"an override naming an unknown comp returned {response.status_code}: "
        f"{response.text[:300]}. A workspace saved against an earlier pull would be "
        "unopenable"
    )


# ---------------------------------------------------------------------------
# AC8 — the reconciliation identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("no filter", {}),
        ("near only", {"filters": NEAR_ONLY}),
        ("hide censored", {"filters": HIDE_CENSORED}),
        ("leased only", {"filters": LEASED_ONLY}),
        ("all three", {"filters": COMBO}),
        ("everything filtered", {"filters": EVERYTHING}),
        ("filter + drift", {"filters": COMBO, "drift_pct": -4.0}),
        ("filter + candidate rent", {"filters": NEAR_ONLY, "candidate_rent": 2400.0}),
    ],
)
def test_the_reconciliation_identity_holds_in_every_curation_state(
    derive, derived, name, overrides
) -> None:
    """``included + excluded + filtered == pulled`` (F7-S1 [INVARIANT]).

    The identity is what makes the F9 breakdown a *reconciliation* rather than
    three counts that happen to be near each other: it is the user's guarantee
    that no comp fell down a gap between "I removed it" and "it is still in
    there". Checked across the filter/weight/override matrix rather than once,
    because the ways to break it are all interaction bugs — a comp that
    matches two rules counted twice, or one that matches none counted nowhere.

    Weight vectors are layered on top of each override set below, so each case
    exercises the filter rule and the weight rule together; that intersection
    is where a double-count lives.
    """
    keys = [c["key"] for c in derived["comps"]]
    weight_shapes = {
        "default": {},
        "all zero": {k: 0.0 for k in keys},
        "alternating": {k: (0.0 if i % 2 else 2.0) for i, k in enumerate(keys)},
    }
    for shape, weights in weight_shapes.items():
        for override_set in ([], keys[:1], keys):
            payload = derive(weights=weights, include_overrides=override_set, **overrides).json()
            breakdown = payload["breakdown"]
            total = breakdown["included"] + breakdown["excluded"] + breakdown["filtered"]

            assert total == breakdown["pulled"], (
                f"[{name} / weights={shape} / {len(override_set)} override(s)] "
                f"included({breakdown['included']}) + excluded({breakdown['excluded']}) + "
                f"filtered({breakdown['filtered']}) = {total} != "
                f"pulled({breakdown['pulled']}) — F7-S1's reconciliation invariant"
            )
            states = [c["state"] for c in payload["comps"]]
            for label in ("included", "excluded", "filtered"):
                assert states.count(label) == breakdown[label], (
                    f"[{name} / weights={shape}] breakdown.{label}={breakdown[label]} but "
                    f"{states.count(label)} comps carry state={label!r} — the count and the "
                    "label must come from one classification, or F9's click-through shows a "
                    "different set than the number it was reached from"
                )
                assert sorted(breakdown["comp_keys"][label]) == sorted(
                    keys_in_state(payload, label)
                ), f"[{name} / weights={shape}] breakdown.comp_keys[{label!r}] disagrees with the labels"


def test_the_identity_is_measured_over_the_post_window_comps_the_payload_carries(
    derive, derived
) -> None:
    """F4-S4's ruling, restated where a filter can reach it.

    ``pulled`` counts POST-WINDOW comps: the +/-90d pad that spec §3.2 buys is
    dropped one stage earlier and is deliberately not on the wire, so a
    padding count can never change what this invariant is measured over
    (``pipeline/shape.py::ShapingSummary``). ``test_f4s4_window_on_the_wire.py``
    pins that for an unfiltered derive; the case it does not cover is this
    one — that switching a filter on does not change the answer either. It
    must not: a filter re-labels the comps that are already here.
    """
    for filters in (NO_FILTER, NEAR_ONLY, COMBO, EVERYTHING):
        payload = derive(filters=filters).json()
        assert payload["breakdown"]["pulled"] == len(payload["comps"]) == len(derived["comps"]), (
            f"with filters={filters}, pulled={payload['breakdown']['pulled']} but the payload "
            f"carries {len(payload['comps'])} comps (unfiltered: {len(derived['comps'])}). "
            "`pulled` is measured over the post-window comps the payload carries — no more "
            "(padding would inflate every count the user reads as 'how much evidence do I "
            "have') and no fewer (a filter that removed comps would make the identity "
            "trivially true and the map empty)"
        )


def test_every_comp_the_breakdown_counts_is_reachable_from_it(derive) -> None:
    """T-S2 evidence-first: every count clicks through to its comps, and a
    filtered comp is *the* case that matters — it is the one the user has
    hidden and may want back (F7-S2's "N filtered · show")."""
    payload = derive(filters=COMBO).json()
    known = {c["key"] for c in payload["comps"]}
    breakdown = payload["breakdown"]

    for label in ("included", "excluded", "filtered"):
        listed = breakdown["comp_keys"][label]
        assert len(listed) == breakdown[label], (
            f"breakdown.{label} counts {breakdown[label]} but names {len(listed)} comps"
        )
        assert set(listed) <= known, (
            f"breakdown.comp_keys[{label!r}] names comps that are not in the payload: "
            f"{sorted(set(listed) - known)}"
        )
    assert len(set(breakdown["comp_keys"]["filtered"])) == breakdown["filtered"], (
        "a comp is listed twice under `filtered`"
    )


def test_the_per_cohort_counts_account_for_the_whole_pull(derive) -> None:
    """``breakdown.per_cohort`` is the *other* per-cohort number in this
    payload (``cohorts[].selected_count`` is the one the anchor and the
    thin-cohort warning read), and the two answer different questions.

    Pinned here is only the part that must hold under either reading: the
    per-cohort counts partition the same set the identity is measured over.
    Whether F9's rail should show the pull's composition (filter-invariant, as
    today) or the *visible* composition is a display question this story does
    not settle, and QA is not settling it silently — it is raised in the
    handoff for F9-S1 rather than fossilised in a test.
    """
    for filters in (NO_FILTER, NEAR_ONLY, EVERYTHING):
        payload = derive(filters=filters).json()
        breakdown = payload["breakdown"]
        assert sum(c["count"] for c in breakdown["per_cohort"]) == breakdown["pulled"], (
            f"with filters={filters}, the per-cohort counts "
            f"({[(c['year'], c['count']) for c in breakdown['per_cohort']]}) do not sum to "
            f"pulled({breakdown['pulled']}) — the rail would reconcile against nothing"
        )
        for cohort in breakdown["per_cohort"]:
            assert len(cohort["comp_keys"]) == cohort["count"], (
                f"cohort {cohort['year']} counts {cohort['count']} but names "
                f"{len(cohort['comp_keys'])} comps"
            )
