"""F4-S5 [INVARIANT] — premium computation and the thin-cohort fallback.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Layer 2 (D21): every assertion below is "given curation state X,
`POST /api/derive` returns Y" — a number, a flag, or a relationship between
two payload fields. Nothing here needs a browser, and nothing here imports a
pipeline internal: `compute_premiums`' and `cohort_medians`' signatures must
change for this story (they have no way to express "fall back to the pulled
set" today), so a test written against those signatures would pin the
implementation this story is about to replace. The endpoint's observable
behaviour is what is pinned; any internal shape that produces it passes.

THE STORY, AS FIVE SEPARATE OBLIGATIONS
---------------------------------------
1. `premium = initial $/sqft ÷ cohort weighted median $/sqft − 1`.
2. The median is taken over the **selected** comps in that cohort.
3. When the selected cohort count is **< min_cohort_size**, the median falls
   back to **all pulled comps** in that cohort.
4. The fallback sets **a flag the UI must surface** (F8-S3 consumes it).
5. Missing-sqft comps are flagged, default to weight 0, and are excluded from
   **every** median — including the fallback's.

THE BOUNDARY IS WALKED DELIBERATELY, NOT INHERITED
--------------------------------------------------
F4-S3's 42-day floor was covered only incidentally by a golden-file group and
F4-S8 showed that inherited coverage is worth nothing — a real pull's shape
could not structurally see an off-by-one at either edge. So cohort 2025 in
`fixtures/synthetic/pulls/f4s5-cohorts.json` carries five comps at psf
1.00–5.00, chosen so that the three arms of the boundary return three
distinguishable medians:

    5 selected (above)   → selected median 3.00
    4 selected (exactly) → selected median 2.00      ← `<=` instead of `<` fails here
    3 selected (below)   → PULLED median 3.00        ← no fallback at all fails here

and the minimum itself is read from the `min_cohort_size` knob (§2.3, range
2–10), not from a literal 4 — pinned in both directions by moving the knob
under a fixed selection.

THE SOURCE-TREE GUARD IS STRUCTURAL, NOT A COMMENT
--------------------------------------------------
`storage/pulls.py::fixture_pulls_dir()` resolves the synthetic-pulls directory
from the *loaded package's* `__file__`. `f4s5-cohorts.json` exists only on this
branch, so a run that loaded some other checkout's `rentcomp` cannot find it
and `test_the_f4s5_fixture_pull_resolves` fails loudly naming the directory it
looked in (WORKFLOW.md §2's `PYTHONPATH`/`.pth` trap, in the one form that
cannot pass by accident). It is a failing precondition rather than a skip
guard on purpose: a skip that exits 0 is indistinguishable from a pass.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from rentcomp.pipeline.keys import comp_key

PULL_REF = "f4s5-cohorts"

TOL = 1e-9

#: Cohort 2025 — the boundary cohort, psf 1.00 … 5.00.
BOUNDARY_2025 = {psf: comp_key(f"{psf}0 W Boundary St", None) for psf in (1, 2, 3, 4, 5)}
#: Cohort 2024 — three comps, psf 1.00/2.00/3.00. Always thin at the default
#: minimum, so it is where an *excluded* comp's presence in the fallback set is
#: decisive.
EXCLUDED_2024 = {psf: comp_key(f"{psf}0 W Excluded Ave", None) for psf in (1, 2, 3)}
#: Cohort 2023 — same shape; the psf-3.00 comp sits ~6.9 miles from the
#: subject, so a distance filter can remove it from the *selected* set without
#: removing it from the *pulled* one.
FILTERED_2023 = {psf: comp_key(f"{psf}0 W Filtered Rd", None) for psf in (1, 2, 3)}
#: Cohort 2022 — two comps with a psf (1.00/3.00) plus one with no
#: `squareFootage` at all, whose $10,000 ask makes any leak into a median loud.
NOSQFT_KEY = comp_key("30 W Nosqft Pl", None)
#: Cohort 2021 — exactly one comp (QUEUE row 11a's structural zero).
LONELY_2021 = comp_key("10 W Lonely Ct", None)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


@pytest.fixture
def f4s5_body(make_body):
    """The canonical derive body, pointed at this story's fixture pull."""

    def _body(**overrides: Any) -> dict[str, Any]:
        return make_body(pull_ref=PULL_REF, **overrides)

    return _body


