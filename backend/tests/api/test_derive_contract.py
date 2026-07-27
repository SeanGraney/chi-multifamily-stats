"""F0-S2 [INVARIANT] — ``POST /api/derive``: the request/response contract and
the wiring of the derivation graph. QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

WHAT THIS FILE PINS — AND WHAT IT DELIBERATELY DOES NOT
-------------------------------------------------------
F0-S2 builds the GRAPH and the ENDPOINT, not the statistics. The real
stitcher (F4-S3), premium (F4-S5), anchor (F8-S1), buckets (F10-S1), kNN
(F11-S1), guard (F11-S3) and KM (F11-S2) are later stories. So nothing here
asserts a dollar value, a median, a survival probability or a neighbour
ordering. What is asserted is the contract, the wiring, and the invariants
that must hold of every future implementation:

* the request shape of ADR-001 §1.1 — ``weights`` and NO ``selections``
  (F5-S2 [INVARIANT]: toggle-off IS weight 0, one source of truth), filters
  as *parameters* rather than a client-side pre-filtered list, comps
  addressed by normalized address+unit (F13-S1) and never by listing id;
* the response shape of ADR-001 §1.2 — every aggregate carries the keys of
  the comps it was computed from (T-S2 evidence-first, as a *schema*
  property), ``premium: float | None`` first class, three buckets that never
  estimate when empty, a band wherever drift reaches, and ``price_test`` as a
  discriminated union in which a curve and a guard reason cannot coexist;
* the breakdown partition ``included + excluded + filtered == pulled``
  (F7-S1), computed server-side in one place;
* the payload completeness that makes the story's "no statistic is computed
  anywhere in TypeScript" AC *possible* — every quantity the view needs is
  already in the response, so the view has nothing left to compute.

Statelessness / idempotence (AC1) lives in ``test_derive_idempotence.py``;
the 100-comp budget (AC2) in ``test_derive_performance.py``; the structural
guards (D19a leakage, no clock, no I/O, union exclusivity as a type) in
``backend/tests/unit/test_derivation_graph.py``.

EXPECTED RED STATE TODAY: ``test_derive_endpoint_exists`` FAILS naming the
missing route; every other test in this directory SKIPS (see conftest.py).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def find_comp(comps: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
    for comp in comps:
        if predicate(comp):
            return comp
    return None


def all_comp_key_lists(node: Any, path: str = "$") -> list[tuple[str, list[str]]]:
    """Every ``comp_keys`` list anywhere in the payload, with its JSON path."""
    found: list[tuple[str, list[str]]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if key == "comp_keys":
                if isinstance(value, list):
                    found.append((here, value))
                elif isinstance(value, dict):  # breakdown.comp_keys: count -> keys
                    for name, keys in value.items():
                        found.append((f"{here}.{name}", keys))
                continue
            found.extend(all_comp_key_lists(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(all_comp_key_lists(value, f"{path}[{index}]"))
    return found


def assert_is_band(value: Any, where: str) -> None:
    assert isinstance(value, dict), f"{where} must be a Band object, got {value!r}"
    assert set(value) >= {"low", "mid", "high"}, (
        f"{where} must carry low/mid/high (drift−2 / drift / drift+2 — "
        f"ADR-001 §1.2, F8-S2 [INVARIANT]); got keys {sorted(value)}"
    )


# ---------------------------------------------------------------------------
# the one ungated failure — everything else in this directory skips until the
# endpoint exists
# ---------------------------------------------------------------------------


def test_derive_endpoint_exists(app) -> None:
    """ARCHITECTURE.md §4 / ADR-001: one stateless ``POST /api/derive``.

    Also pins the registration ordering note in ADR-001 §4: the router must
    be included BEFORE the static-UI mount at ``/``, or the route disappears
    the moment a UI build exists on disk.
    """
    paths = app.openapi().get("paths") or {}
    api_paths = sorted(p for p in paths if p.startswith("/api"))
    assert "/api/derive" in paths, (
        "POST /api/derive is not registered on the app. Registered /api routes: "
        f"{api_paths or '(none)'}"
    )
    assert "post" in paths["/api/derive"], (
        f"/api/derive exists but does not accept POST (methods: {sorted(paths['/api/derive'])})"
    )


# ---------------------------------------------------------------------------
# the request contract (ADR-001 §1.1)
# ---------------------------------------------------------------------------


def test_canonical_body_is_accepted(derive) -> None:
    """The full curation state of ADR-001 §1.1 round-trips to a 200."""
    response = derive()
    assert response.status_code == 200, (
        f"canonical derive body rejected with {response.status_code}: {response.text[:600]}"
    )


def test_request_has_no_selections_field(derive) -> None:
    """F5-S2 [INVARIANT]: toggle-off IS weight 0 — one source of truth.

    The story text's ``(selections, weights, ...)`` wording is superseded by
    ADR-001 §1.1: a separate selections set would be a second source of truth
    for the same fact, and the two could disagree. ``extra="forbid"`` is what
    makes that unrepresentable rather than merely discouraged.
    """
    response = derive(selections=["1234-w-fake-st-2"])
    assert response.status_code == 422, (
        "a body carrying a 'selections' field must be rejected (422): selection is "
        f"the weight (F5-S2). Got {response.status_code}: {response.text[:400]}"
    )


def test_unknown_request_field_is_rejected(derive) -> None:
    """``extra='forbid'`` generally — a typo'd field must not be silently
    dropped, or the user's curation quietly fails to apply."""
    response = derive(drift_percent=7.0)
    assert response.status_code == 422, (
        f"unknown request field accepted ({response.status_code}) — ADR-001 §1.1 "
        "pins extra='forbid' on DeriveRequest"
    )


