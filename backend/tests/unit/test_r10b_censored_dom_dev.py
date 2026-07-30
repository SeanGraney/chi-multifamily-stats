"""Row 10b — the fifth fact a pre-shaped comp must not be able to state freely.

Developer-authored (AGENT_DEVELOPER.md step 4). QA's
`test_r10b_preshaped_pulls_are_shapeable.py` re-ties four of the identities the
pre-shaped format lets an author state independently:

    removed_on    = first_listed + effective_dom
    cohort_year   = first_listed.year
    removal_class = ladder(as_of - removed_on)
    a price cut happens during the spell it belongs to

There is a fifth, and it is the one that produced seven of the impossible comps
this row had to repair — one in `synthetic-basic`, six in `synthetic-100`:

    censored  =>  effective_dom = as_of - first_listed

A censored comp's DOM is a **floor measured to the pull date** (owner ruling 1,
`shape.py::_build_comp`: `effective_dom = (as_of - first.listed).days` when the
chain never ended), so a still-listed comp cannot state a start date and a DOM
that disagree about the day the pull happened. The fixtures did exactly that:
`655 W Imaginary Pl #5` claimed the 2025 cohort while claiming it had been on
market only 88 days as of 2026-05-04, which is 124 days after the last day of
2025.

Why it needs its own test rather than a comment on the repair: nothing else in
either suite would catch it. `removed_on` short-circuits on `censored` before it
ever looks at `first_listed`, so the wire contract this row is about
(`days_since_removal` null iff `removal_class` null) stays satisfied by a
censored comp whose dates are nonsense — the field is null either way. The
number that would be wrong is `effective_dom` itself: the censoring time every
Kaplan-Meier estimate over this corpus is built from (F11-S2), and the
`censored_floors` a bucket reports. A fixture stating an impossible one is
therefore silently wrong in exactly the place NORTH_STAR calls "the single most
important distinction in the whole system", and it would be reintroduced by the
next author of a pre-shaped pull without this.

Parametrized over the directory listing, like QA's file, so a fifth pre-shaped
pull is covered the day it is added.

Layer 1 (ARCHITECTURE.md §8): reads JSON off disk, does date arithmetic. No
HTTP, no browser, no clock.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

PULLS_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "pulls"


def _pull_files() -> list[Path]:
    return sorted(PULLS_DIR.glob("*.json"))


def _load(path: Path) -> tuple[date, list[dict]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return date.fromisoformat(document["fetched_at"]), list(document["comps"])


def _censored_cases() -> list:
    cases = []
    for path in _pull_files():
        as_of, comps = _load(path)
        for comp in comps:
            if not comp["censored"]:
                continue
            label = f"{path.stem}:{comp['address']}"
            if comp.get("unit"):
                label += f" #{comp['unit']}"
            cases.append(pytest.param(label, comp, as_of, id=label))
    return cases


def test_the_rentcomp_under_test_is_the_one_beside_these_tests() -> None:
    """The shared `.venv`'s editable install names ONE checkout's
    `backend/src` (WORKFLOW.md §2), so a worktree run without `PYTHONPATH`
    collects these files while importing another tree's package. This file
    reads fixtures rather than product code, but it is committed next to the
    fixtures it reads — and a run that imported the wrong tree is reading the
    wrong `fixtures/` too."""
    tests_root = Path(__file__).resolve().parents[1]  # backend/tests
    expected = tests_root.parent / "src" / "rentcomp"  # backend/src/rentcomp
    import rentcomp

    loaded = Path(rentcomp.__file__).resolve().parent
    assert loaded == expected.resolve(), (
        "these tests are running against a DIFFERENT checkout's source tree.\n"
        f"  tests here:  {tests_root}\n"
        f"  rentcomp at: {loaded}\n"
        f"  expected:    {expected}\n"
        "Set PYTHONPATH to this tree's backend/src (Windows separator is ';') and re-run."
    )


def test_the_corpus_holds_censored_comps_for_the_assertion_below_to_be_about() -> None:
    """A failing precondition rather than a skip guard (WORKFLOW.md §2: a skip
    that exits 0 is indistinguishable from a pass). If every pre-shaped comp
    were removed, the parametrization below would collect nothing and this file
    would report green over an empty set."""
    assert PULLS_DIR.is_dir(), f"pre-shaped pulls are not at {PULLS_DIR}"
    assert len(_censored_cases()) >= 10, (
        f"only {len(_censored_cases())} censored comp(s) across the pre-shaped corpus; "
        "synthetic-100 alone carries 11 and synthetic-basic 2"
    )


@pytest.mark.parametrize(("label", "comp", "as_of"), _censored_cases())
def test_a_censored_comp_has_been_listed_since_the_start_it_states(label, comp, as_of) -> None:
    """`effective_dom` for a still-active comp is `as_of - first_listed`, not a
    free number beside it.

    Stated as the subtraction rather than as `first_listed + dom == as_of` in
    the message, because the number a reader has to correct is the DOM.
    """
    first_listed = date.fromisoformat(comp["first_listed"])
    observed = (as_of - first_listed).days
    assert comp["effective_dom"] == observed, (
        f"{label} is still listed as of {as_of} and says it was first listed {first_listed}, "
        f"which is {observed}d of market time — but it states effective_dom "
        f"{comp['effective_dom']}. A censored comp's DOM is a floor measured to the pull date, "
        "so these are one fact, not two."
    )
    assert comp["removal_class"] is None, (
        f"{label} is censored and claims the {comp['removal_class']!r} rung"
    )
