"""F0-S2 Layer 2 — `POST /api/derive` behaviours QA's contract file leaves open.

Developer-authored companion to QA's `test_derive_contract.py` /
`test_derive_idempotence.py` / `test_derive_performance.py`, which own the AC
itself. This file covers the edge's own responsibilities — how the endpoint
behaves when the request is wrong, what it refuses to leak, and the
`PullLoader` seam (including its memo, which is the one piece of mutable
process state anywhere near this endpoint and therefore the one worth pinning
directly).

Reuses QA's fixtures from `conftest.py` (`derive`, `derived`, `derive_client`,
`rentcomp_home`) so both files exercise the endpoint through exactly one door.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

#: The fetch date stamped on `fixtures/synthetic/pulls/synthetic-basic.json`.
#: Hard-coded on purpose: "as_of is the pull date" is only observable if the
#: expected date is stated independently of the thing under test.
FIXTURE_AS_OF = "2026-05-04"


# ---------------------------------------------------------------------------
# the edge's error contract
# ---------------------------------------------------------------------------


def test_an_unknown_pull_ref_names_the_ref_and_not_the_filesystem(derive) -> None:
    """404 (a stale workspace is a normal condition), and the message must not
    hand a client the server's directory layout."""
    response = derive(pull_ref="no-such-pull")
    assert response.status_code == 404
    detail = json.dumps(response.json())
    assert "no-such-pull" in detail
    assert "/" not in detail.replace("\\/", ""), f"the 404 leaks a path: {detail}"


@pytest.mark.parametrize(
    "ref", ["../../../etc/passwd", "..", ".hidden", "sub/dir", "", "synthetic-basic/../x"]
)
def test_a_pull_ref_cannot_escape_the_pull_store(derive, ref: str) -> None:
    """`pull_ref` arrives from a request body, so path traversal is something a
    client can *ask* for. A ref is a flat name; anything else is not found."""
    assert derive(pull_ref=ref).status_code in (404, 422)


@pytest.mark.parametrize(
    "override",
    [
        {"weights": {"1200 w fake st|1": -1.0}},
        {"candidate_rent": 0.0},
        {"candidate_rent": -100.0},
        {"subject": {"address": "x", "lat": 41.9, "lng": -87.68, "sqft": 0.0, "beds": 2.0, "baths": 1.0}},
        {"filters": {"max_distance_mi": -1.0, "hide_censored": False, "leased_only": False}},
        {"selections": ["a"]},
        {"drift": 7.0},
    ],
)
def test_curation_that_cannot_mean_anything_is_rejected_not_absorbed(derive, override) -> None:
    """Each of these would otherwise silently produce a plausible-looking
    number: a negative weight corrupts every weighted statistic, a zero-sqft
    subject divides through, and an unrecognized field is curation the user
    believes they applied."""
    assert derive(**override).status_code == 422


def test_a_missing_body_is_a_422_not_a_500(derive_client, derive_path) -> None:
    assert derive_client.post(derive_path, json={}).status_code == 422


# ---------------------------------------------------------------------------
# owner ruling 1, pinned against the fixture rather than against "not today"
# ---------------------------------------------------------------------------


def test_as_of_is_exactly_the_pulls_fetch_date(derived) -> None:
    assert derived["meta"]["as_of"] == FIXTURE_AS_OF
    assert date.fromisoformat(derived["meta"]["as_of"]) < date.today()


def test_meta_reports_the_ref_it_derived_and_a_digest_of_the_evidence(derived, derive) -> None:
    assert derived["meta"]["pull_ref"] == "synthetic-basic"
    assert len(derived["meta"]["pull_digest"]) == 64, "expected a sha256 hex digest"
    other = derive(pull_ref="synthetic-100").json()
    assert other["meta"]["pull_digest"] != derived["meta"]["pull_digest"], (
        "two different pulls share a digest — a memo keyed on it could serve one for the other"
    )


# ---------------------------------------------------------------------------
# the stub inventory is visible to whoever is looking at the numbers
# ---------------------------------------------------------------------------


def test_every_placeholder_stage_announces_itself_in_the_payload(derived) -> None:
    """A stub that is silent is a stub that gets screenshotted as a result.
    Each entry names the story that replaces it, so the warning list doubles as
    the handoff note the UI can render."""
    stubs = [w for w in derived["warnings"] if w["code"] == "stub_stage"]
    assert stubs, "the pipeline is stubbed but the payload does not say so"
    blob = json.dumps(stubs)
    for story in ("F4-S3", "F8-S1", "F10-S1", "F11-S1", "F11-S3", "F11-S2"):
        assert story in blob, f"no stub warning names {story}"
    assert "stubs" in derived["meta"]["pipeline_version"]


