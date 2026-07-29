"""Row 10b — a pre-shaped fixture must assert a state real shaping could produce.

WHY THIS FILE EXISTS
--------------------
`models/responses.py` declares `DerivedComp.days_since_removal` to be `None`
**iff** `removal_class` is `None`. That declaration is true of every comp the
product can produce from real evidence, and false in all three committed
pre-shaped fixtures — which predate `StitchedComp.first_listed` and omit it,
so `removed_on` returns `None` while `removal_class` is set. Measured before
this row landed: `synthetic-100` 89/100, `synthetic-basic` 7/9,
`ws1-anchor-drift` 2/2; `ws1-real` 0/567.

That is not a shipped-numbers defect — it is a trap. `synthetic-basic` is the
pull the entire Layer-2 API-contract suite derives against, so its pending row
reads `pending / None`, i.e. *"removed nulld — classifying"*, and F5-S1 renders
exactly that row.

WHAT IS ACTUALLY BEING PINNED
-----------------------------
Not "the fixtures currently have a `first_listed` field" — that is a spelling.
The real property is that **a pre-shaped fixture must not assert a combination
of facts that `pipeline/shape.py` could never emit.** The pre-shaped format
lets an author state, independently, several things that real shaping *derives
from one another*:

    removed_on   = first_listed + effective_dom      domain.py::removed_on
    cohort_year  = first_listed.year                 shape.py:585
    removal_class = ladder(as_of - removed_on)       shape.py:555
    a price cut happens during the spell it belongs to

Stating them independently is how they drifted apart. Each test below re-ties
one of them, so a future fixture that is internally impossible fails here — on
arrival, cheaply, at Layer 1 — instead of surfacing as a null on a row a
frontend developer is trying to render.

Every test is parametrized over **the fixture directory listing**, not over a
hard-coded list of three names, so a fourth pre-shaped pull is covered the day
it is added and cannot opt out by being new.

Layer 1 (D21): these read JSON off disk and call one property. No request body,
no HTTP, no browser. The wire-level statement of the same invariant is in
`backend/tests/api/test_r10b_removal_contract_on_the_wire.py`.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

#: `fixtures/synthetic/pulls/` — located from this file rather than from the
#: `rentcomp` package, so that a worktree whose `PYTHONPATH` resolves to the
#: wrong source tree still reads *this checkout's* fixtures and the mismatch
#: shows up as a failure rather than as a quiet pass over someone else's data.
PULLS_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "pulls"

#: F4-S8's ladder, restated here as data rather than imported, so that a change
#: to the product's thresholds has to be made deliberately in two places. The
#: bounds are days-since-removal, inclusive lower / exclusive upper.
LADDER = (("pending", 0, 7), ("provisional", 7, 42), ("confirmed", 42, None))


def _rung(days: int) -> str:
    for name, lo, hi in LADDER:
        if days >= lo and (hi is None or days < hi):
            return name
    raise AssertionError(f"{days} days since removal falls off the ladder")


def _pull_files() -> list[Path]:
    return sorted(PULLS_DIR.glob("*.json"))


def _load(path: Path) -> tuple[date, list[dict]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    return date.fromisoformat(document["fetched_at"]), list(document["comps"])


def _cases() -> list[tuple[str, dict, date]]:
    """One case per comp, id'd by file + address, so a failure names the row."""
    cases = []
    for path in _pull_files():
        as_of, comps = _load(path)
        for comp in comps:
            label = f"{path.stem}:{comp['address']}"
            if comp.get("unit"):
                label += f" #{comp['unit']}"
            cases.append(pytest.param(label, comp, as_of, id=label))
    return cases


# ---------------------------------------------------------------------------
# the harness itself must not be able to pass vacuously
# ---------------------------------------------------------------------------


