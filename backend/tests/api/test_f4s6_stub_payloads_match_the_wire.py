"""F4-S6 — the browser specs' stub payloads are real wire payloads. Layer 2.

QA-authored, written before any implementation exists (AGENT_QA.md protocol).

WHY THIS FILE EXISTS
--------------------
``e2e/tests/f4s6-pipeline-states.spec.ts`` fulfils ``/api/search``,
``/api/search/plan`` and ``/api/derive`` from committed JSON under
``e2e/fixtures/f4s6/`` instead of standing up a live server. That is the right
harness for this story — every F4-S6 assertion is about what the *view* does
with a payload, the states in question (a pull one window short, a pull that
found nothing) are awkward to arrange through a real server, and it keeps the
browser leg off the contended port 8000 entirely.

It also introduces the one risk a stub always has: the fixture and the wire
drift apart, the browser spec keeps passing, and it is testing a shape the
backend no longer produces. So every fixture is validated here against the
**same Pydantic models the API responds with** — ``extra="forbid"`` means a
renamed or removed field fails this file on the next run, loudly, in the layer
where the contract lives.

The payloads themselves were generated *from* this branch's pipeline in
fixture mode (zero network); only the fields the story is about were then
edited, and the semantic assertions below pin exactly those, so a fixture
whose premise has been quietly changed cannot leave a browser spec asserting
nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rentcomp.models.responses import DerivedState, SearchPlan, SearchResult

FIXTURES = Path(__file__).resolve().parents[3] / "e2e" / "fixtures" / "f4s6"

MODELS = {
    "plan.json": SearchPlan,
    "plan-unreal-years.json": SearchPlan,
    "search-complete.json": SearchResult,
    "search-complete-zero.json": SearchResult,
    "search-partial.json": SearchResult,
    "derive-complete.json": DerivedState,
    "derive-partial.json": DerivedState,
    "derive-partial-divergent.json": DerivedState,
    "derive-empty.json": DerivedState,
}


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_every_stub_fixture_is_present() -> None:
    """Fails rather than skips: a missing fixture would otherwise turn each
    browser spec below into a 404 that reads like a UI bug."""
    missing = sorted(name for name in MODELS if not (FIXTURES / name).is_file())
    assert not missing, f"e2e/fixtures/f4s6 is missing {missing} (dir: {FIXTURES})"


@pytest.mark.parametrize("name", sorted(MODELS))
def test_stub_fixture_validates_against_the_response_model(name: str) -> None:
    """The stub is a payload this API could actually have produced.

    ``model_config = ConfigDict(extra="forbid")`` on every response model
    makes this a two-way check: a field the fixture invented fails here, and a
    field the wire dropped fails here too.
    """
    MODELS[name].model_validate(load(name))


def test_the_partial_fixture_names_one_window_and_one_call() -> None:
    """The premise of "2025 Inactive missing · 1 call to complete"."""
    meta = load("derive-partial.json")["meta"]
    assert meta["partial_pull"] == {"missing": ["2019 Inactive"], "calls_to_complete": 1}
    assert load("derive-partial.json")["comps"], (
        "the partial fixture has no comps, so the browser spec's 'the workspace is "
        "still usable' assertion would pass vacuously"
    )


def test_the_divergent_fixture_keeps_the_two_numbers_apart() -> None:
    """The premise of the D5 assertion: a view printing ``missing.length``
    would print 3 where the server says 1."""
    partial = load("derive-partial-divergent.json")["meta"]["partial_pull"]
    assert len(partial["missing"]) == 3
    assert partial["calls_to_complete"] == 1
    assert partial["calls_to_complete"] != len(partial["missing"])


def test_the_complete_fixture_has_no_gap_and_real_comps() -> None:
    """The control case — it is what proves the banner is conditional."""
    state = load("derive-complete.json")
    assert state["meta"]["partial_pull"] is None
    assert len(state["comps"]) > 0
    assert state["anchor"] is not None


def test_the_empty_fixture_is_a_complete_pull_holding_nothing() -> None:
    """0 comps and NO gap: the empty state must be about the search's
    constraints, not about missing evidence — the two are different problems
    with different fixes."""
    state = load("derive-empty.json")
    assert state["comps"] == []
    assert state["breakdown"]["pulled"] == 0
    assert state["anchor"] is None
    assert state["meta"]["partial_pull"] is None
    assert load("search-complete-zero.json")["complete"] is True


def test_the_unreal_plan_years_cannot_come_from_a_clock() -> None:
    """The premise of "the progress surface reads its cohort years off the
    plan": 1999/1998 are not producible by ``new Date().getFullYear() - i``."""
    years = [cohort["year"] for cohort in load("plan-unreal-years.json")["cohorts"]]
    assert years == [1999, 1998]