@pytest.fixture
def f4s5(derive, f4s5_body):
    """`f4s5(**overrides) -> parsed DerivedState`, with a loud 200 assertion."""

    def _derive(**overrides: Any) -> dict[str, Any]:
        response = derive(f4s5_body(**overrides))
        assert response.status_code == 200, (
            f"POST /api/derive against {PULL_REF!r} returned {response.status_code}: "
            f"{response.text[:600]}"
        )
        return response.json()

    return _derive


@pytest.fixture
def set_min_cohort_size(rentcomp_home, clear_caches):
    """Write a `min_cohort_size` into this test's RENTCOMP_HOME config.

    F0-S5's load-bearing clause is "consumed only via the derivation graph so a
    knob change re-derives like any other input" — which is only *checkable*
    from out here if a test can move the knob and watch the payload move.
    """

    def _set(value: int) -> None:
        (rentcomp_home / "config.json").write_text(
            json.dumps({"min_cohort_size": value}), encoding="utf-8"
        )
        clear_caches()

    return _set


def cohort(derived: dict[str, Any], year: int) -> dict[str, Any]:
    for stat in derived["cohorts"]:
        if stat["year"] == year:
            return stat
    raise AssertionError(
        f"no cohort {year} in the response — cohorts present: "
        f"{[c['year'] for c in derived['cohorts']]}"
    )


def comp(derived: dict[str, Any], key: str) -> dict[str, Any]:
    for row in derived["comps"]:
        if row["key"] == key:
            return row
    raise AssertionError(
        f"no comp keyed {key!r} in the response — keys present: "
        f"{[c['key'] for c in derived['comps']]}"
    )


def zero_out(*keys: str) -> dict[str, float]:
    """Weights that exclude exactly `keys` (toggle-off ≡ weight 0, F5-S2)."""
    return {key: 0.0 for key in keys}


# --------------------------------------------------------------------------
# preconditions
# --------------------------------------------------------------------------


def test_the_f4s5_fixture_pull_resolves(derive, f4s5_body) -> None:
    """A failing precondition, never a skip (WORKFLOW.md §2).

    Doubles as this suite's source-tree guard: the fixture exists only on this
    branch and is found through the loaded package's own `__file__`, so a 404
    here means the run loaded a `rentcomp` from somewhere else.
    """
    import rentcomp.storage.pulls as pulls_module

    response = derive(f4s5_body())
    assert response.status_code == 200, (
        f"POST /api/derive could not resolve pull_ref={PULL_REF!r} "
        f"({response.status_code}: {response.text[:300]}).\n"
        f"  loaded rentcomp.storage.pulls: {pulls_module.__file__}\n"
        f"  synthetic pulls dir it searched: {pulls_module.fixture_pulls_dir()}\n"
        f"If that directory is not this worktree's, the run is testing another "
        f"checkout's source (PYTHONPATH must be <worktree>/backend/src, ';'-separated "
        f"on Windows)."
    )
    payload = response.json()
    years = sorted({c["year"] for c in payload["cohorts"]})
    assert years == [2021, 2022, 2023, 2024, 2025], (
        f"the f4s5-cohorts fixture must shape into exactly five cohorts; got {years}"
    )


