"""F11-S2 AC — "a test asserts no runtime module imports `lifelines`".

QA-authored, written RED before implementation. Layer 1, structural.

This is the same species of test as `test_ws1_knn_retrieval.py`'s D19a
target-leakage guard, and QA owns it for the same reason AGENT_QA.md gives:
it is not observable from a browser, not observable from the API, and is
exactly the kind of invariant that quietly rots if it waits on the developer
to remember it. It is also cheap insurance on a real hazard — F11-S2's own
story text names `lifelines` twice, so a developer reaching for it in
`stats/km.py` and forgetting to take it back out is a plausible slip, not a
hypothetical one.

WHAT IT DEFENDS (D20, the hard rule in CLAUDE.md)
------------------------------------------------
"No ML frameworks. There is no machine learning in this project." The runtime
dependency budget (ARCHITECTURE.md §1a) is exactly five packages: fastapi,
uvicorn, pydantic, httpx, numpy. `lifelines` is sanctioned **dev-only** (D3,
D8) — it may verify fixtures, it may never ship. Importing it (or its
pandas/scipy/matplotlib transitive stack) anywhere under
`backend/src/rentcomp/` would silently move it into the runtime budget and
break `pip install -e .` on a clean machine.

Deliberately scanned rather than probed: `import lifelines` inside a function
body, or under a `try/except ImportError`, would never show up in a "does the
app start" check but is still a runtime dependency the moment that branch
runs. The AST sees all of them.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

#: D20's named exclusions plus `lifelines` itself. `matplotlib` and `autograd`
#: are here because they are what `lifelines` drags in — if they appear, the
#: import happened even if the literal name `lifelines` does not.
FORBIDDEN_AT_RUNTIME = frozenset(
    {
        "lifelines",
        "pandas",
        "scipy",
        "sklearn",
        "scikit_learn",
        "torch",
        "statsmodels",
        "matplotlib",
        "autograd",
    }
)


def _runtime_source_root() -> Path:
    """The package directory that is actually importable in this run.

    Derived from `rentcomp.__file__` rather than from this test file's own
    path on purpose: worktrees share one `.venv` whose editable install can
    resolve to a *different* checkout's `backend/src` (WORKFLOW.md §2), and a
    guard that scans a tree other than the one under import would be a false
    green.
    """
    package = importlib.import_module("rentcomp")
    root = Path(package.__file__).parent
    assert root.is_dir(), f"rentcomp package root is not a directory: {root}"
    return root


def _imported_names(path: Path) -> set[str]:
    """Every module name imported by `path`, at any nesting depth, as its
    top-level package (so `pandas.core` counts as `pandas`)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def test_the_runtime_package_is_scannable() -> None:
    """Legible failure if the guard is pointed at nothing (an empty scan that
    passes vacuously is the failure mode this test exists to prevent)."""
    root = _runtime_source_root()
    modules = list(root.rglob("*.py"))
    assert len(modules) > 10, (
        f"only {len(modules)} runtime module(s) found under {root} — the scan "
        "below would be close to vacuous"
    )


def test_no_runtime_module_imports_lifelines_or_any_ml_framework() -> None:
    """The AC, stated over the whole runtime package rather than just km.py —
    a reference import that migrates into `pipeline/` is the same breach."""
    root = _runtime_source_root()
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        forbidden = _imported_names(path) & FORBIDDEN_AT_RUNTIME
        if forbidden:
            offenders.append(f"{path.relative_to(root)} imports {sorted(forbidden)}")
    assert not offenders, (
        "runtime modules import a forbidden package (D20 / ARCHITECTURE.md §1a: "
        "the runtime budget is fastapi, uvicorn, pydantic, httpx, numpy — "
        "`lifelines` is dev-only, D8):\n  " + "\n  ".join(offenders)
    )


def test_the_estimator_module_stays_in_the_math_layer() -> None:
    """QA-PROPOSED contract point (flagged to the PM, cheap to overrule).

    `stats/weighted.py` and `stats/knn.py` both import nothing from
    `rentcomp` — `stats/` is the math layer and the wire contract lives in
    `models/responses.py`. `km.py` should hold that line: it may lean on its
    sibling `rentcomp.stats.*` modules, but a KM estimator that returns
    `KMCurve`/`KMPoint` directly couples the arithmetic to the API shape, and
    CLAUDE.md's three-model-layer rule is what stops that kind of coupling
    from spreading. Mapping the estimate onto the wire types belongs in
    `pipeline/pricetest.py`, where the rest of the mapping already is.
    """
    root = _runtime_source_root()
    km_path = root / "stats" / "km.py"
    if not km_path.exists():
        pytest.skip("rentcomp/stats/km.py not implemented yet (F11-S2 red)")
    leaks = {
        name
        for name in _imported_names(km_path)
        if name == "rentcomp"
    }
    tree = ast.parse(km_path.read_text(encoding="utf-8"), filename=str(km_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            top, _, rest = node.module.partition(".")
            if top == "rentcomp" and not rest.startswith("stats"):
                leaks.add(node.module)
    assert not leaks, (
        f"stats/km.py imports {sorted(leaks)} — the math layer should take plain "
        "values and return a plain result; mapping onto models/responses.py "
        "belongs in pipeline/pricetest.py (CLAUDE.md, three model layers). If the "
        "PM overrules this contract point, delete this test rather than working "
        "around it."
    )
