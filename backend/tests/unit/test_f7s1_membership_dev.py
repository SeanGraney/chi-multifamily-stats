"""F7-S1 Layer 1 — the filter engine's pure half, and the `[DEFAULT]`s it owns.

Developer-authored. QA's `backend/tests/api/test_f7s1_filter_engine.py` owns
every `DerivedState` field comparison over HTTP and deliberately left ALL of
Layer 1 here, for a reason it states in its own docstring: three of this
story's choices are `[DEFAULT]`s, and a QA suite that pinned them would be
legislating an implementation rather than an outcome. Its bounds are chosen
well clear of any comp's distance precisely so a ``>`` / ``>=`` swap cannot
flip a result there.

So this file is where those choices get pinned — deliberately, by the person
who made them, with the reasoning attached:

1. **The ``max_distance_mi`` boundary is ``>``**, i.e. a comp sitting *exactly*
   on the bound is KEPT. "Max distance 0.5 mi" is read by everyone who has ever
   used a search filter as "within 0.5 miles", inclusive — the bound names the
   furthest acceptable comp, not the first rejected one. The alternative
   (``>=``) makes the *stated* radius the one distance that is excluded, which
   is the kind of detail nobody notices until a comp they can see on the map
   is missing from the list. It also keeps ``max_distance_mi=0.0`` meaning
   "only a comp at the subject's own coordinates", which is the reading QA's
   ``EVERYTHING`` filter relies on: no real comp is at 0.0mi, so the filter
   takes everything, and it does so because of the *data*, not because 0.0 is
   special-cased anywhere.

2. **The metric is haversine** (unchanged from F0-S2). At the few-mile scale
   of a Chicago comp net the difference between great-circle and a projected
   or geodesic (WGS84 ellipsoid) distance is metres — far below the precision
   anybody reads off a comp row — and it costs no dependency. What is pinned
   below is not the formula but the properties any acceptable metric must
   have: identity, symmetry, and agreement with a known ground distance.

3. **Precedence: a comp that is both filtered out and weight-0 is labelled
   ``filtered``** (unchanged from F0-S2, which marked it `[DEFAULT]` and
   invited F7-S1 to reverse it). Kept, because the label answers a user's
   question — "where did that comp go?" — and the filter is the coarser, later
   statement of intent, the one they can undo with a single click on the strip.
   The weight is still there underneath and comes back untouched when the
   filter clears; the partition is valid either way. QA found it had
   accidentally pinned this and *removed* the pin, so this file is now the only
   place that fixes it: reversing it should be a deliberate edit here, not a
   silent behaviour change nothing notices.

Nothing here re-tests what QA already pins over the wire. What is here is
either a `[DEFAULT]` decision, a boundary QA's fixture cannot reach, or the
reconciliation identity as a *property* over arbitrary inputs rather than over
the eight named curation states QA enumerates.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from rentcomp.models.domain import StitchedComp
from rentcomp.models.requests import Filters, Subject
from rentcomp.pipeline.membership import (
    classify_membership,
    distances_mi,
    haversine_miles,
)

SUBJECT = Subject(address="1 Subject St", lat=41.9, lng=-87.68, sqft=1000.0, beds=2.0, baths=1.0)


def make_comp(
    *,
    censored: bool = False,
    removal_class: str | None = "confirmed",
    lat: float = 41.9,
    lng: float = -87.68,
) -> StitchedComp:
    return StitchedComp(
        address="1200 W Fake St",
        unit="1",
        lat=lat,
        lng=lng,
        beds=2.0,
        baths=1.0,
        sqft=1000.0,
        initial_ask=2400.0,
        effective_dom=30,
        censored=censored,
        removal_class=None if censored else removal_class,
        cohort_year=2026,
    )


def classify_one(
    comp: StitchedComp,
    *,
    weight: float = 1.0,
    distance: float = 0.0,
    filters: Filters | None = None,
    overrides: list[str] | None = None,
) -> str:
    """One comp through the classifier — the whole file's unit of work."""
    return classify_membership(
        [comp],
        ["k"],
        [weight],
        [distance],
        filters if filters is not None else Filters(),
        overrides or [],
    )[0]


