"""F4-S5 — D19a target-leakage guard over the two modules this story rewrites.

QA-authored (AGENT_QA.md: the D19a guard "isn't observable from a browser and
shouldn't wait on the developer to remember it"). GREEN today and required to
stay green — a preventive guard, not a red-first AC test.

WHY THIS STORY NEEDS ITS OWN COPY OF THE GUARD
----------------------------------------------
`premium` is the ONLY feature `distance()` may use (D19a). Everything that
computes a premium therefore sits directly upstream of kNN retrieval: if a
premium could be conditioned on a comp's outcome, the outcome would be inside
the distance metric no matter how clean `stats/knn.py` stays — and
`test_derivation_graph.py`'s existing scan covers `stats/knn.py` only, plus
any function whose name starts with `distance`. `pipeline/premium.py` and
`pipeline/cohorts.py` are covered by neither.

The risk is created by this story specifically. F4-S5 adds "fall back to all
*pulled* comps in the cohort", and the shortest way to reach "all pulled
comps" is to hand the stage the comp records themselves — at which point
`effective_dom`, `censored` and the removal ladder are one attribute access
away from the number retrieval measures distance in. They are not needed:
`cohort_medians` already receives psfs, cohort years, weights and inclusion
flags, and already counts the pulled set from exactly those, so the fallback
is computable from arguments the stage has today (ADR-001 §2.2, "plain values
in, plain values out"; `pipeline/premium.py`'s own docstring: "the stage
receives psfs, years, and medians -- and nothing else, so nothing else can
influence a premium").

Scoped to identifier *use* (names, attributes, parameters, string literals),
not to imports: prose may discuss the outcome freely, and this file's own
docstrings are proof that discussing it is not the hazard.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

#: The kNN target and everything that labels it (same set as
#: `test_derivation_graph.py`'s `TARGET_NAMES`, kept literal here so the two
#: guards cannot be weakened in one place and pass in the other).
TARGET_NAMES = {
    "effective_dom",
    "effectiveDom",
    "dom",
    "censored",
    "removal_class",
    "removalClass",
    "days_since_removal",
    "confirmed",
    "provisional",
    "pending",
}

#: The two stages F4-S5 rewrites.
PREMIUM_MODULES = ("rentcomp.pipeline.premium", "rentcomp.pipeline.cohorts")


def module_source(dotted: str) -> tuple[Path, str]:
    """The source of the module *as loaded*, so this guard reads the tree under
    test rather than whichever checkout happens to be on `sys.path` first
    (WORKFLOW.md §2)."""
    module = importlib.import_module(dotted)
    path = Path(module.__file__ or "")
    assert path.is_file(), f"cannot locate the source of {dotted} (got {path!r})"
    return path, path.read_text(encoding="utf-8")


@pytest.mark.parametrize("dotted", PREMIUM_MODULES)
def test_the_premium_stages_cannot_reach_the_outcome(dotted: str) -> None:
    """No comp outcome may be nameable where a premium is computed (D19a)."""
    path, source = module_source(dotted)
    tree = ast.parse(source, filename=str(path))

    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in TARGET_NAMES:
            offenders.append(f"name {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in TARGET_NAMES:
            offenders.append(f"attribute .{node.attr}")
        elif isinstance(node, ast.arg) and node.arg in TARGET_NAMES:
            offenders.append(f"parameter {node.arg}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in TARGET_NAMES
        ):
            offenders.append(f"string {node.value!r}")

    assert not offenders, (
        f"{dotted} can reach the kNN target (D19a): {'; '.join(sorted(set(offenders)))}. "
        f"premium is the only feature distance() may use, so a premium conditioned on a "
        f"comp's outcome puts the outcome inside the metric however clean stats/knn.py "
        f"stays. The thin-cohort fallback needs no comp records: the pulled set is already "
        f"derivable from the psfs, cohort years, weights and inclusion flags this stage "
        f"receives. ({path})"
    )