def test_a_warning_clicks_through_to_a_real_breakdown_count(derived) -> None:
    for warning in derived["warnings"]:
        ref = warning.get("breakdown_ref")
        if ref is not None:
            assert ref in derived["breakdown"]["comp_keys"], (
                f"warning {warning['code']!r} points at breakdown count {ref!r}, which does "
                "not exist — the evidence trail is broken"
            )


def test_the_placeholder_anchor_is_the_sentinel_not_a_market_estimate(derived) -> None:
    """F0-S2 stub, asserted over HTTP so the wire cannot quietly start
    carrying a plausible number before F8-S1 lands and this test is deleted."""
    anchor = derived["anchor"]
    assert anchor["psf"] == {"low": 1.0, "mid": 1.0, "high": 1.0}
    assert anchor["rent"]["mid"] == anchor["subject_sqft"]


# ---------------------------------------------------------------------------
# curation, end to end over HTTP
# ---------------------------------------------------------------------------


def test_contribution_shares_are_computed_server_side_and_sum_to_one(derive) -> None:
    """D5: the view must never divide one payload field by another."""
    payload = derive().json()
    shares = [c["contribution_pct"] for c in payload["comps"] if c["state"] == "included"]
    assert shares and all(share is not None for share in shares)
    assert sum(shares) == pytest.approx(1.0)
    assert all(
        c["contribution_pct"] is None for c in payload["comps"] if c["state"] != "included"
    )


def test_a_heavier_weight_earns_a_larger_share(derive, derived) -> None:
    key = next(c["key"] for c in derived["comps"] if c["state"] == "included")
    baseline = next(c for c in derived["comps"] if c["key"] == key)["contribution_pct"]
    heavier = derive(weights={key: 3.0}).json()
    assert next(c for c in heavier["comps"] if c["key"] == key)["contribution_pct"] > baseline


def test_an_include_override_survives_a_filter(derive, derived) -> None:
    """F7-S1: the manual re-include is what makes a filter safe to toggle."""
    censored = next(c["key"] for c in derived["comps"] if c["censored"])
    hidden = derive(filters={"max_distance_mi": None, "hide_censored": True, "leased_only": False})
    assert next(c for c in hidden.json()["comps"] if c["key"] == censored)["state"] == "filtered"

    kept = derive(
        filters={"max_distance_mi": None, "hide_censored": True, "leased_only": False},
        include_overrides=[censored],
    )
    assert next(c for c in kept.json()["comps"] if c["key"] == censored)["state"] == "included"


def test_a_toggled_off_comp_stops_being_evidence_everywhere_at_once(derive, derived) -> None:
    """The reactivity invariant in the small: one payload, so a comp cannot be
    out of the anchor and still inside a cohort median."""
    key = next(
        c["key"]
        for c in derived["comps"]
        if c["state"] == "included" and c["psf"] is not None
    )
    zeroed = derive(weights={key: 0.0}).json()
    assert key not in (zeroed["anchor"] or {"comp_keys": []})["comp_keys"]
    for cohort in zeroed["cohorts"]:
        assert key not in cohort["comp_keys"]
    for bucket in zeroed["buckets"]:
        assert key not in bucket["comp_keys"]


def test_a_filter_relabels_and_never_deletes_evidence(derive, derived) -> None:
    tight = derive(
        filters={"max_distance_mi": 0.0001, "hide_censored": False, "leased_only": False}
    ).json()
    assert len(tight["comps"]) == len(derived["comps"])
    assert tight["breakdown"]["filtered"] >= 1
    assert tight["breakdown"]["pulled"] == derived["breakdown"]["pulled"]


def test_the_cohort_median_moves_when_the_selection_moves(derive, derived) -> None:
    """F4-S5 [INVARIANT] observed over the wire: premium is defined against the
    median of the *selected* comps, which is why it is a per-request stage."""
    year = next(c["year"] for c in derived["cohorts"] if c["selected_count"] >= 2)
    victim = next(
        c["key"]
        for c in derived["comps"]
        if c["cohort_year"] == year and c["state"] == "included" and c["psf"] is not None
    )
    after = derive(weights={victim: 0.0}).json()
    before_stat = next(c for c in derived["cohorts"] if c["year"] == year)
    after_stat = next(c for c in after["cohorts"] if c["year"] == year)
    assert after_stat["selected_count"] == before_stat["selected_count"] - 1


