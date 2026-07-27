"""F0-S2 AC1 — "the endpoint is stateless and idempotent: identical body
always yields identical response." QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

WHY THIS IS ASSERTED ON RAW BYTES
---------------------------------
ADR-001 §4 makes idempotence *structural* rather than disciplinary: no wall
clock is reachable from the pipeline, no I/O below the API edge, every model
frozen, every ordering an explicit total sort, and — the load-bearing part —
**no clock- or environment-derived field anywhere in the response**. That
last property is exactly what allows the AC to be asserted as byte equality
of ``response.content`` rather than as a dict comparison with timestamps
excused. A dict comparison would pass while a ``computed_at`` field quietly
made every response unique; byte equality would not.

"Always" in the AC is a universal quantifier, so one of these tests is a
hypothesis property over arbitrary curation states rather than a single
hand-picked body.

OWNER RULING 1 (binding, supersedes spec §1's "today" wording, which is an
erratum): ``as_of`` is the PULL DATE, not the wall clock. "Censored" means
still active *as of the pull*. Two derives of the same body at different
system times must be byte-identical. The runtime half of that is here; the
structural half (the AST clock guard over ``pipeline/`` and ``stats/``) is in
``backend/tests/unit/test_derivation_graph.py`` — the two together are what
make the ruling enforceable, because a runtime test alone can only sample the
clocks it happens to fake.

EXPECTED RED STATE TODAY: every test here SKIPS (conftest's ``derive_client``
skips while ``POST /api/derive`` is absent). The named failure lives in
``test_derive_contract.py::test_derive_endpoint_exists``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

#: Keys that would make a response unique per call. ``as_of`` is explicitly
#: NOT here — it is data (the pull date), not a clock reading.
CLOCK_DERIVED_KEY_HINTS = (
    "computed_at",
    "generated_at",
    "created_at",
    "timestamp",
    "elapsed",
    "duration_ms",
    "took_ms",
    "server_time",
    "now",
)


def walk_keys(node: Any, path: str = "$"):
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}.{key}", key, value
            yield from walk_keys(value, f"{path}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_keys(value, f"{path}[{index}]")


def tree_snapshot(root: Path) -> dict[str, str]:
    """Path → sha256 for every file under ``root`` (a write of any kind, to
    any file, changes this)."""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


# ---------------------------------------------------------------------------
# AC1 — identical body ⇒ identical response
# ---------------------------------------------------------------------------


def test_identical_bodies_yield_byte_identical_responses(derive) -> None:
    first = derive(candidate_rent=2100.0)
    second = derive(candidate_rent=2100.0)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content, (
        "two identical derives returned different bytes. If the difference is a timestamp, "
        "the response is carrying a clock-derived field, which ADR-001 §1.2 forbids; if it is "
        "an ordering, some stage is iterating a set/dict instead of an explicit total sort."
    )


def test_interleaving_a_different_body_leaves_no_residue(derive, derived) -> None:
    """Statelessness, not just determinism: derive A, then a very different B,
    then A again. If any request leaves a footprint — a memo keyed on
    something incomplete, a mutated upstream artifact, a module-level cache —
    the third response differs from the first."""
    keys = [c["key"] for c in derived["comps"]]
    first = derive(drift_pct=7.0, candidate_rent=2100.0)
    derive(
        drift_pct=-3.5,
        candidate_rent=4200.0,
        weights={key: 0.0 for key in keys},
        filters={"max_distance_mi": 0.1, "hide_censored": True, "leased_only": True},
    )
    third = derive(drift_pct=7.0, candidate_rent=2100.0)
    assert first.content == third.content, (
        "an intervening derive with different curation state changed the response to the "
        "original body — the endpoint is carrying state between requests"
    )


def test_json_key_order_and_weight_insertion_order_do_not_matter(derive, derived, make_body) -> None:
    """"Identical body" means identical *values*. Two clients that serialize
    the same curation state with different key order must get the same bytes —
    otherwise something downstream is depending on dict iteration order, which
    is precisely the class of bug ADR-001 §4.5 rules out."""
    keys = [c["key"] for c in derived["comps"]][:4]
    if len(keys) < 2:
        pytest.skip("fixture pull too small to permute weights")

    forward = make_body(weights={key: 1.0 + index for index, key in enumerate(keys)}, candidate_rent=2100.0)
    reversed_weights = dict(reversed(list(forward["weights"].items())))
    backward = {key: forward[key] for key in reversed(list(forward))}
    backward["weights"] = reversed_weights

    assert derive(forward).content == derive(backward).content, (
        "reordering the JSON keys (and the weights map) changed the response bytes"
    )


def test_two_clients_share_no_state(app, derive_client, make_body) -> None:
    """No per-client state (ADR-001 §4.7): a second connection derives the
    same body to the same bytes, and neither client's history matters."""
    from fastapi.testclient import TestClient

    other = TestClient(app)
    body = make_body(candidate_rent=2100.0)
    derive_client.post("/api/derive", json=make_body(drift_pct=-1.0))
    assert derive_client.post("/api/derive", json=body).content == other.post(
        "/api/derive", json=body
    ).content, "two clients derived the same body to different bytes"


