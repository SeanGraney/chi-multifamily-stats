"""Row 10b — the `days_since_removal` iff, asserted over HTTP on the pulls
the suite actually ships.

`models/responses.py` says of `DerivedComp.days_since_removal`:

    `None` iff `removal_class` is `None`.

F4-S8's own `test_the_number_and_the_rung_beside_it_agree` checks one half of
that (`rung is None ⇒ days is None`) against a fixture it writes itself, in
which every comp carries `first_listed`. **The half it does not check is the
half that was false**, and it was false on `synthetic-basic` — the pull every
other Layer-2 contract test in this directory derives against.

The distinction this file turns on: a contract test that only ever runs
against a fixture authored alongside it proves the code is self-consistent, not
that the artifacts the product ships obey it. So every assertion here is
parametrized over **the committed pre-shaped pulls plus `ws1-real`**, and
`ws1-real` is what makes it a real test rather than a restatement — those 567
comps go through `shape_raw_pull`, so they are the control that says the
product was always right and only the fixtures were wrong.

Layer 2 (D21): `POST /api/derive` in, parsed JSON out. This is a value
assertion about a field in `DerivedState`, so it belongs here and not in a
browser — the *rendering* of "removed 4d — classifying" is F5-S1's, and a
Playwright spec asserting these numbers would be a logic assertion in a
browser costume. Zero network (D17): every ref below is served from disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: `fixtures/synthetic/pulls/` — located from this file, exactly as the Layer-1
#: guard does. See `_pre_shaped_refs`.
PULLS_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "pulls"


def _pre_shaped_refs() -> tuple[str, ...]:
    """Every committed pre-shaped pull, **by directory listing, not by name.**

    This file used to enumerate three refs by hand. That is the same shape as
    the defect this row exists to prevent: `f4s5-cohorts.json` arrived with
    F4-S5, was pre-shaped, was impossible in all 15 of its comps, and was named
    nowhere — the row's own fix instruction said "add `first_listed` to the
    three fixtures" and would have left it broken. It was caught only because
    the Layer-1 guard globs. A hand-kept list one layer up is the same trap
    waiting for the fifth pull, and it is worse here than at Layer 1, because a
    reader who opens this file sees three names and reasonably concludes three
    is the complete set.
    """
    return tuple(sorted(path.stem for path in PULLS_DIR.glob("*.json")))


#: The pre-shaped fixtures, plus the real-shaping control. `ws1-real` is not
#: decoration — see the module docstring. It is spelled out because it is the
#: one ref that is *not* a pre-shaped file: it goes through `shape_raw_pull`,
#: which is exactly why it is the control.
PRE_SHAPED_REFS = _pre_shaped_refs()
ALL_REFS = (*PRE_SHAPED_REFS, "ws1-real")

#: F4-S8's ladder: `< 7` pending, `< 42` provisional, else confirmed.
LADDER = (("pending", 0, 7), ("provisional", 7, 42), ("confirmed", 42, None))


def _rung(days: int) -> str:
    for name, lo, hi in LADDER:
        if days >= lo and (hi is None or days < hi):
            return name
    raise AssertionError(f"{days} falls off the ladder")


@pytest.fixture
def comps_for(derive):
    """``comps_for(ref) -> list[DerivedComp]`` — a loud failure, never a skip."""

    def _comps_for(ref: str):
        response = derive(pull_ref=ref)
        assert response.status_code == 200, (
            f"POST /api/derive over pull_ref={ref!r} returned {response.status_code}: "
            f"{response.text[:600]}"
        )
        comps = response.json()["comps"]
        assert comps, f"pull {ref!r} derived zero comps; every assertion below would be vacuous"
        return comps

    return _comps_for


# ---------------------------------------------------------------------------
# the two directions of the declared iff
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ALL_REFS)
def test_a_row_that_cannot_say_when_never_claims_a_rung(ref, comps_for) -> None:
    """**The violated direction**: `days_since_removal is None ⇒ removal_class
    is None`.

    This is the assertion whose absence let the trap survive a green gate. A
    row carrying a rung beside a null count renders as *"removed nulld —
    classifying"*, and F5-S1's developer — reading an "iff" that promised the
    two travel together — would reasonably write `days_since_removal!` and be
    wrong on the very fixture they are most likely to develop against.
    """
    offenders = [
        (comp["key"], comp["removal_class"])
        for comp in comps_for(ref)
        if comp["days_since_removal"] is None and comp["removal_class"] is not None
    ]
    assert not offenders, (
        f"{len(offenders)} comp(s) in {ref!r} carry a removal rung with no days_since_removal. "
        f"responses.py declares the field `None` iff removal_class is `None`. "
        f"First few: {offenders[:5]}"
    )


@pytest.mark.parametrize("ref", ALL_REFS)
def test_a_row_with_no_rung_never_reports_a_count(ref, comps_for) -> None:
    """The forward direction, on the shipped pulls rather than a hand-built one.

    `0` here would be worse than a crash: *"removed 0d"* printed on a unit that
    is still on the market is the censored/leased confusion NORTH_STAR calls
    the single most important distinction in the system, rendered as fact.
    """
    offenders = [
        (comp["key"], comp["days_since_removal"])
        for comp in comps_for(ref)
        if comp["removal_class"] is None and comp["days_since_removal"] is not None
    ]
    assert not offenders, (
        f"{len(offenders)} comp(s) in {ref!r} report a removal age with no removal rung: "
        f"{offenders[:5]}"
    )


def test_this_file_and_the_server_are_looking_at_the_same_fixtures() -> None:
    """A failing precondition, not a skip (`WORKFLOW.md` §2).

    Two different resolutions are in play and they must agree: this file globs
    `PULLS_DIR` **from its own path**, while the endpoint under test resolves
    pulls through `storage.pulls.fixture_pulls_dir()`, which is derived from
    the *loaded package's* location. In a worktree run whose `PYTHONPATH` names
    another checkout (`WORKFLOW.md` §2 — and on Windows a `:` separator is
    enough to cause it), those two are different directories, and this file
    would parametrize over one corpus while deriving over another. That failure
    is silent by construction, so it gets a loud test rather than a comment.
    """
    from rentcomp.storage.pulls import fixture_pulls_dir

    assert fixture_pulls_dir().resolve() == PULLS_DIR.resolve(), (
        "this test file and the server disagree about where the pre-shaped pulls are.\n"
        f"  this file globs: {PULLS_DIR.resolve()}\n"
        f"  the server reads: {fixture_pulls_dir().resolve()}\n"
        "Set PYTHONPATH to this tree's backend/src (Windows separator is ';') and re-run."
    )
    assert len(PRE_SHAPED_REFS) >= 4, (
        f"globbed only {len(PRE_SHAPED_REFS)} pre-shaped pull(s) {PRE_SHAPED_REFS}; the corpus "
        "held four when this row landed (synthetic-basic, synthetic-100, ws1-anchor-drift, "
        "f4s5-cohorts). A shrinking glob makes every parametrized test below quietly narrower."
    )


@pytest.mark.parametrize("ref", ALL_REFS)
def test_every_pull_can_exercise_the_converse(ref, comps_for) -> None:
    """Every pull must contain at least one *removed* comp.

    Without this, the converse test above passes on a pull where
    `removal_class` is `None` everywhere — which is very close to the state the
    fixtures were actually in, and is exactly how a suite goes green over a
    corpus that proves nothing. Stated per-ref deliberately: an average over
    the corpus would let one healthy pull cover for three empty ones.
    """
    removed = [c for c in comps_for(ref) if c["removal_class"] is not None]
    assert removed, f"{ref!r} has no removed comps — the converse test is vacuous on it"
    assert all(c["days_since_removal"] is not None for c in removed)


def test_the_corpus_can_exercise_the_forward_direction(comps_for) -> None:
    """At least one pull must hold a still-active comp.

    Deliberately corpus-level rather than per-ref, and this is a real change of
    meaning from the per-ref form: `ws1-anchor-drift` (2 comps) and
    `f4s5-cohorts` (15 comps) are *both* all-removed by construction — they
    exist to exercise cohort drift and cohort selection, not censoring. The
    earlier version of this test named `ws1-anchor-drift` as an exemption,
    which is the same hand-maintained-list trap as the ref list itself: the day
    `f4s5-cohorts` was globbed in, a name-based exemption would have failed it
    for being the wrong *kind* of fixture rather than for being wrong.

    So the requirement is stated where it is actually true — of the corpus.
    """
    with_active = {
        ref: sum(1 for c in comps_for(ref) if c["removal_class"] is None) for ref in ALL_REFS
    }
    assert any(with_active.values()), (
        "no pull in the whole corpus has a still-active comp, so the forward direction "
        f"is vacuous everywhere: {with_active}"
    )


# ---------------------------------------------------------------------------
# the number and the badge are one fact, on the shipped pulls
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ref", ALL_REFS)
def test_the_rung_a_row_shows_is_the_rung_its_own_count_implies(ref, comps_for) -> None:
    """F4-S8's agreement check, moved off its private fixture onto the corpus.

    A row reading *"provisional · removed 3d"* is worse than a row with no
    date: it invites the user to distrust the badge that is actually correct.
    The dev's version of this assertion is sound — it simply never ran against
    the data the suite ships.
    """
    wrong = [
        (comp["key"], comp["removal_class"], comp["days_since_removal"])
        for comp in comps_for(ref)
        if comp["removal_class"] is not None
        and comp["days_since_removal"] is not None
        and comp["removal_class"] != _rung(comp["days_since_removal"])
    ]
    assert not wrong, (
        f"{len(wrong)} comp(s) in {ref!r} show a rung their own days_since_removal "
        f"contradicts (key, rung, days): {wrong[:5]}"
    )


@pytest.mark.parametrize("ref", ALL_REFS)
def test_no_row_claims_a_removal_in_the_future(ref, comps_for) -> None:
    """`days_since_removal` is `as_of − removed_on`, and `as_of` is the pull
    date (owner ruling 1). A negative value would mean the pull observed a
    removal that had not happened when the evidence was gathered."""
    negative = [
        (comp["key"], comp["days_since_removal"])
        for comp in comps_for(ref)
        if comp["days_since_removal"] is not None and comp["days_since_removal"] < 0
    ]
    assert not negative, f"{ref!r} reports removals in the future: {negative[:5]}"


# ---------------------------------------------------------------------------
# the concrete row F5-S1 is about to render
# ---------------------------------------------------------------------------


def test_the_suite_default_pull_can_render_the_pending_row_copy(comps_for) -> None:
    """The trap, stated as the thing it breaks.

    `synthetic-basic` is what `conftest.CANONICAL_BODY` derives against, so it
    is what F5-S1's developer will have on screen. Its pending comp must be
    able to say *"removed 3d — classifying"* — a rung, and an integer beside
    it, with no null in between. Asserting the pair rather than the literal
    keeps this test honest if the fixture's dates are ever re-authored.
    """
    pendings = [c for c in comps_for("synthetic-basic") if c["removal_class"] == "pending"]
    assert pendings, (
        "synthetic-basic has no pending comp; the fixture's own note promises each rung of the "
        "removal ladder, and F5-S1's row copy is specified on the pending case"
    )
    for comp in pendings:
        days = comp["days_since_removal"]
        assert isinstance(days, int) and not isinstance(days, bool), (
            f"{comp['key']} is pending but reports days_since_removal={days!r}; the row would "
            'render "removed nulld — classifying"'
        )
        assert 0 <= days < 7, f"{comp['key']} is pending at {days}d, off the ladder's first rung"


def test_the_real_pull_was_never_the_problem(comps_for) -> None:
    """The control, pinned so the diagnosis cannot be quietly rewritten later.

    `ws1-real` goes through `shape_raw_pull`, which always sets `first_listed`.
    Its five pendings report real counts. If this ever goes red, the finding
    behind this row was wrong about where the fault was, and the fix direction
    (repair the fixtures, keep the contract) needs revisiting rather than
    patching.
    """
    comps = comps_for("ws1-real")
    assert len(comps) > 500, f"ws1-real derived {len(comps)} comps; expected the full real pull"
    violations = [
        c["key"]
        for c in comps
        if c["days_since_removal"] is None and c["removal_class"] is not None
    ]
    assert not violations, (
        f"the REAL pull now violates the iff at {len(violations)} comps. This row was opened on "
        "the finding that only the synthetic fixtures did"
    )
    pendings = sorted(c["days_since_removal"] for c in comps if c["removal_class"] == "pending")
    assert pendings and all(0 <= d < 7 for d in pendings), (
        f"ws1-real's pendings report {pendings}; every one must sit on the ladder's first rung"
    )