# --------------------------------------------------------------------------
# obligation 1 — the formula itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "weights",
    [
        pytest.param({}, id="all-selected"),
        pytest.param(zero_out(BOUNDARY_2025[4], BOUNDARY_2025[5]), id="2025-thinned-to-3"),
    ],
)
def test_premium_is_psf_over_its_own_cohort_median_minus_one(f4s5, weights) -> None:
    """[INVARIANT] `premium = psf ÷ cohort median psf − 1`, for every comp,
    under both bases.

    Basis-agnostic on purpose: whichever set the median came from, the comp's
    own premium must be *that* median divided into *its own* $/sqft. A comp
    measured against another cohort's median, or against a median from a
    different set than its own `premium_basis` claims, is caught here.
    """
    derived = f4s5(weights=weights)
    checked = 0
    for row in derived["comps"]:
        if row["psf"] is None:
            continue
        median = cohort(derived, row["cohort_year"])["median_psf"]
        if median is None:
            assert row["premium"] is None, (
                f"{row['key']}: cohort {row['cohort_year']} has no median, so premium "
                f"must be null — never a fabricated number ({row['premium']})"
            )
            continue
        expected = row["psf"] / median - 1.0
        assert row["premium"] == pytest.approx(expected, abs=TOL), (
            f"{row['key']}: premium must be psf/{median} − 1 = {expected}, "
            f"got {row['premium']} (psf {row['psf']}, cohort {row['cohort_year']})"
        )
        checked += 1
    assert checked >= 10, f"expected to check >=10 real premiums, checked {checked}"


def test_the_cohort_median_is_always_an_observed_comps_own_psf(f4s5) -> None:
    """NORTH_STAR / Semantic Change Protocol: the divisor is real evidence.

    Every cohort median must be *equal to some comp in that cohort's own
    $/sqft* — which is true by construction for the locked (non-interpolating,
    lower) weighted median, and false the moment the divisor becomes anything
    else: an AVM figure, a mean, an interpolated quantile, or a subject-derived
    estimate. This is the test that would have to be deleted, not merely
    edited, to swap in a model's opinion for the cohort's own evidence.
    """
    derived = f4s5(weights=zero_out(BOUNDARY_2025[4], BOUNDARY_2025[5]))
    for stat in derived["cohorts"]:
        if stat["median_psf"] is None:
            continue
        observed = {
            row["psf"]
            for row in derived["comps"]
            if row["cohort_year"] == stat["year"] and row["psf"] is not None
        }
        assert any(abs(stat["median_psf"] - psf) < TOL for psf in observed), (
            f"cohort {stat['year']}'s median {stat['median_psf']} is not any comp's "
            f"observed $/sqft {sorted(observed)} — the median must be a real listed "
            f"comp's own number (NORTH_STAR: never a vendor estimate, never an "
            f"interpolation)"
        )


# --------------------------------------------------------------------------
# obligations 2 + 3 — the min-cohort boundary, walked
# --------------------------------------------------------------------------


def test_five_selected_above_the_minimum_uses_the_selected_median(f4s5) -> None:
    """ABOVE the boundary (5 selected, min 4): the selected median, no fallback."""
    derived = f4s5()
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 5 and stat["pulled_count"] == 5
    assert stat["median_psf"] == pytest.approx(3.0, abs=TOL), (
        f"selected {{1,2,3,4,5}} → lower weighted median 3.00, got {stat['median_psf']}"
    )
    assert stat["basis"] == "selected", (
        f"a cohort at or above min_cohort_size must NOT fall back; basis={stat['basis']}"
    )
    assert stat["thin"] is False


def test_exactly_four_selected_is_still_the_selected_median(f4s5) -> None:
    """EXACTLY ON the boundary (4 selected, min 4): still selected.

    The story says "< min", so 4 is *not* thin. This is the arm that catches
    `<=`: a `<=` implementation falls back here and returns the pulled median
    3.00 instead of the selected median 2.00.
    """
    derived = f4s5(weights=zero_out(BOUNDARY_2025[5]))
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 4, (
        f"precondition: exactly four comps selected, got {stat['selected_count']}"
    )
    assert stat["basis"] == "selected", (
        "selected_count == min_cohort_size (4) is NOT below the minimum — the fallback "
        f"must not fire (basis={stat['basis']}); a '<=' comparison fails exactly here"
    )
    assert stat["thin"] is False
    assert stat["median_psf"] == pytest.approx(2.0, abs=TOL), (
        f"selected {{1,2,3,4}} → lower weighted median 2.00, got {stat['median_psf']} "
        f"(3.00 means the pulled set was used)"
    )
    assert comp(derived, BOUNDARY_2025[1])["premium"] == pytest.approx(1.0 / 2.0 - 1.0, abs=TOL)