def test_the_pre_shaped_fixture_corpus_is_where_this_file_thinks_it_is() -> None:
    """A failing precondition, deliberately, rather than a skip guard.

    `WORKFLOW.md` §2: a skip that exits 0 is indistinguishable from a pass, and
    it is the defect class that has recurred most often on this project. If the
    fixture directory moves, every parametrized test below would silently
    collect zero cases and the file would report green over nothing.
    """
    assert PULLS_DIR.is_dir(), f"pre-shaped pulls are not at {PULLS_DIR}"
    files = _pull_files()
    assert len(files) >= 3, (
        f"only {len(files)} pre-shaped pull(s) under {PULLS_DIR}; the three this row was "
        "opened against are synthetic-basic, synthetic-100 and ws1-anchor-drift"
    )
    assert sum(len(_load(path)[1]) for path in files) >= 100, (
        "the corpus collected fewer comps than synthetic-100 alone holds — the parametrization "
        "below is reading the wrong thing"
    )


def test_the_stitched_comp_model_still_carries_the_field_this_row_is_about() -> None:
    """If `first_listed` is ever renamed or removed, this file's premise is gone
    and every test in it would start asserting about a key nothing reads."""
    from rentcomp.models.domain import StitchedComp

    assert "first_listed" in StitchedComp.model_fields
    assert "removed_on" in dir(StitchedComp), (
        "`removed_on` is the property that turns `first_listed` into the datum the removal "
        "ladder is measured from; without it this row's invariant has no subject"
    )


# ---------------------------------------------------------------------------
# the four facts a pre-shaped comp must not be able to state independently
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_every_pre_shaped_comp_says_when_its_listing_started(label, comp, as_of) -> None:
    """`first_listed` is not optional evidence — it is the datum three other
    fields are functions of.

    `shape_raw_pull` sets it on every comp it emits (`shape.py:586`), so a
    pre-shaped fixture that omits it is describing a comp the pipeline cannot
    produce. This is the root cause of the whole row: with it absent,
    `removed_on` returns `None`, and `days_since_removal` is `None` on a row
    that is simultaneously claiming a removal rung.
    """
    assert comp.get("first_listed") is not None, (
        f"{label} states no first_listed. Its removal_class is "
        f"{comp['removal_class']!r}, so the wire will carry that rung beside a null "
        "days_since_removal — the row renders as 'removed nulld'"
    )
    assert date.fromisoformat(comp["first_listed"]) <= as_of, (
        f"{label} was first listed after the pull that observed it"
    )


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_a_removed_comp_reports_a_rung_its_own_dates_agree_with(label, comp, as_of) -> None:
    """`removal_class` is `ladder(as_of - removed_on)`, not an independent
    opinion.

    A fixture is free to choose *when* a comp was listed and how long it ran.
    It is not free to choose the rung as well — that falls out. A fixture that
    picks both is asserting the badge and the number beside it disagree, which
    is exactly the row copy F4-S8 exists to make trustworthy.
    """
    if comp["censored"]:
        # Not a skip: `WORKFLOW.md` §2 — a skip that exits 0 reads the same as
        # a pass. The censored branch has its own test below, and
        # `test_the_invariant_is_not_vacuous_on_this_corpus` is what proves
        # this branch is not swallowing the whole corpus.
        return
    first_listed = date.fromisoformat(comp["first_listed"])
    removed_on = first_listed + timedelta(days=comp["effective_dom"])
    days = (as_of - removed_on).days
    assert days >= 0, (
        f"{label} is removed on {removed_on} — after the pull date {as_of} that observed it"
    )
    assert comp["removal_class"] == _rung(days), (
        f"{label} claims the {comp['removal_class']!r} rung, but first_listed "
        f"{first_listed} + dom {comp['effective_dom']} puts its removal {days}d before the "
        f"pull, which is the {_rung(days)!r} rung"
    )


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_a_still_active_comp_claims_no_rung(label, comp, as_of) -> None:
    """The forward direction, on the shipped corpus rather than on a hand-built
    comp.

    NORTH_STAR's most important distinction: censored is never a kind of
    leased. A censored comp's `effective_dom` is a floor measured to the pull
    date, so `first_listed + effective_dom` reconstructs the *pull date* and
    not a removal — which is precisely why `removed_on` refuses to answer.
    """
    if not comp["censored"]:
        return
    assert comp["removal_class"] is None, (
        f"{label} is censored and still claims the {comp['removal_class']!r} rung"
    )


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_the_cohort_year_is_the_year_the_listing_started(label, comp, as_of) -> None:
    """`shape.py:585` is `cohort_year=first.listed.year`. One fact, and the
    pre-shaped format lets it be written down twice.

    This matters past tidiness: `cohort_year` selects the median a comp's
    premium is measured against (F4-S5), so a fixture whose cohort year and
    stitched start disagree is measuring a comp against the wrong cohort while
    looking entirely well-formed.
    """
    first_listed = date.fromisoformat(comp["first_listed"])
    assert comp["cohort_year"] == first_listed.year, (
        f"{label} is in cohort {comp['cohort_year']} but was first listed {first_listed}"
    )


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_every_price_cut_happens_during_the_spell_it_belongs_to(label, comp, as_of) -> None:
    """A cut before the listing existed is not a cut.

    Included because it is a *second, independent* witness to the same
    `first_listed`: nine comps in `synthetic-100` carry a 2025 cut on a listing
    the fixture places in 2026, and that contradiction was undetectable while
    the start date was absent. It is also what makes the ladder corrections
    this row applied checkable rather than arbitrary — the cut date and the
    cohort year agree with each other about when the listing ran.
    """
    cuts = comp.get("cut_history") or []
    if not cuts:
        return
    first_listed = date.fromisoformat(comp["first_listed"])
    end = first_listed + timedelta(days=comp["effective_dom"])
    for cut in cuts:
        on = date.fromisoformat(cut["on"])
        assert first_listed <= on <= end, (
            f"{label} cut its price on {on}, outside the spell {first_listed}..{end}"
        )


