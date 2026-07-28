"""F11-S2 — the curve must actually reach `/api/derive` and displace the guard.

QA-authored, written RED before implementation. Layer 2 (pytest API contract).

======================================================================
SCOPE FLAG — READ BEFORE MERGING THIS FILE (raised to the PM in the handoff)
======================================================================
F11-S2's own AC is a pure-estimator AC: product-limit arithmetic, monotone,
S(0)=1, no runtime `lifelines`. It says nothing about `/api/derive`. This file
exists because the DISPATCH asked for the `curve_not_available` transition to
be pinned, and that is a wiring claim, not an estimator claim.

The wiring cannot land on F11-S2 alone: `CurveResult` (models/responses.py)
requires `horizons` AND `expected_vacancy`, and `expected_vacancy` is
**F11-S4's** formula and its "always state the truncation" invariant. So
returning a `CurveResult` at all forces F11-S4 (or at least its shape) into
this story.

THIS WHOLE FILE IS THEREFORE DELIBERATELY ISOLATED so the PM can defer it to
F11-S3/F11-S4 by deleting one path, with zero collateral on the Layer-1 specs.
Its assertions are written to survive either decision: nothing here pins
F11-S4's expected-vacancy NUMBER, only that it is present and internally
honest.

======================================================================
WHY `curve_not_available` IS THE THING TO PIN
======================================================================
WS-1a added a third `GuardResult.reason` literal, `curve_not_available`,
precisely as an honest placeholder: at a candidate near the real pull's own
anchor the evidence is NOT thin, so neither real F11-S3 trip condition holds,
but there was no `CurveResult` to return. Its own docstring in
`models/responses.py` says it "retires the moment F11-S2/F11-S3's
guard-vs-curve branch selection lands for real."

QA measured the real `ws1-real` pull across the candidate range before writing
this (probe run, not committed): at every rent from 0.80x to 1.20x the pull's
own anchor, retrieval returns 21 neighbours, 19-21 of them within +/-3 premium
points and 17-21 of those uncensored. This is genuinely well-evidenced data.
Every one of those rents returns `curve_not_available` today.

======================================================================
THE OWNER'S BAND RULING IS NOT CONTRADICTED HERE
======================================================================
"Guard if it trips at ANY of drift-2 / drift / drift+2" (QUEUE.md, binding).
This file never asserts that a specific rent MUST produce a curve — the guard
may legitimately win at a band edge that the mid-point response cannot show.
It asserts only the two things the ruling leaves untouched:

* `curve_not_available` must never be returned by anything, at any rent
  (it names a gap in the implementation, not a property of the evidence);
* the curve branch must be reachable at SOME rent on real data, or the
  estimator is dead code behind a guard that always wins.

======================================================================
EXPECTED RED STATE TODAY
======================================================================
Two loud failures naming two distinct missing things:

  * `test_curve_not_available_is_never_returned` — the transition itself.
  * `test_the_curve_branch_is_reachable_on_real_evidence` — the curve exists.

Every curve-shape test below skips behind the `curve` fixture until one is
found, so the red state stays readable.
"""

from __future__ import annotations

import pytest

#: The real, already-paid-for pull committed at the T-S3 gate (QUEUE.md row 6).
REAL_PULL = "ws1-real"

#: WS-1's own hardcoded thin-evidence candidate — chosen precisely so the
#: guard trips for a real reason. It must keep tripping after F11-S2.
THIN_EVIDENCE_RENT = 4500.0

#: Multipliers on the pull's own anchor. QA probed all of these against the
#: real fixtures: every one is well-evidenced (>= 17 uncensored neighbours
#: within +/-3 premium points).
WELL_EVIDENCED_MULTIPLIERS = (0.80, 0.90, 0.95, 1.00, 1.05, 1.10)

#: F0-S5's `km_horizons_days` default. Read from config in the test rather
#: than hardcoded where possible; this is the fallback expectation.
DEFAULT_HORIZONS = (14, 30, 45, 60)