# ---------------------------------------------------------------------------
# [DEFAULT] 1 — the max-distance boundary is `>`: exactly at the bound is KEPT
# ---------------------------------------------------------------------------


def test_a_comp_exactly_at_the_max_distance_is_kept() -> None:
    """The decision, stated as the one case that distinguishes `>` from `>=`.

    If this ever flips, it should flip here first and loudly, because nothing
    else in the suite can see it: QA's bounds sit >0.05mi clear of every comp
    in the fixture pull on purpose.
    """
    assert classify_one(make_comp(), distance=0.5, filters=Filters(max_distance_mi=0.5)) == (
        "included"
    ), "a comp exactly 0.5mi away is *within* a 0.5mi radius — the bound names the furthest comp the user still wants, not the first one they do not"


def test_a_comp_a_hair_beyond_the_max_distance_is_filtered() -> None:
    """The other side of the same boundary, so the test above cannot be
    satisfied by a filter that simply never fires."""
    assert (
        classify_one(make_comp(), distance=0.5000001, filters=Filters(max_distance_mi=0.5))
        == "filtered"
    )


def test_a_zero_radius_keeps_only_a_comp_at_the_subjects_own_coordinates() -> None:
    """``max_distance_mi=0.0`` is not a special case and is not "off".

    QA's ``EVERYTHING`` filter leans on this: a 0.0mi radius takes every real
    comp because no real comp shares the subject's coordinates — data, not a
    branch. A comp that *did* would be kept, and that is the honest answer.
    """
    assert classify_one(make_comp(), distance=0.0, filters=Filters(max_distance_mi=0.0)) == (
        "included"
    )
    assert classify_one(make_comp(), distance=0.001, filters=Filters(max_distance_mi=0.0)) == (
        "filtered"
    )


def test_no_max_distance_at_all_is_not_the_same_as_a_zero_one() -> None:
    """``None`` means "the user has not asked for a radius"; 0.0 means they
    asked for the tightest one there is. Collapsing the two would make an
    un-set filter silently take the whole pull."""
    assert classify_one(make_comp(), distance=99.0, filters=Filters(max_distance_mi=None)) == (
        "included"
    )


# ---------------------------------------------------------------------------
# [DEFAULT] 2 — the metric. Properties, not the formula.
# ---------------------------------------------------------------------------


def test_a_point_is_zero_miles_from_itself() -> None:
    assert haversine_miles(41.9, -87.68, 41.9, -87.68) == 0.0


def test_the_metric_is_symmetric() -> None:
    """Distance is a property of the pair, not of which one is the subject."""
    there = haversine_miles(41.83, -87.665, 41.9, -87.68)
    back = haversine_miles(41.9, -87.68, 41.83, -87.665)
    assert there == pytest.approx(back, rel=1e-12)


def test_the_metric_agrees_with_a_known_ground_distance() -> None:
    """A degree of latitude is ~69.05 statute miles everywhere on the globe;
    a degree of longitude at Chicago's latitude is that times cos(41.9°).

    This is the check that would catch a radians/degrees slip or a wrong Earth
    radius — the two ways this function can be wrong while still looking like a
    plausible distance.
    """
    assert haversine_miles(41.9, -87.68, 42.9, -87.68) == pytest.approx(69.05, abs=0.1)
    expected_lng_mi = 69.05 * math.cos(math.radians(41.9))
    assert haversine_miles(41.9, -87.68, 41.9, -86.68) == pytest.approx(expected_lng_mi, abs=0.15)


def test_distances_are_returned_one_per_comp_in_order() -> None:
    """`classify_membership` zips distances against comps with `strict=True`,
    so a metric that returned a different length would be a loud error — but
    one that returned them *reordered* would silently filter the wrong comps."""
    near = make_comp(lat=41.9, lng=-87.68)
    far = make_comp(lat=41.83, lng=-87.665)
    distances = distances_mi([near, far], SUBJECT)
    assert distances[0] == 0.0
    assert distances[1] > distances[0]


