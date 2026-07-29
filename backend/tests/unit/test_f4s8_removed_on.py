"""F4-S8 — `StitchedComp.removed_on`, the datum the ladder is measured from.

Developer-authored (AGENT_DEVELOPER.md step 4: the Layer-1 tests QA's plan
calls for but does not itself write). QA's files own the *classification* —
which rung a removal lands on at which boundary. This file owns the thing
those rungs are computed from and that the wire now reports beside them:
**when the unit actually left the market**.

Layer 1 (ARCHITECTURE.md §8): `shape_raw_pull` is importable and callable
with plain dicts and plain dates. No HTTP, no browser, no clock.

WHY THE PROPERTY NEEDS ITS OWN TESTS AT ALL
--------------------------------------------
`removed_on` is *reconstructed* — `first_listed + effective_dom` — rather
than stored, so that it cannot drift from the DOM the rest of the pipeline
uses. The price of that choice is that it inherits F4-S3's definition of
`effective_dom` ("final removal − first listed"), and if that definition ever
changes, the reconstruction silently starts naming the wrong day. So these
tests never assert the identity against itself: every one of them checks
`removed_on` against the ``removedDate`` the raw records literally carried.

The overlapping-chain case below is the one where the two readings actually
diverge, and it is the reason this file exists rather than a one-line
docstring claim.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from f4s8_records import FULL_YEAR, closed_record, open_record
from rentcomp.models.domain import StitchedComp
from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

AS_OF = date(2026, 6, 1)


def _shape(records: list[dict], as_of: date = AS_OF) -> tuple[StitchedComp, ...]:
    return shape_raw_pull(records, [], Config(), as_of, *FULL_YEAR)


# ---------------------------------------------------------------------------
# Provenance — prove the code under test is THIS tree's, do not assert it
# ---------------------------------------------------------------------------


def test_the_rentcomp_under_test_is_the_one_beside_these_tests() -> None:
    """The `.venv` is shared across worktrees and its editable install's
    `.pth` names ONE checkout's `backend/src` (WORKFLOW.md §2). A worktree run
    without `PYTHONPATH` therefore collects *these* test files while importing
    *another* tree's `rentcomp` — and since every story here edits the same
    modules, the result is a green run that proves nothing about the branch.

    This is a real trap and not a hypothetical: bare `python -c "import
    rentcomp.pipeline.shape"` from this worktree resolves to the main
    checkout. Naming both paths in the failure message is the difference
    between a five-minute fix and an hour.
    """
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


# ---------------------------------------------------------------------------
# The property itself
# ---------------------------------------------------------------------------


def test_removed_on_is_the_date_the_record_reported() -> None:
    """The simple case, stated against the raw input rather than the DOM."""
    removed = date(2026, 5, 28)
    comps = _shape(
        [closed_record(id_="r1", address="100 W Removed St", listed=date(2026, 5, 26), removed=removed)]
    )
    assert len(comps) == 1
    assert comps[0].removed_on == removed


def test_removed_on_is_the_chains_latest_removal_not_its_last_listed_spell() -> None:
    """The case the reconstruction has to get right to be worth having.

    Two overlapping observations of one unit: the spell listed *second* ends
    *first*. `chain[-1].removed` is 2026-02-15; the day the unit actually left
    the market is 2026-03-01. F4-S3 already fixed `effective_dom` for exactly
    this shape (two real comps were under-reported by five to six weeks), and
    a `removed_on` reading `chain[-1]` would put "removed 106d ago" on a row
    that left the market 92 days ago — and, at the bottom of the ladder, would
    move a live pending removal onto a rung it has not reached.
    """
    records = [
        closed_record(
            id_="ov-a",
            address="110 W Overlap St",
            listed=date(2026, 1, 1),
            removed=date(2026, 3, 1),
        ),
        closed_record(
            id_="ov-b",
            address="110 W Overlap St",
            listed=date(2026, 2, 1),
            removed=date(2026, 2, 15),
        ),
    ]
    comps = _shape(records)
    assert len(comps) == 1, f"overlapping spells are one continuous vacancy, got {len(comps)}"
    assert comps[0].removed_on == date(2026, 3, 1), (
        "removed_on must be the LATEST removal the chain observed; 2026-02-15 is the last "
        "spell by listed date, which is not the same thing once two spells overlap"
    )


def test_a_still_listed_unit_has_no_removal_date_at_all() -> None:
    """NORTH_STAR's most important distinction, in this field.

    A censored comp's `effective_dom` is a floor measured to the pull date, so
    `first_listed + effective_dom` reconstructs **the pull date** — a
    plausible-looking day on which nothing happened. Returning `None` is what
    keeps "still on market" from being reported as "removed today", which is
    the same 0-day removal the ladder's bottom rung exists to distrust.
    """
    comps = _shape([open_record(id_="a1", address="120 W Active St", listed=date(2026, 3, 1))])
    assert len(comps) == 1
    assert comps[0].censored is True
    assert comps[0].removed_on is None, (
        f"a still-active listing reported removed_on={comps[0].removed_on!r}; it has not been "
        "removed, let alone leased"
    )


@pytest.mark.parametrize("days_since", [0, 1, 6, 7, 41, 42, 400])
def test_removed_on_is_exactly_the_quantity_the_classifier_ruled_on(days_since: int) -> None:
    """The ladder and the date beneath it are one fact, not two.

    `removal_class` is a function of `as_of − removed_on`; this asserts the
    subtraction the wire reports reproduces the number the rung was chosen by,
    at every boundary QA pins. A row saying "provisional" beside "removed 3d"
    would be self-contradicting evidence — worse than no date at all.
    """
    removed = AS_OF - timedelta(days=days_since)
    comps = _shape(
        [
            closed_record(
                id_="c1",
                address="130 W Ladder St",
                listed=removed - timedelta(days=20),
                removed=removed,
            )
        ]
    )
    comp = comps[0]
    assert comp.removed_on == removed
    assert (AS_OF - comp.removed_on).days == days_since

    expected = "pending" if days_since < 7 else "provisional" if days_since < 42 else "confirmed"
    assert comp.removal_class == expected


def test_removed_on_moves_with_the_pull_date_and_never_with_the_wall_clock() -> None:
    """Owner ruling 1: `as_of` is the pull's fetch date, carried as data.

    The same records shaped at two different `as_of` values must report the
    *same* `removed_on` — the day the unit left the market is a property of
    the evidence, not of when it was looked at. (What moves with `as_of` is
    the ladder rung and the days-since count derived from it.)
    """
    records = [
        closed_record(
            id_="p1", address="140 W Pull St", listed=date(2026, 1, 1), removed=date(2026, 2, 1)
        )
    ]
    early = _shape(records, as_of=date(2026, 2, 2))[0]
    late = _shape(records, as_of=date(2027, 2, 2))[0]

    assert early.removed_on == late.removed_on == date(2026, 2, 1)
    assert (early.removal_class, late.removal_class) == ("pending", "confirmed"), (
        "the rung is what moves with the pull date, not the removal date under it"
    )


def test_a_hand_built_comp_with_no_stitched_start_infers_nothing() -> None:
    """`first_listed` is `None` only for a `StitchedComp` a test constructed
    directly (see that field's own note). With no start date there is no
    removal date to reconstruct, and guessing one from `effective_dom` alone
    would invent a day the evidence never named."""
    comp = StitchedComp(
        address="150 W Handbuilt St",
        lat=41.9,
        lng=-87.68,
        beds=2.0,
        initial_ask=2000.0,
        effective_dom=30,
        censored=False,
        removal_class="confirmed",
        cohort_year=2026,
    )
    assert comp.first_listed is None
    assert comp.removed_on is None