@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    drift_pct=st.floats(min_value=-10.0, max_value=25.0, allow_nan=False, allow_infinity=False),
    candidate_rent=st.one_of(st.none(), st.floats(min_value=500.0, max_value=9000.0, allow_nan=False)),
    hide_censored=st.booleans(),
    leased_only=st.booleans(),
    weight_choices=st.lists(st.sampled_from([0.0, 1.0, 2.0, 3.0]), min_size=1, max_size=40),
)
def test_arbitrary_curation_states_are_idempotent_and_partition_the_pull(
    derive, derived, drift_pct, candidate_rent, hide_censored, leased_only, weight_choices
) -> None:
    """The AC says *always*. This is that quantifier, over arbitrary weights,
    drift, candidate rent and filter combinations — each one asserted twice
    (byte-identical) and checked against F7-S1's partition invariant, which
    must hold no matter how the user curates."""
    keys = [c["key"] for c in derived["comps"]]
    weights = {key: weight_choices[index % len(weight_choices)] for index, key in enumerate(keys)}
    overrides = dict(
        weights=weights,
        drift_pct=drift_pct,
        candidate_rent=candidate_rent,
        filters={"max_distance_mi": None, "hide_censored": hide_censored, "leased_only": leased_only},
    )

    first = derive(**overrides)
    second = derive(**overrides)
    assert first.status_code == 200, f"derive failed on a legal curation state: {first.text[:400]}"
    assert first.content == second.content, "an identical body produced different bytes"

    breakdown = first.json()["breakdown"]
    assert (
        breakdown["included"] + breakdown["excluded"] + breakdown["filtered"] == breakdown["pulled"]
    ), f"included+excluded+filtered != pulled for {overrides!r}: {breakdown!r}"


# ---------------------------------------------------------------------------
# statelessness — the endpoint writes nothing
# ---------------------------------------------------------------------------


def test_derive_writes_nothing_to_the_workspace_home(derive, rentcomp_home) -> None:
    """ADR-001 §4.7: the handler has no writer in scope. Workspace autosave is
    F14-S2's separate ``PUT /api/workspaces/{key}``. A derive that writes is a
    derive that can fail differently the second time."""
    empty = tree_snapshot(rentcomp_home)
    assert empty == {}, "the fixture home did not start empty; this test cannot mean anything"

    derive()
    after_one = tree_snapshot(rentcomp_home)
    assert after_one == {}, (
        f"one derive created {sorted(after_one)} under RENTCOMP_HOME. Deriving is a read: "
        "workspace autosave is F14-S2's separate PUT /api/workspaces/{key}."
    )

    for drift in (0.0, 7.0, 12.0):
        derive(drift_pct=drift, candidate_rent=2100.0)
    after = tree_snapshot(rentcomp_home)
    assert after == after_one, (
        "deriving changed files under RENTCOMP_HOME. Added/changed: "
        f"{sorted(set(after) - set(after_one)) or sorted(k for k in after if after_one.get(k) != after[k])}"
    )