# ---------------------------------------------------------------------------
# [DEFAULT] 3 — precedence when a comp is both filtered out and weight-0
# ---------------------------------------------------------------------------


def test_a_filtered_comp_at_weight_zero_is_labelled_filtered_not_excluded() -> None:
    """The `[DEFAULT]` `membership.py` names and F7-S1 was invited to reverse.

    Kept as-is. Reversing it is a one-line change and this is the test that
    should have to change with it — the partition and every count hold either
    way, so without a pin the label could drift with nothing noticing.
    """
    assert (
        classify_one(make_comp(censored=True), weight=0.0, filters=Filters(hide_censored=True))
        == "filtered"
    )


def test_the_weight_survives_underneath_the_filtered_label() -> None:
    """The precedence is only defensible because the label is not destructive:
    clearing the filter must give the comp back at the weight the user chose,
    which is what makes "manual overrides survive filter resets" true for the
    plainest case of all — a weight nobody touched."""
    comp = make_comp(censored=True)
    assert classify_one(comp, weight=3.0, filters=Filters(hide_censored=True)) == "filtered"
    assert classify_one(comp, weight=3.0, filters=Filters()) == "included"


# ---------------------------------------------------------------------------
# Overrides — the two directions, at the lowest layer that can hold them
# ---------------------------------------------------------------------------


def test_an_override_un_filters_but_does_not_select() -> None:
    """F5-S2 [INVARIANT]: selection IS the weight. An override exempts a comp
    from the filters; a weight-0 comp named in one is `excluded`, never
    `included` — otherwise the override list would be a second, hidden channel
    into the analysis."""
    comp = make_comp(censored=True)
    filters = Filters(hide_censored=True)
    assert classify_one(comp, weight=0.0, filters=filters, overrides=["k"]) == "excluded"
    assert classify_one(comp, weight=1.0, filters=filters, overrides=["k"]) == "included"


def test_an_override_beats_all_three_filters_at_once() -> None:
    """An override that beat only one filter would look complete and would
    fail exactly when the user has the most filters on."""
    comp = make_comp(censored=True)
    everything_on = Filters(max_distance_mi=0.1, hide_censored=True, leased_only=True)
    assert classify_one(comp, distance=99.0, filters=everything_on) == "filtered"
    assert classify_one(comp, distance=99.0, filters=everything_on, overrides=["k"]) == "included"


def test_an_override_naming_a_comp_that_is_not_here_changes_nothing() -> None:
    """A saved workspace legitimately outlives the comps it names (F13-S1)."""
    assert (
        classify_one(make_comp(censored=True), filters=Filters(hide_censored=True), overrides=["z"])
        == "filtered"
    )


# ---------------------------------------------------------------------------
# Composition — filters are independent reasons, so they union
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (Filters(), "included"),
        (Filters(max_distance_mi=1.0), "filtered"),
        (Filters(hide_censored=True), "included"),
        (Filters(leased_only=True), "included"),
        (Filters(max_distance_mi=1.0, hide_censored=True, leased_only=True), "filtered"),
    ],
)
def test_one_matching_filter_is_enough(filters: Filters, expected: str) -> None:
    """A confirmed, non-censored comp 2mi away: only the distance rule matches
    it, and that alone must take it. An intersection would leave it visible
    with all three filters on, which is the failure mode that looks like the
    filters "stopped working" the moment you use more than one."""
    assert classify_one(make_comp(), distance=2.0, filters=filters) == expected


# ---------------------------------------------------------------------------
# The reconciliation [INVARIANT], as a property over arbitrary curation states
# ---------------------------------------------------------------------------