def test_three_selected_below_the_minimum_falls_back_to_the_pulled_median(f4s5) -> None:
    """BELOW the boundary (3 selected, min 4): the *pulled* median.

    The expected value 3.00 is reachable only from the five pulled comps —
    a selected-only median of {1,2,3} is 2.00 — so this arm cannot pass
    without the fallback actually running.
    """
    derived = f4s5(weights=zero_out(BOUNDARY_2025[4], BOUNDARY_2025[5]))
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 3 and stat["pulled_count"] == 5
    assert stat["basis"] == "pulled", (
        f"selected_count 3 < min_cohort_size 4 must fall back to the pulled set; "
        f"basis={stat['basis']}"
    )
    assert stat["thin"] is True
    assert stat["median_psf"] == pytest.approx(3.0, abs=TOL), (
        f"the fallback median is over all five PULLED comps {{1,2,3,4,5}} → 3.00; "
        f"got {stat['median_psf']} (2.00 means only the three selected were used)"
    )
    assert comp(derived, BOUNDARY_2025[1])["premium"] == pytest.approx(1.0 / 3.0 - 1.0, abs=TOL)


def test_zero_selected_still_falls_back_rather_than_losing_the_premium(f4s5) -> None:
    """0 selected is below the minimum too — the literal reading of "< min".

    Deliberately pinned: 0 is the one count where "fall back to all pulled
    comps" and "there is no evidence, return null" could both look reasonable,
    and the story draws no line at 0. The rows are still on screen and still
    show a premium; the flag is what says the median behind them is the pulled
    set. (Flagged to the PM as a reading, not an inference.)
    """
    derived = f4s5(weights=zero_out(*BOUNDARY_2025.values()))
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 0 and stat["pulled_count"] == 5
    assert stat["basis"] == "pulled", (
        "0 selected is < min_cohort_size, so the fallback fires like any other "
        f"below-minimum count; basis={stat['basis']}"
    )
    assert stat["median_psf"] == pytest.approx(3.0, abs=TOL)
    assert comp(derived, BOUNDARY_2025[5])["premium"] == pytest.approx(5.0 / 3.0 - 1.0, abs=TOL)


def test_the_fallback_set_includes_comps_the_user_excluded(f4s5) -> None:
    """"All *pulled* comps" means all of them — including the ones curated out.

    This is the arm that separates a real fallback from a no-op: if the pulled
    set were re-weighted by the user's effective weights, every excluded comp
    would carry weight 0 and the "fallback" would return the selected median it
    just replaced. Cohort 2024 selected-only = 1.00; pulled = 2.00.
    """
    derived = f4s5(weights=zero_out(EXCLUDED_2024[3]))
    stat = cohort(derived, 2024)
    assert stat["selected_count"] == 2 and stat["pulled_count"] == 3
    assert stat["basis"] == "pulled"
    assert stat["median_psf"] == pytest.approx(2.0, abs=TOL), (
        f"the fallback is over all three pulled comps {{1,2,3}} → 2.00, including the "
        f"one the user excluded; got {stat['median_psf']} (1.00 means the excluded comp "
        f"was dropped from the fallback set too, which makes the fallback a no-op)"
    )


