"""F2-S3 — Layer 1 pins for the call estimator's two structural guarantees.

Developer-authored (AGENT_DEVELOPER.md step 4: QA owns the contract tests, the
developer owns the supporting unit layer). QA's `test_f2s1_search_preview.py`
asserts *behaviour* over HTTP: the preview moved no ledger, minted no cache
entry, and reported the same number as the submit. That is the right layer for
those facts, and it is exactly the layer that cannot tell "did not spend today"
from "cannot spend at all".

These two tests hold the *structural* half, which no HTTP assertion can reach:

1. `api/plan.py` has no expression capable of spending money, because the names
   that could are not in it. QA's tests would still pass the day someone added
   `run_pull` behind a condition their fixtures happen not to take.
2. `fetchable_queries` is the only definition of "what does this pull cost" in
   the tree. That is the whole of the F2-S3 [INVARIANT] — "one function, two
   consumers, no drift" — and it is a property of the source, not of a response.

The PM's ruling on this story is explicit that the preview must be
"structurally incapable of spending — not 'does not spend today', but *cannot*".
This file is what makes that claim testable rather than a comment.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "backend" / "src" / "rentcomp"
PLAN_MODULE = SRC / "api" / "plan.py"

#: Every name in the tree that can cause a RentCast call, a ledger movement or
#: a cache write. Bound in `api/plan.py` under any alias, the "cannot spend"
#: guarantee is gone — whether or not a test happens to exercise the path.
FORBIDDEN_NAMES = frozenset(
    {
        "run_pull",
        "RentCastClient",
        "fetch_listings",
        "load_ledger",
        "save_ledger",
        "record_call",
        "write_raw_response",
        "write_manifest",
        "save_config",
        "save_api_key",
    }
)

#: Modules that either dial out or are only ever imported in order to.
FORBIDDEN_MODULES = frozenset({"httpx", "socket", "urllib", "urllib.request", "requests"})

needs_plan_module = pytest.mark.skipif(
    not PLAN_MODULE.is_file(),
    reason="backend/src/rentcomp/api/plan.py not implemented yet (F2-S3 red)",
)


def _module_tree() -> ast.Module:
    return ast.parse(PLAN_MODULE.read_text(encoding="utf-8"), filename=str(PLAN_MODULE))


def _bound_import_names(tree: ast.Module) -> dict[str, str]:
    """Every name `api/plan.py` binds by importing, mapped to its origin.

    Both statement forms, and the alias is resolved: `from x import run_pull as
    r` binds `r`, so checking the *source* name is what catches it. An import
    inside a function counts too — `ast.walk` does not stop at module level,
    which is the difference between a guard and a speed bump.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound[alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound[alias.name] = f"{module}.{alias.name}"
    return bound


@needs_plan_module
def test_the_preview_route_cannot_reach_anything_that_spends_money() -> None:
    """The PM's "structurally incapable of spending", as a property of imports.

    A live preview fires on every field edit in a debounced form. If it could
    fetch, one careless afternoon of typing an address would empty a 50-call
    month — so the guarantee cannot rest on the handler's current control flow,
    which the next story is free to change.
    """
    bound = _bound_import_names(_module_tree())

    offending_names = sorted(
        f"{name} (from {origin})" for name, origin in bound.items() if name in FORBIDDEN_NAMES
    )
    assert not offending_names, (
        f"api/plan.py imports {offending_names}. The F2-S3 preview must be structurally "
        "incapable of spending money, not merely coded not to today: it is driven by a "
        "debounced form, so every field edit would be a potential purchase. Read what it "
        "needs (planner arithmetic, the cache key, the manifest) and nothing that writes."
    )

    offending_modules = sorted(module for module in bound if module in FORBIDDEN_MODULES)
    assert not offending_modules, (
        f"api/plan.py imports {offending_modules} — a preview route has no business holding "
        "anything that can open a socket"
    )


@needs_plan_module
def test_no_second_implementation_of_what_a_pull_costs_exists() -> None:
    """F2-S3 [INVARIANT]: one function, two consumers, no drift.

    The invariant is not "the two numbers happen to match" — a test can only
    ever check that for the inputs it thought of. It is that there is one
    definition. `2 * years_back` is the plausible-looking second one, and it is
    wrong on exactly the days a cohort window has not opened yet (F4-S1 AC4):
    it would overcharge the user by two calls in the estimate they consent to.

    Scoped to the two consumers rather than the whole tree, because
    `client/planner.py` legitimately contains the definition itself and the
    tests legitimately recompute it to check the route.
    """
    consumers = [PLAN_MODULE, SRC / "client" / "pull.py"]
    for source in consumers:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        assert "fetchable_queries" in _bound_import_names(tree), (
            f"{source.name} does not import client/planner.py::fetchable_queries. Both the "
            "F2-S3 estimate and the pull that spends must count with the SAME function — a "
            "consumer computing its own count is the drift this [INVARIANT] names."
        )

    # The specific wrong answer, spelled out so a reviewer sees what is banned.
    plan_source = PLAN_MODULE.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in plan_source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "2 * request.years_back" not in code and "years_back * 2" not in code, (
        "api/plan.py appears to compute the call count arithmetically. `2 calls x yearsBack` "
        "is the story's prose, not its definition: a structurally-empty window is planned and "
        "never sent, so the number the user consents to is len(fetchable_queries(plan))."
    )