IN_RANGE_DISTANCE = 0.03


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _anchor_mid(derive) -> float:
    response = derive(pull_ref=REAL_PULL, candidate_rent=None)
    assert response.status_code == 200, response.text[:600]
    anchor = response.json()["anchor"]
    assert anchor is not None, f"the {REAL_PULL} pull must produce an anchor"
    return anchor["rent"]["mid"]


def _price_test(derive, rent: float) -> dict:
    response = derive(pull_ref=REAL_PULL, candidate_rent=rent)
    assert response.status_code == 200, response.text[:600]
    price_test = response.json()["price_test"]
    assert price_test is not None, f"no price_test returned for candidate_rent={rent}"
    return price_test


def _sweep(derive) -> list[tuple[float, dict]]:
    mid = _anchor_mid(derive)
    return [
        (round(mid * m, 2), _price_test(derive, round(mid * m, 2)))
        for m in WELL_EVIDENCED_MULTIPLIERS
    ]


def _step(points: list[dict], day: float) -> float:
    """S(day) by walking the wire step function. Tolerates an implementation
    that prepends an explicit (day=0, survival=1.0) origin point and one that
    does not — that choice is the developer's, not an invariant."""
    survival = 1.0
    for point in sorted(points, key=lambda p: p["day"]):
        if point["day"] <= day:
            survival = point["survival"]
        else:
            break
    return survival


def _assert_curve_is_a_survival_curve(curve: dict, label: str) -> None:
    points = curve["points"]
    assert points, f"{label}: a CurveResult was returned with an empty curve"
    days = [p["day"] for p in points]
    assert days == sorted(set(days)), (
        f"{label}: curve days must be unique and ascending, got {days}"
    )
    previous = 1.0
    for point in points:
        assert 0.0 <= point["survival"] <= 1.0, (
            f"{label}: S({point['day']}) = {point['survival']} is not a probability"
        )
        assert point["survival"] <= previous + 1e-12, (
            f"{label}: survival rose to {point['survival']} at day {point['day']} "
            f"(previous {previous}) — a Kaplan-Meier curve is non-increasing"
        )
        previous = point["survival"]
    assert points[0]["survival"] <= 1.0


# ---------------------------------------------------------------------------
# The transition — the two loud failures
# ---------------------------------------------------------------------------


def test_curve_not_available_is_never_returned(derive) -> None:
    """THE test this file exists for.

    `curve_not_available` is not a statement about the evidence — it is a
    statement that F11-S2 does not exist. Once the estimator lands and the
    branch selects on real trip conditions, every response is either a curve or
    a guard tripped for one of F11-S3's two REAL reasons. A response still
    saying `curve_not_available` is the tool telling the user "I could have
    answered this and didn't."
    """
    offenders = []
    for rent, price_test in _sweep(derive):
        if price_test.get("reason") == "curve_not_available":
            neighbors = price_test["neighbors"]
            in_range = sum(1 for n in neighbors if n["distance"] <= IN_RANGE_DISTANCE)
            offenders.append(
                f"${rent}/mo -> curve_not_available ({len(neighbors)} neighbours, "
                f"{in_range} within +/-3 premium points)"
            )
    assert not offenders, (
        "the provisional WS-1a placeholder reason is still being returned on "
        "well-evidenced candidates:\n  " + "\n  ".join(offenders) + "\n"
        "Its own docstring in models/responses.py says it retires when F11-S2's "
        "curve branch lands. Every response must now be a CurveResult or a guard "
        "tripped for a real F11-S3 reason."
    )


def test_the_curve_branch_is_reachable_on_real_evidence(derive) -> None:
    """The estimator must not be dead code behind a guard that always wins.

    Deliberately does NOT demand a curve at one particular rent — the owner's
    band ruling (guard if it trips at ANY of drift-2/drift/drift+2) means a
    specific rent may honestly land on the guard even when its mid-point looks
    well-evidenced. It demands only that SOMEWHERE across a range QA measured
    as densely-evidenced on real data, the curve wins at least once.
    """
    states = {rent: price_test["state"] for rent, price_test in _sweep(derive)}
    assert "curve" in states.values(), (
        "no candidate rent across 0.80x-1.10x the real pull's own anchor produced "
        f"a CurveResult; got {states}. QA measured 17-21 uncensored neighbours "
        "within +/-3 premium points at every one of these rents — if the guard "
        "still wins everywhere, either the branch selection never consults the "
        "estimator or the guard is tripping for a reason the evidence does not "
        "support."
    )


