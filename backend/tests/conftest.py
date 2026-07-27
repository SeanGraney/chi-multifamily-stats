"""F0-S1a scaffold — shared QA fixtures.

Written by QA *before* implementation (WORKFLOW.md §2), so every import of
the product package happens lazily inside fixtures/tests. A missing package
(or missing fastapi, since `pip install -e .` is the developer's step) must
produce a legible per-test failure message, never a collection error.

App-object location: pinned to the canonical **`rentcomp.app:app`**
(module-level object built by `create_app()` in the same module) per the
F0-S1a contract ruling relayed by the PM — this replaces the original
pre-implementation candidate-list resolver. If this import fails, that is a
contract break, not a lookup miss.
"""

from __future__ import annotations

import importlib

import pytest

_SCAFFOLD_HINT = (
    "F0-S1a scaffold broken: {problem} "
    "(expected after `pip install -e backend/` into the repo-root .venv)"
)


def _resolve_app():
    try:
        module = importlib.import_module("rentcomp.app")
    except ImportError as exc:
        pytest.fail(
            _SCAFFOLD_HINT.format(
                problem=f"cannot import 'rentcomp.app' — canonical app module ({exc})"
            )
        )
    candidate = getattr(module, "app", None)
    if candidate is None:
        pytest.fail(
            _SCAFFOLD_HINT.format(
                problem="'rentcomp.app' has no module-level 'app' object "
                "(canonical contract is rentcomp.app:app)"
            )
        )
    return candidate


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