# ---------------------------------------------------------------------------
# the PullLoader seam and its memo (ADR-001 §3)
# ---------------------------------------------------------------------------


def test_the_record_shaping_memo_is_a_pure_function_cache(rentcomp_home) -> None:
    """The one piece of process-level state near this endpoint. It is keyed on
    every input that can change the answer — including `Config`, because
    "a knob change re-derives like any other input" (F0-S5) — and it is a pure
    function cache, so it cannot affect idempotence."""
    from rentcomp.storage.config import Config
    from rentcomp.storage.pulls import load_shaped_pull

    load_shaped_pull.cache_clear()
    first = load_shaped_pull("synthetic-basic", Config())
    assert load_shaped_pull("synthetic-basic", Config()) is first, "memo did not hit"
    assert load_shaped_pull.cache_info().hits >= 1

    other_knobs = load_shaped_pull("synthetic-basic", Config(knn_k=11))
    assert other_knobs is not first, "the memo ignored a knob change"
    assert other_knobs == first, "shaping is not knob-dependent yet, so the value must agree"

    load_shaped_pull.cache_clear()
    assert load_shaped_pull("synthetic-basic", Config()) is not first
    assert load_shaped_pull("synthetic-basic", Config()) == first


def test_an_absent_pull_raises_rather_than_returning_an_empty_market() -> None:
    """A pull that is gone must not look like a neighbourhood with no comps in
    it — that renders as a real, empty answer."""
    from rentcomp.storage.config import Config
    from rentcomp.storage.pulls import PullNotFoundError, load_shaped_pull

    with pytest.raises(PullNotFoundError):
        load_shaped_pull("definitely-not-a-pull", Config())


def test_the_config_digest_changes_with_the_knobs_and_not_with_their_order() -> None:
    from rentcomp.storage.config import Config
    from rentcomp.storage.pulls import config_digest

    assert config_digest(Config()) == config_digest(Config())
    assert config_digest(Config()) != config_digest(Config(knn_k=11))
    assert config_digest(Config(knn_k=11, min_cohort_size=2)) == config_digest(
        Config(min_cohort_size=2, knn_k=11)
    )


def test_the_perf_pull_is_a_realistic_shape_not_a_padded_one(derive) -> None:
    """A 100-comp fixture that is 100 identical comps would make the AC2
    measurement meaningless — the chain's cost is in the branches."""
    payload = derive(pull_ref="synthetic-100").json()
    comps = payload["comps"]
    assert len(comps) == 100
    assert len({c["key"] for c in comps}) == 100
    assert any(c["sqft"] is None for c in comps)
    assert any(c["censored"] for c in comps)
    assert len({c["cohort_year"] for c in comps}) >= 2
    assert len({c["removal_class"] for c in comps}) >= 3


# ---------------------------------------------------------------------------
# registration order (ADR-001 §4) — the failure that only appears in production
# ---------------------------------------------------------------------------


def test_the_derive_route_survives_a_built_ui_being_mounted_at_root(tmp_path, monkeypatch) -> None:
    """`create_app()` mounts the built UI at "/" last, and a Starlette mount at
    root swallows everything registered after it. Every test in this suite runs
    without a UI build, so without this test the ordering bug would appear for
    the first time on the developer's own machine, after the first frontend
    build (D7)."""
    from fastapi.testclient import TestClient

    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>built</html>", encoding="utf-8")
    monkeypatch.setenv("RENTCOMP_UI_DIR", str(dist))
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path / "home"))

    from rentcomp.app import create_app

    app = create_app()
    assert "post" in (app.openapi().get("paths") or {}).get("/api/derive", {})

    client = TestClient(app)
    assert client.get("/").status_code == 200, "the UI is not being served"
    body = {
        "pull_ref": "synthetic-basic",
        "subject": {
            "address": "1234 W Fake St Unit 2",
            "lat": 41.9,
            "lng": -87.68,
            "sqft": 1000.0,
            "beds": 2.0,
            "baths": 1.0,
        },
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 7.0,
        "candidate_rent": None,
    }
    assert client.post("/api/derive", json=body).status_code == 200