# ---------------------------------------------------------------------------
# The guard side of the mutual exclusion — green today, pinned as regression
# ---------------------------------------------------------------------------


def test_thin_evidence_still_trips_the_guard_after_the_curve_exists(derive) -> None:
    """F11-S3's [INVARIANT] and the prototype failure it exists to kill: a
    candidate far above the evidence must render the guard, never a curve.
    Landing the estimator must not turn the guard off."""
    price_test = _price_test(derive, THIN_EVIDENCE_RENT)
    assert price_test["state"] == "insufficient_evidence", (
        f"${THIN_EVIDENCE_RENT}/mo produced state={price_test['state']!r}. This is "
        "the observed prototype failure the guard exists to kill (a curve rendered "
        "at +16.5% from mid-market comps) — F11-S3 [INVARIANT], NORTH_STAR."
    )
    assert price_test["reason"] in ("too_few_in_range", "all_censored")
    assert "curve" not in price_test, (
        "a guard response carries a curve field — guard-state and curve-state are "
        "mutually exclusive renders BY CONSTRUCTION (ADR-001's discriminated "
        "union), not by the view remembering to check"
    )


def test_a_response_is_a_curve_or_a_guard_and_never_both(derive) -> None:
    """The discriminated union holds over the whole real candidate range."""
    for rent, price_test in [(THIN_EVIDENCE_RENT, _price_test(derive, THIN_EVIDENCE_RENT))] + _sweep(derive):
        state = price_test["state"]
        assert state in ("curve", "insufficient_evidence"), f"${rent}: state={state!r}"
        if state == "curve":
            assert "reason" not in price_test, (
                f"${rent}: a CurveResult carries a guard `reason` — the two arms "
                "must not be confusable (F0-S2 pinned this)"
            )
            assert {"curve", "horizons", "expected_vacancy"} <= set(price_test)
        else:
            assert "curve" not in price_test and "horizons" not in price_test
            assert "expected_vacancy" not in price_test


# ---------------------------------------------------------------------------
# What the curve must look like once it exists
# ---------------------------------------------------------------------------


@pytest.fixture
def curve(derive):
    """The first `CurveResult` found across the well-evidenced sweep, or a
    legible skip. `test_the_curve_branch_is_reachable_on_real_evidence` above
    is the ONE loud failure for "no curve exists"; the tests below skip rather
    than repeat it."""
    for rent, price_test in _sweep(derive):
        if price_test["state"] == "curve":
            return rent, price_test
    pytest.skip("no CurveResult produced on real evidence yet (F11-S2 red)")


def test_every_band_edge_of_the_curve_is_a_valid_survival_curve(curve) -> None:
    """`CurveResult.curve` is a `Band[KMCurve]` — the curve is recomputed at
    drift-2 / drift / drift+2, because the candidate's PREMIUM moves with the
    anchor even though premium itself is drift-free. All three edges must be
    real curves; a band whose edge is malformed is not a band."""
    rent, price_test = curve
    for edge in ("low", "mid", "high"):
        assert edge in price_test["curve"], f"${rent}: curve band missing {edge!r}"
        _assert_curve_is_a_survival_curve(price_test["curve"][edge], f"${rent} {edge}")


