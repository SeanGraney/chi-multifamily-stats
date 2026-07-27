"""F0-S2 — shared fixtures for the ``POST /api/derive`` Layer-2 contract tests.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). Everything here is deliberately *outside* the product package: a
test may only touch the endpoint over HTTP, never a pipeline internal, so
these tests keep working when the stub stages behind the endpoint are
replaced by the real statistics (F4-S3, F8-S1, F10-S1, F11-S1/S2/S3).

RED-STATE SHAPE (the pattern the PM asked for: a couple of loud fails, the
rest legible skips):

* ``derive_client`` SKIPS every test in this directory while ``POST
  /api/derive`` is absent from the app's route table.
* ``test_derive_contract.py::test_derive_endpoint_exists`` is the one
  ungated Layer-2 failure that names what is missing.

FIXTURE-PULL CONTRACT (QA-PROPOSED — needs PM confirmation, see the handoff).
ADR-001 §1.1 resolves ``pull_ref`` through a single ``PullLoader`` seam with
a fixture-backed implementation "so WS-1 can run before F3-S1's cache
exists". These tests need two named, deterministic, zero-network pulls:

* ``synthetic-basic`` — a small pull (~10-20 comps) that must contain, at
  minimum: >=1 comp with no ``squareFootage`` (ADR §1.2's first-class
  ``premium: None``, 14.7% of the real pull), >=1 censored/still-active comp,
  and >=2 distinct cohort years. ``test_fixture_pull_covers_the_pathological_cases``
  asserts this directly, so an inadequate fixture fails loudly rather than
  making three behaviour tests silently vacuous.
* ``synthetic-100`` — exactly ~100 comps, existing only so the "<100ms at
  100 comps" AC can be measured against the size the AC names.

Both ref *names* are overridable with ``RENTCOMP_TEST_PULL_REF`` /
``RENTCOMP_TEST_PERF_PULL_REF`` so a rename is a one-env-var change, not a
test edit. Neither pull may require live network access (D17) — the whole
suite runs in fixture mode.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

#: The endpoint under test (ARCHITECTURE.md §4).
DERIVE_PATH = "/api/derive"

#: Named fixture pulls — see the module docstring's contract note.
BASIC_PULL_REF = os.environ.get("RENTCOMP_TEST_PULL_REF", "synthetic-basic")
PERF_PULL_REF = os.environ.get("RENTCOMP_TEST_PERF_PULL_REF", "synthetic-100")

#: The subject unit. Values are arbitrary but FIXED — every derive in this
#: suite uses the same subject so that any response difference is caused by
#: the curation state under test and nothing else.
SUBJECT: dict[str, Any] = {
    "address": "1234 W Fake St Unit 2",
    "lat": 41.9,
    "lng": -87.68,
    "sqft": 1000.0,
    "beds": 2.0,
    "baths": 1.0,
}

#: The canonical ADR-001 §1.1 request body, fully explicit (no reliance on
#: server-side defaults for fields the ADR does not give defaults for).
CANONICAL_BODY: dict[str, Any] = {
    "pull_ref": BASIC_PULL_REF,
    "subject": SUBJECT,
    "weights": {},
    "include_overrides": [],
    "filters": {
        "max_distance_mi": None,
        "hide_censored": False,
        "leased_only": False,
    },
    "drift_pct": 7.0,
    "candidate_rent": None,
}


def body(**overrides: Any) -> dict[str, Any]:
    """A fresh copy of the canonical body with ``overrides`` applied."""
    payload = {
        key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
        for key, value in CANONICAL_BODY.items()
    }
    payload.update(overrides)
    return payload


def route_present(app: Any, path: str = DERIVE_PATH, method: str = "POST") -> bool:
    """True when ``app`` exposes ``method path``.

    Asked of the OpenAPI document rather than ``app.routes``: FastAPI 0.140
    keeps an included router as a single nested ``_IncludedRouter`` entry in
    ``app.routes`` instead of flattening its routes into the table, so a naive
    route scan reports "missing" for every router-registered endpoint. The
    OpenAPI document is also the thing D12's codegen consumes, so it is the
    more meaningful question anyway. The flat scan stays as a fallback for
    FastAPI versions that do flatten.
    """
    try:
        operations = (app.openapi().get("paths") or {}).get(path) or {}
    except Exception:  # pragma: no cover - defensive
        operations = {}
    if method.lower() in operations:
        return True
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) != path:
            continue
        if method in (getattr(route, "methods", None) or {method}):
            return True
    return False


def clear_pipeline_caches() -> None:
    """Best-effort ``cache_clear()`` over every already-imported rentcomp
    module (ADR-001 §3: the record-shaping memo is keyed on pull+config+as_of,
    and these tests rewrite ``RENTCOMP_HOME`` in place between cases)."""
    for name, module in list(sys.modules.items()):
        if not (name == "rentcomp" or name.startswith("rentcomp.")):
            continue
        for attribute in list(vars(module).values()):
            cache_clear = getattr(attribute, "cache_clear", None)
            if callable(cache_clear):
                try:
                    cache_clear()
                except Exception:  # pragma: no cover - defensive only
                    pass


@pytest.fixture
def rentcomp_home(tmp_path, monkeypatch):
    """An empty ``RENTCOMP_HOME`` for the duration of one test."""
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    clear_pipeline_caches()
    yield root
    clear_pipeline_caches()


@pytest.fixture
def derive_client(app, rentcomp_home):
    """A ``TestClient`` for the derive endpoint, or a legible skip.

    Layer 2 (D21): no server bound to a port, no network. Skipping (rather
    than erroring) while the endpoint is unimplemented is what keeps the red
    state readable — one named failure, N skips.
    """
    if not route_present(app):
        pytest.skip(f"POST {DERIVE_PATH} not implemented yet (F0-S2 red)")
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def make_body():
    """``make_body(**overrides) -> dict`` — the canonical body, overridden.

    Exposed as a fixture (rather than imported from this conftest) so test
    modules never depend on ``conftest`` being importable by name.
    """
    return body


@pytest.fixture
def clear_caches():
    """Expose ``clear_pipeline_caches`` to tests that mutate ``RENTCOMP_HOME``
    mid-test (ADR-001 §3 asks tests doing this to clear the record-shaping
    memo)."""
    return clear_pipeline_caches


@pytest.fixture
def pull_ref() -> str:
    return BASIC_PULL_REF


@pytest.fixture
def perf_pull_ref() -> str:
    return PERF_PULL_REF


@pytest.fixture
def derive_path() -> str:
    return DERIVE_PATH


@pytest.fixture
def derive(derive_client):
    """``derive(**overrides) -> httpx.Response`` against the canonical body."""

    def _derive(_body: dict[str, Any] | None = None, **overrides: Any):
        return derive_client.post(DERIVE_PATH, json=_body if _body is not None else body(**overrides))

    return _derive


@pytest.fixture
def derived(derive):
    """The parsed ``DerivedState`` for the canonical (all-default) body."""
    response = derive()
    assert response.status_code == 200, (
        f"POST {DERIVE_PATH} with the canonical ADR-001 §1.1 body returned "
        f"{response.status_code}: {response.text[:600]}"
    )
    return response.json()