@settings(max_examples=200, deadline=None)
@given(
    flags=st.lists(
        st.tuples(
            st.booleans(),  # censored
            st.sampled_from([None, "pending", "provisional", "confirmed"]),
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False),  # weight
            st.floats(min_value=0.0, max_value=50.0, allow_nan=False),  # distance
        ),
        min_size=0,
        max_size=12,
    ),
    max_distance=st.one_of(st.none(), st.floats(min_value=0.0, max_value=50.0, allow_nan=False)),
    hide_censored=st.booleans(),
    leased_only=st.booleans(),
    override_count=st.integers(min_value=0, max_value=12),
)
def test_included_plus_excluded_plus_filtered_always_equals_pulled(
    flags, max_distance, hide_censored, leased_only, override_count
) -> None:
    """F7-S1 `[INVARIANT]`, at Layer 1 and over the whole input space.

    QA asserts this over the wire across 8 filter states x 3 weight shapes x 3
    override sets — 72 curation states, and it is right that the identity is
    asserted where a user can actually reach it. What this adds is the reason
    it can never fail: ``classify_membership`` appends exactly one label per
    comp, so the partition is a property of the function's shape rather than of
    the cases anyone thought to enumerate.

    That is also why there is no runtime ``assert`` in the pipeline. The PM's
    ruling is that the `[INVARIANT]` is the identity and not the mechanism, and
    a guard comparing ``len(states)`` to ``len(comps)`` would be re-checking
    that a loop ran — a statement about Python, not about this story — while
    adding a way for a legitimate request to 500. The property below is the
    honest form of the same claim.
    """
    comps = [
        make_comp(censored=censored, removal_class=removal_class)
        for censored, removal_class, _, _ in flags
    ]
    keys = [f"k{i}" for i in range(len(flags))]
    weights = [weight for _, _, weight, _ in flags]
    distances = [distance for _, _, _, distance in flags]

    states = classify_membership(
        comps,
        keys,
        weights,
        distances,
        Filters(
            max_distance_mi=max_distance,
            hide_censored=hide_censored,
            leased_only=leased_only,
        ),
        keys[:override_count],
    )

    counts = {label: states.count(label) for label in ("included", "excluded", "filtered")}
    assert sum(counts.values()) == len(comps), (
        f"included({counts['included']}) + excluded({counts['excluded']}) + "
        f"filtered({counts['filtered']}) != pulled({len(comps)})"
    )
    assert set(states) <= {"included", "excluded", "filtered"}


@settings(max_examples=200, deadline=None)
@given(
    weight=st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
    distance=st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
    max_distance=st.one_of(st.none(), st.floats(min_value=0.0, max_value=50.0, allow_nan=False)),
    hide_censored=st.booleans(),
    leased_only=st.booleans(),
    censored=st.booleans(),
    removal_class=st.sampled_from([None, "pending", "provisional", "confirmed"]),
)
def test_an_override_can_only_ever_move_a_comp_out_of_filtered(
    weight, distance, max_distance, hide_censored, leased_only, censored, removal_class
) -> None:
    """"Overrides survive filter resets", as the property underneath it.

    A reset is just the default `Filters`, so "survive" means an override is
    never *consumed* and never *costs* anything: for any curation state, adding
    the comp to `include_overrides` either changes nothing or turns `filtered`
    into whatever the weight alone says. If an override could ever move a comp
    *into* `filtered`, or flip `included` to `excluded`, then carrying the list
    through a reset would silently rewrite curation the user had already made.
    """
    comp = make_comp(censored=censored, removal_class=removal_class)
    filters = Filters(
        max_distance_mi=max_distance, hide_censored=hide_censored, leased_only=leased_only
    )
    without = classify_one(comp, weight=weight, distance=distance, filters=filters)
    with_override = classify_one(
        comp, weight=weight, distance=distance, filters=filters, overrides=["k"]
    )
    by_weight_alone = "included" if weight > 0.0 else "excluded"

    if without == "filtered":
        assert with_override == by_weight_alone
    else:
        assert with_override == without, "an override changed a comp no filter had touched"