def test_the_curve_never_counts_a_censored_neighbor_as_leased(curve) -> None:
    """NORTH_STAR's single most important distinction, asserted at the wire.

    Every step of the curve must sit on a day some returned, NON-censored
    neighbour actually leased. A step at a censored neighbour's floor DOM is
    the estimator counting "still vacant at 47 days" as "leased on day 47".

    Points carrying no event weight are exempt: an implementation may include
    an explicit origin or truncation marker, which asserts nothing about a
    lease.
    """
    rent, price_test = curve
    leased_days = {
        n["effective_dom"] for n in price_test["neighbors"] if not n["censored"]
    }
    censored_only_days = {
        n["effective_dom"] for n in price_test["neighbors"] if n["censored"]
    } - leased_days
    for edge in ("low", "mid", "high"):
        for point in price_test["curve"][edge]["points"]:
            if point["events"] == 0:
                continue
            assert point["day"] not in censored_only_days, (
                f"${rent} {edge}: the curve drops at day {point['day']}, which is "
                "only ever a CENSORED neighbour's floor DOM. A censored comp is "
                "still vacant as of the pull — it never leased."
            )
            assert point["day"] in leased_days, (
                f"${rent} {edge}: the curve has an event at day {point['day']}, but "
                "no returned non-censored neighbour has that effective_dom"
            )


def test_horizon_readouts_agree_with_the_curve_they_summarize(curve) -> None:
    """The horizon readouts (14/30/45/60) are the numbers the user actually
    reads, and they are a step-function lookup away from being off by one
    event. Pinning them AGAINST the curve in the same response catches exactly
    that: a readout that disagrees with its own curve is wrong no matter which
    of the two is right."""
    rent, price_test = curve
    horizons = price_test["horizons"]
    assert horizons, f"${rent}: a CurveResult with no horizon readouts"
    days = [h["day"] for h in horizons]
    assert days == sorted(set(days)), f"${rent}: horizon days {days} not ascending"
    assert set(days) >= set(DEFAULT_HORIZONS), (
        f"${rent}: horizons {days} do not cover F0-S5's km_horizons_days default "
        f"{list(DEFAULT_HORIZONS)}"
    )
    for readout in horizons:
        for edge in ("low", "mid", "high"):
            expected = _step(price_test["curve"][edge]["points"], readout["day"])
            assert readout["survival"][edge] == pytest.approx(expected, rel=1e-9, abs=1e-12), (
                f"${rent} {edge}: the day-{readout['day']} readout says "
                f"{readout['survival'][edge]!r} but stepping that same edge's own "
                f"curve gives {expected!r}"
            )


def test_horizon_survival_is_non_increasing_across_horizons(curve) -> None:
    rent, price_test = curve
    for edge in ("low", "mid", "high"):
        values = [h["survival"][edge] for h in sorted(price_test["horizons"], key=lambda h: h["day"])]
        for earlier, later in zip(values, values[1:], strict=False):
            assert later <= earlier + 1e-12, (
                f"${rent} {edge}: horizon survival rose from {earlier} to {later}"
            )


def test_the_curve_states_where_it_stops_being_informative(curve) -> None:
    """NORTH_STAR: expected vacancy is "an expected value under a TRUNCATED
    empirical curve, always stated with its truncation point" — never a
    guarantee and never valid beyond the observation window.

    F11-S4 owns the truncation FORMULA and the expected-vacancy NUMBER; neither
    is pinned here. What is pinned is that the honesty statement is present and
    self-consistent: the area under a curve bounded by 1 over [0, T] cannot
    exceed T.
    """
    rent, price_test = curve
    for edge in ("low", "mid", "high"):
        assert price_test["curve"][edge]["truncated_at"] is not None, (
            f"${rent} {edge}: a curve was rendered without stating where it stops "
            "being informative"
        )

    vacancy = price_test["expected_vacancy"]
    truncated_at = vacancy["truncated_at"]
    assert truncated_at is not None and truncated_at > 0
    for edge in ("low", "mid", "high"):
        days = vacancy["days"][edge]
        assert 0.0 <= days <= truncated_at + 1e-9, (
            f"${rent} {edge}: expected vacancy of {days} days under a curve "
            f"truncated at {truncated_at} — the area under a step function "
            "bounded by 1 over [0, T] cannot exceed T"
        )
        assert vacancy["cost"][edge] >= 0.0