def test_the_fallback_set_includes_comps_a_filter_removed(f4s5) -> None:
    """A filtered comp is pulled evidence as well — it was re-labelled, not
    removed (F7-S1: a filter never takes a comp out of the payload).

    Cohort 2023's psf-3.00 comp sits ~6.9mi away; with `max_distance_mi=1.0`
    the selected set is {1.00, 2.00} (median 1.00) and the pulled set is still
    all three (median 2.00).
    """
    derived = f4s5(
        filters={"max_distance_mi": 1.0, "hide_censored": False, "leased_only": False}
    )
    assert comp(derived, FILTERED_2023[3])["state"] == "filtered", (
        "precondition: the far comp must be filtered out by max_distance_mi=1.0"
    )
    stat = cohort(derived, 2023)
    assert stat["selected_count"] == 2 and stat["pulled_count"] == 3, (
        f"a filtered comp stays *pulled* evidence: pulled_count must be 3, got "
        f"{stat['pulled_count']}"
    )
    assert stat["basis"] == "pulled"
    assert stat["median_psf"] == pytest.approx(2.0, abs=TOL), (
        f"the fallback median is over all three pulled comps → 2.00, got "
        f"{stat['median_psf']}"
    )


# --------------------------------------------------------------------------
# obligation 3b — the minimum is the knob, not a literal 4
# --------------------------------------------------------------------------


def test_lowering_the_knob_stops_the_fallback_at_the_same_selection(
    f4s5, set_min_cohort_size
) -> None:
    """min_cohort_size=2: three selected is now ABOVE the minimum → no fallback."""
    set_min_cohort_size(2)
    derived = f4s5(weights=zero_out(BOUNDARY_2025[4], BOUNDARY_2025[5]))
    assert derived["meta"]["config"]["min_cohort_size"] == 2, (
        "precondition: the derive must have run under the knob this test wrote"
    )
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 3
    assert stat["basis"] == "selected", (
        f"3 selected >= min_cohort_size 2 — no fallback; basis={stat['basis']} means the "
        f"minimum was hardcoded rather than read from config"
    )
    assert stat["median_psf"] == pytest.approx(2.0, abs=TOL)


def test_raising_the_knob_starts_the_fallback_at_the_same_selection(
    f4s5, set_min_cohort_size
) -> None:
    """min_cohort_size=6: even five selected is now BELOW the minimum.

    The median is 3.00 either way here — the *flag* is the whole assertion,
    which is the point: the fallback fired, and the payload says so.
    """
    set_min_cohort_size(6)
    derived = f4s5()
    assert derived["meta"]["config"]["min_cohort_size"] == 6
    stat = cohort(derived, 2025)
    assert stat["selected_count"] == 5
    assert stat["thin"] is True
    assert stat["basis"] == "pulled", (
        f"5 selected < min_cohort_size 6 — the fallback must fire; basis={stat['basis']}"
    )
    assert comp(derived, BOUNDARY_2025[1])["premium_basis"] == "pulled"


# --------------------------------------------------------------------------
# obligation 4 — the flag, as a reachable obligation
# --------------------------------------------------------------------------


def test_the_fallback_flag_flips_as_comps_are_toggled_across_the_threshold(f4s5) -> None:
    """The story's own AC, as one sequence: 5 → 4 → 3 → 4 selected.

    Both directions, because a flag that only ever latches on would pass a
    one-way test and would leave F8-S3's rail warning permanently lit.
    """
    seen = []
    for dropped in ([], [BOUNDARY_2025[5]], [BOUNDARY_2025[5], BOUNDARY_2025[4]], [BOUNDARY_2025[5]]):
        stat = cohort(f4s5(weights=zero_out(*dropped)), 2025)
        seen.append((stat["selected_count"], stat["basis"], stat["thin"]))
    assert seen == [
        (5, "selected", False),
        (4, "selected", False),
        (3, "pulled", True),
        (4, "selected", False),
    ], f"basis/thin must track the selection across the threshold in both directions: {seen}"