# ---------------------------------------------------------------------------
# the converse the existing suites do not cover, stated where it originates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "comp", "as_of"), _cases())
def test_a_comp_that_cannot_say_when_it_was_removed_claims_no_rung(label, comp, as_of) -> None:
    """**The violated direction**: `removed_on is None ⇒ removal_class is None`.

    F4-S8's own `test_the_number_and_the_rung_beside_it_agree` checks only
    `rung is None ⇒ days is None`, and every comp in its hand-built fixture
    sets `first_listed` — so neither suite covered this direction, which is the
    entire reason the defect survived a passing gate.

    Asserted here against the real `StitchedComp.removed_on` property rather
    than against a re-implementation of it, so that a change to *the product's*
    two `None` cases is caught rather than mirrored.
    """
    from rentcomp.models.domain import StitchedComp

    stitched = StitchedComp.model_validate(comp)
    if stitched.removed_on is None:
        assert stitched.removal_class is None, (
            f"{label} cannot say when it was removed (removed_on is None) yet claims the "
            f"{stitched.removal_class!r} rung. days_since_removal will be null beside a "
            "populated badge, and the row renders 'removed nulld'"
        )


def test_the_invariant_is_not_vacuous_on_this_corpus() -> None:
    """The complement of the test above: this corpus must actually contain
    removed comps with a real removal date, or the converse check passes by
    having nothing to check.

    Without this, deleting `first_listed` from every fixture would make
    `removed_on` `None` everywhere — and if `removal_class` went with it, the
    converse test would go green over a corpus with no removals in it at all.
    """
    from rentcomp.models.domain import StitchedComp

    answered = 0
    for path in _pull_files():
        _, comps = _load(path)
        for comp in comps:
            if StitchedComp.model_validate(comp).removed_on is not None:
                answered += 1
    assert answered >= 90, (
        f"only {answered} comps in the whole pre-shaped corpus can say when they were "
        "removed; the converse invariant above is close to vacuous"
    )