def test_filters_are_parameters_not_a_client_side_prefiltered_list(derive, derived) -> None:
    """ADR-001 §1.1: filters are sent as parameters so that
    ``included + excluded + filtered == pulled`` is computable in ONE place.

    Consequence pinned here: turning a filter on never changes what was
    pulled and never removes a comp from the payload — it re-labels comps.
    (F7-S1 owns the filter *semantics*; this pins the seam.)
    """
    unfiltered = derived
    filtered = derive(
        filters={"max_distance_mi": None, "hide_censored": True, "leased_only": False}
    ).json()

    assert filtered["breakdown"]["pulled"] == unfiltered["breakdown"]["pulled"], (
        "a filter changed the pulled count — filters must not remove comps from the pull"
    )
    assert len(filtered["comps"]) == len(unfiltered["comps"]), (
        "a filter changed how many comps the payload carries; filtered comps must stay "
        "in the response (labelled 'filtered'), or the breakdown cannot click through to them"
    )
    censored_count = sum(1 for c in unfiltered["comps"] if c["censored"])
    if censored_count:
        assert filtered["breakdown"]["filtered"] >= 1, (
            f"hide_censored=True over a pull containing {censored_count} censored comps "
            "produced 0 filtered comps — the filter is not applied server-side"
        )


def test_weights_key_comps_and_are_echoed_back(derive, derived) -> None:
    """The weights map is keyed by comp key, and the response echoes the
    EFFECTIVE weight of every comp (ADR-001 §1.1: "nothing is hidden")."""
    comps = derived["comps"]
    assert comps, "the fixture pull produced no comps"
    for comp in comps:
        assert "weight" in comp, f"comp {comp.get('key')!r} does not echo its effective weight"

    target = comps[0]["key"]
    zeroed = derive(weights={target: 0.0}).json()
    comp = find_comp(zeroed["comps"], lambda c: c["key"] == target)
    assert comp is not None, "a comp disappeared when its weight was set to 0"
    assert comp["weight"] == 0.0, (
        f"weight 0 was not applied to {target!r} (got {comp['weight']!r}) — the weights map "
        "must be keyed by the comp key the response reports"
    )
    assert comp["state"] == "excluded", (
        f"a weight-0 comp must be state='excluded' (excluded-but-visible), got {comp['state']!r}"
    )
    anchor = zeroed.get("anchor")
    if anchor:
        assert target not in anchor["comp_keys"], (
            "a weight-0 comp is still cited as evidence for the anchor"
        )