@pytest.mark.parametrize(
    "weights",
    [
        # The MIXED state is the load-bearing one: with 2025 above the minimum
        # and 2021-2024 below it, a comp-level flag that is really one global
        # "something fell back" boolean disagrees with its own cohort here and
        # nowhere else. (Added after a surviving mutant did exactly that — see
        # the handoff's mutation report.)
        pytest.param({}, id="mixed-bases"),
        pytest.param(zero_out(BOUNDARY_2025[4], BOUNDARY_2025[5]), id="all-pulled"),
    ],
)
def test_every_comps_premium_basis_agrees_with_its_own_cohorts_basis(f4s5, weights) -> None:
    """The comp-level flag and the cohort-level flag are one fact.

    F8-S3 reads the rail warning off the cohorts; a comp row reads its own
    `premium_basis`. If those two could disagree the UI would be able to show a
    premium marked "selected" inside a cohort marked "pulled" — a number
    labelled with a provenance it does not have.
    """
    derived = f4s5(weights=weights)
    for row in derived["comps"]:
        stat = cohort(derived, row["cohort_year"])
        expected = stat["basis"] if row["premium"] is not None else None
        assert row["premium_basis"] == expected, (
            f"{row['key']}: premium_basis {row['premium_basis']!r} disagrees with its "
            f"cohort {row['cohort_year']}'s basis {stat['basis']!r}"
        )


def test_one_response_carries_both_bases_at_once(f4s5) -> None:
    """The flag is per cohort, not per response.

    With five comps selected in 2025 and the thin 2021–2024 cohorts untouched,
    one payload must carry a "selected" basis and a "pulled" basis
    simultaneously — a single global "the fallback happened" boolean cannot
    express this, and would send F8-S3's warning to every cohort on screen.
    """
    derived = f4s5()
    bases = {stat["year"]: stat["basis"] for stat in derived["cohorts"]}
    assert bases[2025] == "selected", bases
    assert {bases[year] for year in (2021, 2022, 2023, 2024)} == {"pulled"}, (
        f"every cohort with fewer than four selected comps must report the pulled "
        f"basis: {bases}"
    )


def test_premium_basis_is_never_null_where_a_premium_exists(f4s5) -> None:
    """No half-state: a number on screen always says which set it came from."""
    derived = f4s5()
    unlabelled = [
        row["key"] for row in derived["comps"]
        if row["premium"] is not None and row["premium_basis"] is None
    ]
    assert unlabelled == [], (
        f"these comps carry a premium with no basis: {unlabelled}"
    )


def test_the_basis_flag_is_declared_in_the_served_api_schema(app) -> None:
    """Reachable, not merely set (the F0-S4 half-AC shape).

    D12 codegens `frontend/src/api/schema.d.ts` from this document, and F8-S3's
    rail is written against those types. A fallback recorded only in a server
    log, or on a field the schema does not declare, is a flag the UI cannot
    surface — which is the half of the AC that is easiest to ship without.
    """
    schemas = app.openapi()["components"]["schemas"]
    cohort_basis = json.dumps(schemas["CohortStat"]["properties"]["basis"])
    comp_basis = json.dumps(schemas["DerivedComp"]["properties"]["premium_basis"])
    assert "pulled" in cohort_basis, (
        f"CohortStat.basis must be able to say 'pulled' on the wire: {cohort_basis}"
    )
    assert "pulled" in comp_basis, (
        f"DerivedComp.premium_basis must be able to say 'pulled' on the wire: {comp_basis}"
    )
    assert "thin" in schemas["CohortStat"]["properties"], (
        "CohortStat.thin is the count-based half of F8-S3's warning and must stay declared"
    )


# --------------------------------------------------------------------------
# obligation 5 — missing sqft is out of every median, fallback included
# --------------------------------------------------------------------------


def test_missing_sqft_comps_are_excluded_from_the_fallback_median_too(f4s5) -> None:
    """"Excluded from every median" includes the pulled-set one.

    Cohort 2022's no-sqft comp asks $10,000 — an order of magnitude off its
    cohort — so a leak into the fallback median is loud rather than subtle.
    Its explicit positive weight is the sharper half: an *explicit* weight wins
    over the no-sqft default (weights.py), so the comp is `included` and still
    has no $/sqft to contribute. Intent is echoed back; evidence is not
    invented.
    """
    derived = f4s5(weights={NOSQFT_KEY: 5.0})
    row = comp(derived, NOSQFT_KEY)
    assert row["weight"] == pytest.approx(5.0), "an explicit weight must be echoed back"
    stat = cohort(derived, 2022)
    assert stat["pulled_count"] == 2, (
        f"the no-sqft comp is not evidence for a $/sqft median in either set: "
        f"pulled_count must be 2, got {stat['pulled_count']}"
    )
    assert stat["median_psf"] == pytest.approx(1.0, abs=TOL), (
        f"the fallback median is over {{1.00, 3.00}} → 1.00; got {stat['median_psf']}"
    )
    assert NOSQFT_KEY not in stat["comp_keys"]


