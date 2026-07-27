"""WS-1 — F11-S1 [INVARIANT] kNN retrieval, replacing `stats/knn.py`'s
placeholder (returns no indices at all). QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

Layer 1: `select_neighbors()` is directly importable, takes plain floats,
and is already the real function under test today (F0-S2 pinned its
signature as the stub) -- AGENT_QA.md decision procedure item 1.

D19a is asserted as a SIGNATURE + SOURCE fact, not a runtime probe (same
technique `test_derivation_graph.py` uses): a function that was never handed
`effective_dom` cannot leak it, and a source-text scan makes "someone later
adds a `comps` parameter here" fail immediately rather than only when a test
happens to construct an adversarial input.

Per AGENT_QA.md's own worked example ("don't pin `np.argsort` was called,
pin that ties are broken deterministically"): the tie-break tests below
assert REPRODUCIBILITY across repeated calls, never a specific winning
index, so a legitimate [DEFAULT] swap in tie-break mechanics does not fail
this suite.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from rentcomp.stats.knn import select_neighbors

KNN_SOURCE_PATH = Path(inspect.getfile(select_neighbors))


def test_distance_only_ever_receives_premium_values_not_comp_records() -> None:
    """D19a: the retrieval function's ENTIRE input surface is
    `(premiums, candidate_premium, k)` -- plain floats and an int. No
    parameter through which a comp record, `effective_dom`, or `censored`
    could arrive."""
    params = list(inspect.signature(select_neighbors).parameters)
    assert params == ["premiums", "candidate_premium", "k"], (
        f"select_neighbors' parameter list changed to {params!r} -- if a comp record or an "
        f"outcome field entered this signature, D19a's guarantee ('effective_dom must never "
        f"appear in distance()') would depend on code review again instead of the call graph"
    )


def test_the_module_source_never_mentions_effective_dom_or_censored() -> None:
    """A source-text guard: even a variable named `effective_dom` or
    `censored` appearing anywhere in this module is a smell that the
    outcome pair is somehow in scope here. Same technique as
    `test_derivation_graph.py`'s existing AST guards."""
    source = KNN_SOURCE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    forbidden = {"effective_dom", "censored"}
    hit = names & forbidden
    assert not hit, f"stats/knn.py must never reference {hit} (D19a) -- target leakage"


def test_k_nearest_by_absolute_premium_distance() -> None:
    premiums = [0.0, 0.05, -0.10, 0.02]
    result = select_neighbors(premiums, 0.0, 2)
    assert set(result) == {0, 3}, (
        f"nearest 2 to candidate=0.0 by |diff| are index 0 (dist 0.0) and index 3 (dist 0.02); "
        f"got {result}"
    )


def test_k_larger_than_the_pool_returns_every_index_exactly_once() -> None:
    premiums = [0.01, -0.02, 0.03]
    result = select_neighbors(premiums, 0.0, 10)
    assert sorted(result) == [0, 1, 2]
    assert len(result) == len(set(result)), "no index may repeat"


def test_empty_pool_returns_no_neighbors() -> None:
    assert select_neighbors([], 0.0, 5) == []


def test_ties_are_broken_deterministically_across_repeated_calls() -> None:
    """Two premiums equidistant from the candidate (+0.03 / -0.03 from 0.0),
    k=1: whichever the implementation picks, it must pick the SAME one
    every time -- reproducibility is the AC, not a specific winner (see
    module docstring)."""
    premiums = [0.03, -0.03]
    results = {tuple(select_neighbors(premiums, 0.0, 1)) for _ in range(20)}
    assert len(results) == 1, (
        f"select_neighbors returned different tie-break winners across repeated identical "
        f"calls: {results} -- ties must be broken deterministically (F11-S1 AC)"
    )


def test_ties_are_broken_deterministically_with_a_larger_pool() -> None:
    """Same property with more than one tied pair, so a partial/unstable
    sort couldn't accidentally look deterministic by luck."""
    premiums = [0.10, -0.10, 0.20, -0.20, 0.05, -0.05]
    results = {tuple(select_neighbors(premiums, 0.0, 3)) for _ in range(20)}
    assert len(results) == 1, f"non-deterministic tie-break under repeated calls: {results}"
