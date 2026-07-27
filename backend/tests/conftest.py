"""F0-S1a scaffold — shared QA fixtures.

Written by QA *before* implementation (WORKFLOW.md §2), so every import of
the product package happens lazily inside fixtures/tests. A missing package
(or missing fastapi, since `pip install -e .` is the developer's step) must
produce a legible per-test failure message, never a collection error.

App-object location: ARCHITECTURE.md §2 places FastAPI routers in
`rentcomp/api/` but does not pin where the assembled app object lives, so
this resolver accepts a short list of canonical locations. Once the
developer's implementation lands, QA will tighten this to the one real
location (flagged to the PM in the F0-S1a test-plan handoff).
"""

from __future__ import annotations

import importlib

import pytest

_SCAFFOLD_HINT = (
    "F0-S1a scaffold not implemented yet: {problem} "
    "(expected after `pip install -e backend/` into the repo-root .venv)"
)

# (module, attribute) candidates for the module-level app object, then for an
# app factory. Ordered by how strongly ARCHITECTURE.md §2 implies each.
_APP_CANDIDATES = (
    ("rentcomp.api", "app"),
    ("rentcomp.app", "app"),
    ("rentcomp.main", "app"),
    ("rentcomp", "app"),
)
_FACTORY_CANDIDATES = (
    ("rentcomp.api", "create_app"),
    ("rentcomp.app", "create_app"),
    ("rentcomp.main", "create_app"),
    ("rentcomp", "create_app"),
)


def _resolve_app():
    try:
        importlib.import_module("rentcomp")
    except ImportError as exc:
        pytest.fail(_SCAFFOLD_HINT.format(problem=f"cannot import 'rentcomp' ({exc})"))

    for module_name, attr in _APP_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        candidate = getattr(module, attr, None)
        if candidate is not None:
            return candidate

    for module_name, attr in _FACTORY_CANDIDATES:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        factory = getattr(module, attr, None)
        if callable(factory):
            return factory()

    pytest.fail(
        _SCAFFOLD_HINT.format(
            problem=(
                "no FastAPI app object found at any canonical location "
                f"(looked for {_APP_CANDIDATES} then factories {_FACTORY_CANDIDATES})"
            )
        )
    )


@pytest.fixture(scope="session")
def app():
    """The scaffold's FastAPI application object."""
    return _resolve_app()


@pytest.fixture(scope="session")
def client(app):
    """FastAPI TestClient — Layer 2 (D21). No server bound, no network."""
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:
        pytest.fail(
            _SCAFFOLD_HINT.format(problem=f"fastapi not importable from .venv ({exc})")
        )
    return TestClient(app)