def test_comps_absent_from_the_weights_map_default_to_one(derived) -> None:
    """ADR-001 §1.1's defaulting rule: a comp the client never mentioned
    arrives included at weight 1 (F13-S1 needs new comps after a refresh to
    do this without the client having enumerated them) — except a comp with
    no sqft, which defaults to 0 (spec §7 / F4-S5)."""
    for comp in derived["comps"]:
        expected = 0.0 if comp["sqft"] is None else 1.0
        assert comp["weight"] == expected, (
            f"comp {comp['key']!r} (sqft={comp['sqft']!r}) defaulted to weight "
            f"{comp['weight']!r}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# the fixture pull must actually exercise the pathological cases
# ---------------------------------------------------------------------------


def test_fixture_pull_covers_the_pathological_cases(derived) -> None:
    """Fixture adequacy, asserted once and loudly, so the invariant tests
    below cannot pass vacuously against a too-clean fixture."""
    comps = derived["comps"]
    assert len(comps) >= 5, f"the fixture pull has only {len(comps)} comps"
    assert any(c["sqft"] is None for c in comps), (
        "no comp in the fixture pull is missing sqft — ADR-001 §1.2 makes "
        "premium=None first class off the back of 14.7% of the real pull; the "
        "fixture must contain at least one"
    )
    assert any(c["censored"] for c in comps), (
        "no censored (still-active) comp in the fixture pull — censored-vs-leased is "
        "the single most important distinction in the system (NORTH_STAR)"
    )
    assert len({c["cohort_year"] for c in comps}) >= 2, (
        "the fixture pull spans a single cohort year; premium is defined against a "
        "comp's OWN cohort, so at least two years must be present"
    )


# ---------------------------------------------------------------------------
# the response contract (ADR-001 §1.2)
# ---------------------------------------------------------------------------


def test_response_carries_every_top_level_section(derived) -> None:
    """The story's ``DerivedState``: anchor + sensitivity, cohort medians +
    thin flags, buckets, price test, breakdown counts — one payload, so a
    partially-updated screen is not representable (ARCHITECTURE.md §6)."""
    for section in ("comps", "cohorts", "anchor", "buckets", "price_test", "breakdown", "warnings", "meta"):
        assert section in derived, f"DerivedState is missing '{section}' (ADR-001 §1.2)"


def test_breakdown_partitions_the_pull(derived) -> None:
    """F7-S1 [INVARIANT] ``included + excluded + filtered == pulled``, and the
    payload carries exactly the comps the breakdown counts."""
    breakdown = derived["breakdown"]
    total = breakdown["included"] + breakdown["excluded"] + breakdown["filtered"]
    assert total == breakdown["pulled"], (
        f"included({breakdown['included']}) + excluded({breakdown['excluded']}) + "
        f"filtered({breakdown['filtered']}) = {total} != pulled({breakdown['pulled']})"
    )
    assert len(derived["comps"]) == breakdown["pulled"], (
        f"payload carries {len(derived['comps'])} comps but reports pulled="
        f"{breakdown['pulled']} — every windowed comp must be in the response"
    )
    states = [c["state"] for c in derived["comps"]]
    for label in ("included", "excluded", "filtered"):
        assert states.count(label) == breakdown[label], (
            f"breakdown.{label}={breakdown[label]} but {states.count(label)} comps carry "
            f"state='{label}' — the count and the labels must come from one place"
        )


def test_every_aggregate_names_the_comps_it_came_from(derived) -> None:
    """T-S2 evidence-first, as a schema property: an aggregate that cannot
    name its comps must not exist. Every ``comp_keys`` list anywhere in the
    payload references real comps in that same payload."""
    known = {c["key"] for c in derived["comps"]}
    lists = all_comp_key_lists(derived)
    assert lists, "no comp_keys anywhere in DerivedState — the evidence trail is missing"

    assert any(path.startswith("$.buckets") for path, _ in lists), (
        "no bucket carries comp_keys — every bucket count must click through to its comps"
    )
    assert any(path.startswith("$.cohorts") for path, _ in lists), (
        "no cohort carries comp_keys — a cohort median must name the comps it came from"
    )
    assert any(path.startswith("$.breakdown") for path, _ in lists), (
        "breakdown carries no comp_keys — every count must click through to its comps"
    )
    if derived.get("anchor"):
        assert any(path.startswith("$.anchor") for path, _ in lists), (
            "the anchor does not name the comps it was computed from"
        )

    for path, keys in lists:
        assert isinstance(keys, list), f"{path} is not a list of comp keys"
        unknown = sorted(set(keys) - known)
        assert not unknown, f"{path} cites comps that are not in the payload: {unknown[:5]}"


def test_bucket_counts_agree_with_their_comp_keys(derived) -> None:
    for bucket in derived["buckets"]:
        assert bucket["count"] == len(bucket["comp_keys"]), (
            f"bucket {bucket['id']!r} reports count={bucket['count']} but names "
            f"{len(bucket['comp_keys'])} comps"
        )


def test_there_are_always_three_buckets_and_empty_ones_never_estimate(derived) -> None:
    """F10-S1 / NORTH_STAR: buckets never interpolate; an empty bucket
    renders as a dash, never an estimate."""
    buckets = derived["buckets"]
    assert [b["id"] for b in buckets] == ["below", "at", "above"], (
        f"expected exactly the three buckets below/at/above in order, got "
        f"{[b.get('id') for b in buckets]}"
    )
    for bucket in buckets:
        if bucket["count"] == 0:
            for stat in ("leased_dom_median", "leased_dom_min", "leased_dom_max", "cut_before_lease_rate"):
                assert bucket[stat] is None, (
                    f"empty bucket {bucket['id']!r} reports {stat}={bucket[stat]!r} — an empty "
                    "bucket has no statistic, only a dash"
                )


def test_anchor_carries_the_drift_band_and_the_fixed_sensitivity(derive) -> None:
    """F8-S2 [INVARIANT] — the sensitivity band propagates as a band, never a
    point. PM ruling: ±2 points is a module constant, not an F0-S5 knob, so
    the response reports it as a fact rather than reading it from config."""
    payload = derive(drift_pct=7.0).json()
    anchor = payload["anchor"]
    if anchor is None:
        pytest.fail("anchor is None for the default fixture pull — no positive-weight comp has $/sqft")
    assert_is_band(anchor["rent"], "anchor.rent")
    assert_is_band(anchor["psf"], "anchor.psf")
    assert anchor["drift_pct"] == pytest.approx(7.0), "anchor does not echo the requested drift"
    assert anchor["drift_sensitivity_pts"] == pytest.approx(2.0), (
        f"drift sensitivity is {anchor['drift_sensitivity_pts']!r}; the owner/PM ruling fixes "
        "it at ±2 percentage points"
    )
    assert anchor["subject_sqft"] == pytest.approx(1000.0), "anchor does not echo the subject sqft"


def test_cohorts_report_thinness_and_their_basis(derived) -> None:
    """F4-S4: a thin cohort falls back and says so. What is pinned here is
    that the flag and the basis are reported at all — the threshold behaviour
    is F4-S4's."""
    assert derived["cohorts"], "no cohort stats in the response"
    for cohort in derived["cohorts"]:
        for field in ("year", "selected_count", "pulled_count", "median_psf", "basis", "thin", "comp_keys"):
            assert field in cohort, f"CohortStat is missing '{field}' (ADR-001 §1.2)"
        assert isinstance(cohort["thin"], bool)
        assert cohort["basis"] in (None, "selected", "pulled"), (
            f"cohort basis {cohort['basis']!r} is not one of selected/pulled/None"
        )


# ---------------------------------------------------------------------------
# premium = None is first class (ADR-001 §1.2)
# ---------------------------------------------------------------------------


def test_a_comp_without_sqft_is_none_all_the_way_down(derived) -> None:
    """14.7% of the real pull has no ``squareFootage``. Such a comp appears
    with psf=None, premium=None, bucket=None, is absent from every evidence
    list, is COUNTED in the breakdown and warned about — never a silent zero,
    never a crash."""
    comp = find_comp(derived["comps"], lambda c: c["sqft"] is None)
    assert comp is not None, "fixture inadequate (see test_fixture_pull_covers_the_pathological_cases)"

    assert comp["psf"] is None, f"comp with no sqft reports psf={comp['psf']!r}"
    assert comp["premium"] is None, f"comp with no sqft reports premium={comp['premium']!r}"
    assert comp["bucket"] is None, f"comp with no premium was placed in bucket {comp['bucket']!r}"

    for path, keys in all_comp_key_lists(derived):
        # `breakdown.comp_keys` is deliberately exempt: it maps every COUNT to
        # its comps, so the missing-sqft comps must be clickable from the
        # `missing_sqft` count (T-S2 evidence-first). What must not happen is a
        # comp with no premium being cited as EVIDENCE for a statistic.
        if path.startswith("$.breakdown"):
            continue
        assert comp["key"] not in keys, (
            f"a comp with no sqft is cited as evidence at {path} — it must be absent from "
            "every median, bucket and neighbour set"
        )

    by_count = derived["breakdown"].get("comp_keys") or {}
    if "missing_sqft" in by_count:
        assert comp["key"] in by_count["missing_sqft"], (
            "the missing_sqft count does not click through to the comps it counted — every "
            "aggregate must name its comps (T-S2)"
        )

    missing = sum(1 for c in derived["comps"] if c["sqft"] is None)
    assert derived["breakdown"]["missing_sqft"] == missing, (
        f"breakdown.missing_sqft={derived['breakdown']['missing_sqft']} but {missing} comps "
        "have no sqft — excluded from the math, still counted for the user"
    )
    blob = json.dumps(derived["warnings"]).lower()
    assert "sqft" in blob or "square" in blob, (
        "comps were dropped from the math for missing sqft with no warning in the payload: "
        f"{derived['warnings']!r}"
    )


# ---------------------------------------------------------------------------
# the price test is a discriminated union (ADR-001 §1.2, F11-S3)
# ---------------------------------------------------------------------------


def test_price_test_is_none_exactly_when_there_is_no_candidate_rent(derive) -> None:
    assert derive(candidate_rent=None).json()["price_test"] is None, (
        "price_test must be None when no candidate rent has been entered (Results view)"
    )
    assert derive(candidate_rent=2100.0).json()["price_test"] is not None, (
        "price_test must be present once a candidate rent is supplied"
    )


def test_a_curve_and_a_guard_reason_can_never_coexist(derive) -> None:
    """F11-S3 [INVARIANT] made a fact of the type: there is no value in which
    both a curve and a guard reason exist. The view ``switch``es on ``state``;
    it never has to decide which of two populated fields to believe."""
    price_test = derive(candidate_rent=2100.0).json()["price_test"]
    state = price_test["state"]
    assert state in ("curve", "insufficient_evidence"), f"unknown price_test state {state!r}"

    if state == "curve":
        assert "reason" not in price_test, (
            f"a curve result carries a guard reason ({price_test.get('reason')!r}) — the two "
            "states must be mutually exclusive by construction"
        )
        assert price_test.get("curve") is not None, "curve state with no curve"
    else:
        assert "curve" not in price_test, (
            "an insufficient-evidence result carries a curve — the guard renders INSTEAD of "
            "a curve, it is not a curve with wide error bars (NORTH_STAR)"
        )
        assert price_test["reason"] in ("too_few_in_range", "all_censored"), (
            f"guard reason {price_test['reason']!r} is not one of the two named reasons"
        )
        assert "neighbors" in price_test, (
            "the guard state must still surface the nearest comps and their distances (spec §5.4)"
        )


def test_the_candidate_premium_is_evaluated_across_the_whole_drift_band(derive) -> None:
    """Owner ruling 2: the guard trips if it trips at ANY of drift−2 / drift /
    drift+2. F0-S2 pins that at the interface — the candidate premium reaches
    the price test as a BAND, so a band-aware guard is representable and a
    centre-only one is visibly incomplete. F11-S3 owns the trip behaviour."""
    price_test = derive(candidate_rent=2100.0).json()["price_test"]
    assert_is_band(price_test["candidate_premium"], "price_test.candidate_premium")


# ---------------------------------------------------------------------------
# naming and classification (PM ruling: `confirmed`, not `leased`)
# ---------------------------------------------------------------------------


def test_removal_class_uses_the_confirmed_ladder(derived) -> None:
    """pending → provisional → confirmed (NORTH_STAR, F4-S8). ARCHITECTURE
    §3's ``leased`` is an erratum (PM ruling). A still-active comp has NO
    removal class — censored is never a kind of leased."""
    for comp in derived["comps"]:
        removal_class = comp["removal_class"]
        assert removal_class in (None, "pending", "provisional", "confirmed"), (
            f"comp {comp['key']!r} has removal_class={removal_class!r}; the ladder is "
            "pending/provisional/confirmed with None for still-active comps"
        )
        if comp["censored"]:
            assert removal_class is None, (
                f"censored comp {comp['key']!r} carries removal_class={removal_class!r} — a "
                "still-active listing has not been removed, let alone leased"
            )
        else:
            assert removal_class is not None, (
                f"non-censored comp {comp['key']!r} has no removal class — every removed "
                "listing sits somewhere on the confidence ladder"
            )


def test_the_word_leased_is_not_a_removal_class_value(app, derived) -> None:
    """Catches the ARCHITECTURE §3 spelling reappearing in the wire contract,
    where it would codegen into TypeScript and outlive the erratum."""
    values = {c["removal_class"] for c in derived["comps"]}
    assert "leased" not in values, "'leased' is not a removal class — the ladder ends at 'confirmed'"

    schema = app.openapi()
    blob = json.dumps(schema)
    assert '"confirmed"' in blob, (
        "the OpenAPI schema never mentions 'confirmed' — the removal-class literal is missing "
        "from the generated contract (D12 codegens this into TS)"
    )


# ---------------------------------------------------------------------------
# AC3's mechanizable half — nothing is left for TypeScript to compute
# ---------------------------------------------------------------------------


def test_payload_carries_every_quantity_the_view_would_otherwise_compute(derive) -> None:
    """AC: "no statistic is computed anywhere in TypeScript". The review half
    of that AC is a human read of the frontend (deferred to F0-S1b/F5-S2, no
    frontend exists yet); the half that can be mechanized NOW is its
    precondition — every derived quantity the UI displays is already in the
    payload, so TypeScript has nothing left to do but format it (ADR-001 §4:
    "TypeScript may format, never compute").
    """
    payload = derive(candidate_rent=2100.0).json()

    included = [c for c in payload["comps"] if c["state"] == "included"]
    for comp in included:
        assert comp["contribution_pct"] is not None, (
            f"included comp {comp['key']!r} has no contribution_pct — the view must not divide "
            "a weight by the sum of weights itself (F5-S2)"
        )
        assert "distance_mi" in comp, "distance to the subject is not in the payload"

    for bucket in payload["buckets"]:
        assert "dollar_min" in bucket and "dollar_max" in bucket, (
            f"bucket {bucket['id']!r} carries no dollar boundaries — translating a premium "
            "cutoff through the anchor is arithmetic, and arithmetic is Python's job"
        )
        for edge in ("dollar_min", "dollar_max"):
            if bucket[edge] is not None:
                assert_is_band(bucket[edge], f"bucket[{bucket['id']}].{edge}")

    price_test = payload["price_test"]
    assert "bucket" in price_test, "the candidate's bucket is not in the payload"
    if price_test["state"] == "curve":
        assert price_test.get("horizons"), "horizon readouts are not in the payload"
        assert price_test.get("expected_vacancy") is not None, (
            "expected vacancy days/cost are not in the payload — area under a step function "
            "is not a TypeScript job"
        )


def test_meta_carries_the_provenance_of_the_derive(derived) -> None:
    meta = derived["meta"]
    for field in ("as_of", "pull_digest", "config_digest", "pipeline_version", "config"):
        assert field in meta, f"meta is missing '{field}' (ADR-001 §1.2)"
    assert isinstance(meta["config"], dict) and meta["config"], (
        "meta.config must echo the knobs this derive actually used (F0-S5's 'a knob change "
        "re-derives like any other input' is only checkable if the knobs are reported)"
    )


def test_openapi_documents_the_endpoint_and_the_price_test_union(app, derive_client) -> None:
    """D12: the OpenAPI document is what ``openapi-typescript`` consumes. The
    discriminator is what makes ``price_test`` a *discriminated* union in TS,
    so the view is forced to switch on ``state`` rather than probe fields."""
    schema = app.openapi()
    operation = schema["paths"].get("/api/derive", {}).get("post")
    assert operation is not None, "/api/derive is not in the OpenAPI document (D12 codegen input)"
    assert "requestBody" in operation, "/api/derive documents no request body"
    assert "200" in operation.get("responses", {}), "/api/derive documents no 200 response"

    blob = json.dumps(schema)
    assert '"discriminator"' in blob and '"state"' in blob, (
        "no discriminator in the schema — price_test must codegen to a TS discriminated "
        "union on 'state' (ADR-001 §1.2)"
    )


# ---------------------------------------------------------------------------
# comp identity
# ---------------------------------------------------------------------------


def test_comps_are_keyed_by_address_and_unit_not_by_listing_id(derived) -> None:
    """F13-S1 [INVARIANT]: re-key selections/weights by normalized
    address+unit — ids can churn. A key that is a listing id survives a
    refresh only by luck, and a curation state keyed on luck silently loses
    the user's work."""
    comps = derived["comps"]
    keys = [c["key"] for c in comps]
    assert len(set(keys)) == len(keys), f"comp keys are not unique: {keys}"

    for comp in comps:
        assert isinstance(comp["key"], str) and comp["key"], "a comp has an empty key"
        assert "id" not in comp and "listing_id" not in comp, (
            f"comp {comp['key']!r} exposes a listing id in the wire contract — nothing "
            "downstream may be tempted to key on it"
        )

    # The agreement between the key and the (address, unit) it claims to
    # normalize is asserted directly in
    # backend/tests/unit/test_derivation_graph.py::test_comp_key_is_derived_from_address_and_unit


class TestProposedContract:
    """QA-proposed behaviour that neither the story nor ADR-001 states
    outright. The PM/owner confirms (or overrides) these before they count as
    locked — the repo convention established by F0-S3/F0-S5."""

    def test_an_unknown_pull_ref_is_a_clean_404(self, derive) -> None:
        """A stale workspace pointing at a pull that no longer exists is a
        normal condition (F1's "corrupt/missing cache entry" edge), not a
        server error."""
        response = derive(pull_ref="no-such-pull-ref-exists")
        assert response.status_code == 404, (
            f"unknown pull_ref returned {response.status_code}; expected a clean 404 "
            f"({response.text[:300]})"
        )

    def test_cohort_comp_keys_enumerate_the_set_the_median_came_from(self, derived) -> None:
        """Evidence-first taken literally: a cohort median's ``comp_keys`` are
        the comps it was computed over — which is the selected set, or the
        pulled set when the cohort fell back (F4-S4)."""
        for cohort in derived["cohorts"]:
            if cohort["median_psf"] is None:
                continue
            expected = cohort["selected_count"] if cohort["basis"] == "selected" else cohort["pulled_count"]
            assert len(cohort["comp_keys"]) == expected, (
                f"cohort {cohort['year']} has basis={cohort['basis']!r} and "
                f"{expected} comps in that set, but names {len(cohort['comp_keys'])}"
            )

    def test_a_weight_may_not_be_negative(self, derive) -> None:
        """Consistent with the PM-confirmed F0-S3 ruling that caller bugs
        raise rather than laundering into a valid-looking result."""
        key = derive().json()["comps"][0]["key"]
        response = derive(weights={key: -1.0})
        assert response.status_code == 422, (
            f"a negative weight was accepted ({response.status_code}) — it has no meaning in "
            "this system and would silently corrupt every weighted statistic"
        )