def test_a_missing_sqft_comp_has_no_premium_and_no_basis(f4s5) -> None:
    """Flagged and null, never a fabricated 0.0 (which reads as "priced exactly
    at market" — a claim about a comp whose size we do not know)."""
    derived = f4s5()
    row = comp(derived, NOSQFT_KEY)
    assert row["sqft"] is None and row["psf"] is None
    assert row["weight"] == pytest.approx(0.0), "missing-sqft comps default to weight 0"
    assert row["premium"] is None, f"expected a null premium, got {row['premium']}"
    assert row["premium_basis"] is None, (
        f"a comp with no premium has no basis either, got {row['premium_basis']!r}"
    )
    assert NOSQFT_KEY in derived["breakdown"]["comp_keys"]["missing_sqft"], (
        "the comp must still be one click away from the missing-sqft count (T-S2)"
    )


# --------------------------------------------------------------------------
# the AC line — reactivity
# --------------------------------------------------------------------------


def test_premiums_are_recomputed_when_selection_changes(f4s5) -> None:
    """The story's first AC. Premiums depend on cohort medians, and cohort
    medians depend on the selection — so a premium that survived a selection
    change unchanged would be evidence that this stage was memoized into the
    once-per-pull group (ADR-001 §2.1's one genuine conflict, and its ruling).
    """
    before = comp(f4s5(), BOUNDARY_2025[1])["premium"]
    after = comp(f4s5(weights=zero_out(BOUNDARY_2025[5])), BOUNDARY_2025[1])["premium"]
    assert before == pytest.approx(1.0 / 3.0 - 1.0, abs=TOL), before
    assert after == pytest.approx(1.0 / 2.0 - 1.0, abs=TOL), after
    assert before != after, (
        "excluding one comp changed cohort 2025's median from 3.00 to 2.00, so every "
        "premium in that cohort must move with it"
    )


# --------------------------------------------------------------------------
# QUEUE row 11a — legibility only. No answer is locked here.
# --------------------------------------------------------------------------


def test_a_lone_comps_premium_never_travels_without_the_counts_that_explain_it(
    f4s5,
) -> None:
    """A one-comp cohort's weighted median *is* that comp, so its premium is
    zero by construction rather than by measurement (QUEUE row 11a).

    This story does not decide what to do about that — bounding the cohort year
    is nobody's AC yet — so nothing here asserts the premium's value in either
    direction. What it does pin is that the number can never reach a consumer
    naked: whatever premium a one-comp cohort carries, the same payload states
    the cohort is thin and says how little evidence is behind it. Written as an
    implication so a later story that drops such cohorts entirely, or bounds
    the year, does not have to fight this test.
    """
    derived = f4s5()
    stat = cohort(derived, 2021)
    row = comp(derived, LONELY_2021)
    assert stat["pulled_count"] == 1, (
        f"precondition: cohort 2021 holds exactly one comp, got {stat['pulled_count']}"
    )
    if row["premium"] is None:
        return  # a later story may legitimately withhold it; nothing to explain
    assert stat["thin"] is True, (
        "a premium measured against a single comp — itself — must travel with the "
        "thin flag; otherwise it is a confident number with no evidence under it "
        "(NORTH_STAR)"
    )
    assert stat["basis"] == "pulled", (
        f"selected_count {stat['selected_count']} < min 4, so even this cohort's median "
        f"reports which set it came from; basis={stat['basis']}"
    )
    assert row["premium_basis"] == stat["basis"]
