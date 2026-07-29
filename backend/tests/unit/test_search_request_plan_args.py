"""F2-S1 — Layer 1 for `SearchRequest.plan_args()` (PM ruling 3).

Developer-authored. QA pins the *consequence* of trimming at Layer 2 (the
preview and the submit resolve an untrimmed body to one `pull_ref`) and pins
the thing that must NOT change (`plan_pull_queries` still refuses `"06-01 "`).
What is left, and what belongs down here, is the translation itself: it is a
pure function of one value object, so every case it has is assertable in
microseconds without an app, a route or a temp home.

The rule it implements, stated once: **trim whitespace, repair nothing.** A
space the user cannot see is not a different search; a month-day they got wrong
is still wrong after trimming, and must still reach the planner and raise.
"""

from __future__ import annotations

import pytest

from rentcomp.models.requests import SearchRequest

VALID = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": "1",
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start": "06-15",
    "window_end": "06-30",
}


def request(**overrides: object) -> SearchRequest:
    return SearchRequest(**{**VALID, **overrides})


def test_plan_args_speaks_the_planners_argument_names() -> None:
    """`plan_pull_queries` takes `window_start_mmdd`; the wire says
    `window_start`. This mapping is the only place that rename lives, so both
    routes can call the planner with `**args` and neither can get it half
    right."""
    args = request().plan_args()
    assert set(args) == {
        "address",
        "radius",
        "bedrooms",
        "bathrooms",
        "property_types",
        "years_back",
        "window_start_mmdd",
        "window_end_mmdd",
    }


@pytest.mark.parametrize(
    ("field", "raw", "planner_key", "expected"),
    [
        ("address", "  3651 S Wood St  ", "address", "3651 S Wood St"),
        ("bedrooms", " 3:4 ", "bedrooms", "3:4"),
        ("bathrooms", "\t1.5\n", "bathrooms", "1.5"),
        ("window_start", " 06-15", "window_start_mmdd", "06-15"),
        ("window_end", "06-30 ", "window_end_mmdd", "06-30"),
    ],
)
def test_every_string_the_planner_or_the_cache_key_reads_is_trimmed(
    field: str, raw: str, planner_key: str, expected: str
) -> None:
    """Two different damages, one fix. `plan_pull_queries` rejects `"06-01 "`
    outright, and F3-S1's cache key is a hash of these exact strings — so an
    untrimmed address makes the user pay twice for one search."""
    assert request(**{field: raw}).plan_args()[planner_key] == expected


def test_property_types_are_trimmed_elementwise() -> None:
    """A multi-select posted from a form can pad any element. `propertyType`
    goes on the wire pipe-separated, so `" Townhouse"` would ask RentCast for a
    type that does not exist and silently narrow the pull."""
    args = request(property_types=[" Multi-Family", "Apartment ", " Townhouse "]).plan_args()
    assert args["property_types"] == ["Multi-Family", "Apartment", "Townhouse"]


@pytest.mark.parametrize("blank", [None, "", "   ", "\t\n"])
def test_a_blank_bathrooms_means_no_filter_not_an_empty_one(blank: str | None) -> None:
    """`None` and `""` are NOT interchangeable on this endpoint.

    T-S3 round 2 (paid for with live calls) found that sending an *empty*
    bathrooms filter makes RentCast exclude records whose bathroom count is
    missing — a silent narrowing of the comp set that looks like a thin market.
    `build_listing_params` omits the param entirely for `None`, so a
    whitespace-only field has to arrive as `None`.
    """
    assert request(bathrooms=blank).plan_args()["bathrooms"] is None


@pytest.mark.parametrize("bad", ["13-01", "02-30", "6-1x", "", "  "])
def test_trimming_is_not_laundering(bad: str) -> None:
    """The other half of the rule, and the reason this is not just `.strip()`
    everywhere and forget it.

    A month-day the user actually got wrong must survive this function intact
    so the planner can refuse it and the route can turn that into a 422 the
    form shows inline. Quietly coercing `"02-30"` to something valid would move
    the window the user asked for — which is precisely why `_parse_mmdd`
    refuses it rather than clamping.
    """
    from rentcomp.client.planner import plan_pull_queries
    from datetime import date

    args = request(window_start=bad).plan_args()
    assert args["window_start_mmdd"] == bad.strip()
    with pytest.raises(ValueError):
        plan_pull_queries(date(2026, 7, 28), **args)


def test_force_refresh_is_not_part_of_the_search() -> None:
    """It is consent to re-spend, not a search parameter.

    If it leaked into `plan_args` it would reach `pull_ref_for` and give the
    REFRESH click its own cache key — so a refresh would write beside the
    entry it was meant to replace, and the user would pay for a whole second
    copy of evidence they already own (F3-S1).
    """
    assert "force_refresh" not in request(force_refresh=True).plan_args()
    assert (
        request(force_refresh=True).plan_args() == request(force_refresh=False).plan_args()
    )