# ---------------------------------------------------------------------------
# owner ruling 1 — as_of is the pull date, never the wall clock
# ---------------------------------------------------------------------------


def test_no_response_field_is_clock_derived(derived) -> None:
    """ADR-001 §1.2: "nothing in the response is clock-derived or
    environment-derived — no ``computed_at``, no ``elapsed_ms``". This is the
    property that makes byte equality assertable at all."""
    offenders = [
        path
        for path, key, _ in walk_keys(derived)
        if any(hint in str(key).lower() for hint in CLOCK_DERIVED_KEY_HINTS)
    ]
    assert not offenders, (
        f"clock/environment-derived field(s) in DerivedState: {offenders}. Every such field "
        "makes two identical derives differ and the AC untestable."
    )


def test_as_of_is_the_pull_date_and_not_today(derived) -> None:
    """Owner ruling 1. Using the wall clock would add unobserved days to
    every censored comp's DOM floor and silently re-classify removals across
    the pending→provisional→confirmed ladder with no new evidence."""
    as_of = derived["meta"]["as_of"]
    parsed = dt.date.fromisoformat(str(as_of))
    assert parsed < dt.date.today(), (
        f"meta.as_of is {as_of} (today is {dt.date.today().isoformat()}). The fixture pull must "
        "carry a past fetch date so that 'as_of is the pull date, not the wall clock' is "
        "observable at all — if these are ever equal, this AC is untestable."
    )
    today = dt.date.today().isoformat()
    assert today not in json.dumps(derived), (
        f"today's date ({today}) appears somewhere in a response derived from a pull fetched "
        f"on {as_of} — something read the wall clock"
    )


def test_a_moving_system_clock_cannot_change_the_response(derive, monkeypatch) -> None:
    """Owner ruling 1, the runtime half: two derives of the same body at
    different simulated system times are byte-identical.

    Faking a system clock in-process is necessarily partial (``datetime`` is a
    C type), so this samples what CAN be faked; the structural guarantee is
    the AST guard in
    ``backend/tests/unit/test_derivation_graph.py::test_pipeline_and_stats_cannot_read_the_wall_clock``.
    """
    first = derive(candidate_rent=2100.0)

    real_time = time.time
    monkeypatch.setattr(time, "time", lambda: real_time() + 400 * 24 * 3600)
    monkeypatch.setattr(time, "monotonic", lambda: 0.0)
    second = derive(candidate_rent=2100.0)

    assert first.content == second.content, (
        "advancing the system clock by ~13 months changed the derive output — as_of must come "
        "from the pull manifest (owner ruling 1), never from the clock"
    )


def test_a_knob_change_re_derives_like_any_other_input(derive, rentcomp_home, clear_caches) -> None:
    """F0-S5's load-bearing clause, and ADR-001 §3's memo key. Config is part
    of the record-shaping memo key precisely so that a memo cannot serve a
    result computed under different knobs."""
    before = derive()
    payload_before = before.json()

    (rentcomp_home / "config.json").write_text(
        json.dumps({"knn_k": 11, "min_cohort_size": 2}), encoding="utf-8"
    )
    clear_caches()

    after = derive()
    payload_after = after.json()

    assert payload_after["meta"]["config"]["knn_k"] == 11, (
        "a knob edited on disk did not reach the derive — meta.config still reports "
        f"knn_k={payload_after['meta']['config'].get('knn_k')!r}"
    )
    assert payload_before["meta"]["config_digest"] != payload_after["meta"]["config_digest"], (
        "config_digest is unchanged after a knob change; a memo keyed on it could then serve "
        "a result computed under the old knobs"
    )
