"""F0-S1a scaffold — Layer 1 structural tests (QA, written pre-implementation).

Pins the package layout from ARCHITECTURE.md §2 and the packaging decisions
D7 (console-script launch) and D9 (pip + venv, setuptools backend,
pyproject.toml + requirements.txt). All product imports happen inside test
bodies so the pre-implementation red state is a set of legible failures, not
collection errors.

Deliberately NOT asserted here (and why):
- Actual binding to localhost:8000 — no server is started in tests (D21);
  the launch command is exercised for real by Playwright's `webServer`
  config in later E2E stories (D22).
- Anything inside `rentcomp/stats/` beyond the subpackage existing — F0-S3
  owns that module's contents and is in flight in parallel.
- Any API endpoint the docs don't name — see the Layer 2 file.
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]

FAIL_HINT = (
    "F0-S1a scaffold not implemented yet: {problem} "
    "(expected after `pip install -e backend/` into the repo-root .venv)"
)

# ARCHITECTURE.md §2 — backend/src/rentcomp/ subpackages.
SUBPACKAGES = ["api", "models", "pipeline", "stats", "storage", "client"]

# ARCHITECTURE.md §2/§3 — the three model layers, separate modules, never collapsed.
MODEL_LAYER_MODULES = ["dto", "domain", "responses"]


def _import_or_fail(name: str):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        pytest.fail(FAIL_HINT.format(problem=f"cannot import '{name}' ({exc})"))


def _pyproject() -> dict:
    path = BACKEND_DIR / "pyproject.toml"
    if not path.is_file():
        pytest.fail(FAIL_HINT.format(problem="backend/pyproject.toml does not exist"))
    import tomllib

    return tomllib.loads(path.read_text())


# --- package layout (§2) ----------------------------------------------------


def test_rentcomp_package_imports():
    """The root structural assertion — everything else builds on this."""
    _import_or_fail("rentcomp")


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_importable(name):
    _import_or_fail(f"rentcomp.{name}")


@pytest.mark.parametrize("layer", MODEL_LAYER_MODULES)
def test_model_layer_module_exists(layer):
    """dto / domain / responses are three separate modules (§3 — never collapsed)."""
    _import_or_fail(f"rentcomp.models.{layer}")


def test_dunder_main_exists_for_python_dash_m_launch():
    """`python -m rentcomp` requires rentcomp/__main__.py (D7).

    Uses find_spec so the launch module is located without being executed —
    executing it would start the real server.
    """
    _import_or_fail("rentcomp")  # legible failure if the package is absent
    spec = importlib.util.find_spec("rentcomp.__main__")
    assert spec is not None, FAIL_HINT.format(
        problem="rentcomp/__main__.py missing — `python -m rentcomp` cannot launch (D7)"
    )


# --- packaging (D7 / D9) ----------------------------------------------------


def test_pyproject_uses_setuptools_backend():
    """D9: setuptools backend, binding."""
    data = _pyproject()
    backend = data.get("build-system", {}).get("build-backend")
    assert backend == "setuptools.build_meta", (
        f"D9 requires the setuptools backend; pyproject declares {backend!r}"
    )


def test_pyproject_project_name_is_rentcomp():
    data = _pyproject()
    assert data.get("project", {}).get("name") == "rentcomp"


def test_pyproject_declares_rentcomp_console_script():
    """D7: `rentcomp` console script declared in [project.scripts]."""
    scripts = _pyproject().get("project", {}).get("scripts", {})
    assert "rentcomp" in scripts, (
        f"[project.scripts] must declare 'rentcomp' (D7); found {sorted(scripts)}"
    )
    assert scripts["rentcomp"].partition(":")[0].startswith("rentcomp"), (
        f"console script should target the rentcomp package, got {scripts['rentcomp']!r}"
    )


def test_pyproject_requires_python_3_12():
    """Story text + ARCHITECTURE.md §7.3: Python floor is 3.12."""
    requires = _pyproject().get("project", {}).get("requires-python")
    assert requires is not None, "pyproject must declare requires-python"
    assert "3.12" in requires, (
        f"requires-python should floor at 3.12, got {requires!r}"
    )


def test_console_script_entry_point_registered():
    """After `pip install -e .`, the `rentcomp` console script is actually
    registered in the environment — the declaration test above only proves
    the TOML says so; this proves the install wired it (D7)."""
    from importlib.metadata import entry_points

    names = {ep.name for ep in entry_points(group="console_scripts")}
    assert "rentcomp" in names, FAIL_HINT.format(
        problem="'rentcomp' console script not registered in the venv"
    )


def test_requirements_txt_exists_and_pins_deps():
    """D9: requirements.txt for pinned deps, alongside pyproject.toml."""
    path = BACKEND_DIR / "requirements.txt"
    assert path.is_file(), FAIL_HINT.format(
        problem="backend/requirements.txt does not exist (D9)"
    )
    assert path.read_text().strip(), "backend/requirements.txt exists but is empty"
