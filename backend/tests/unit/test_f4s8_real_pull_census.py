"""F4-S8 [INVARIANT] — the removal ladder measured on the REAL committed pull.

QA-authored, written test-first. Layer 1: `shape_raw_pull` over the two
committed T-S3 gate fixtures (`fixtures/live-samples/`) — real RentCast
responses, zero network (D17; the suite-wide socket kill switch in
`tests/conftest.py` would stop one anyway).

WHY A CENSUS AND NOT JUST HAND-BUILT FIXTURES
----------------------------------------------
AGENT_QA.md: "an invariant that holds only on hand-made data has caught this
project out before." Every other F4-S8 spec is synthetic by necessity — the
boundaries need dates chosen to the day. This file is the counterweight: it
asserts that all three rungs, the censored state, and the withdrawal-suspect
flag are *actually reachable* on the real evidence, and pins the counts so a
change that silently re-classifies 30 comps has to be blessed rather than
noticed.

AND WHY THE SYNTHETIC BOUNDARY TESTS EXIST ANYWAY
--------------------------------------------------
This file also records, as executable evidence, the fact that motivated the
PM's "no inherited fixture luck" instruction. Measured over this pull, the
inter-chain gaps are::

    44, 59, 61, 63, 80, 120, 122, 123, 135, 143, 145, 148, 189, 193, 229, ...

* the 6-week suspect FLOOR (42d) has its nearest real evidence at 44 — two
  days clear, so an off-by-one at the floor is invisible here;
* the 6-month CEILING is bracketed only by 148 and 189 — every ceiling in
  (148, 189] yields the identical 12 suspects, so this pull cannot tell six
  months from five, or from six-and-a-bit.

`test_f4s8_boundaries.py` is what covers those. This test's job is to keep the
statement true, so that if a future re-pull *does* straddle a boundary,
somebody finds out.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

#: The pull's own fetch date and window — identical to `storage/pulls.py`'s
#: `ws1-real` branch, so what this file measures is what `POST /api/derive`
#: serves.
AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")

#: The census, as it stands today. A deliberate change is re-blessable (edit
#: these numbers in the same commit as the change); an accidental one is what
#: this catches. 528 closed + 39 censored == 567 comps.
EXPECTED_CENSUS = {"confirmed": 493, "provisional": 30, "pending": 5, None: 39}


@pytest.fixture(scope="module")
def real_comps():
    active = json.loads(ACTIVE_FIXTURE.read_text())
    inactive = json.loads(INACTIVE_FIXTURE.read_text())
    return shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)


def test_every_state_in_the_story_is_reachable_on_the_real_pull(real_comps) -> None:
    """All three rungs, the censored non-rung, and the suspect flag occur in
    the committed evidence.

    This is the "is this story about a real phenomenon" test. If the real pull
    had, say, zero pendings, then "pendings are excluded from leased stats"
    would be a rule with no data behind it and every downstream assertion about
    it would be vacuous."""
    census = Counter(comp.removal_class for comp in real_comps)
    for state in ("pending", "provisional", "confirmed", None):
        assert census[state] > 0, (
            f"the committed pull contains no comp in state {state!r}; F4-S8's ladder is not "
            f"exercised by real evidence. Census: {dict(census)}"
        )
    assert sum(1 for c in real_comps if c.withdrawal_suspect) > 0, (
        "no withdrawal-suspect comp in the real pull — the flag is untested against reality"
    )


def test_the_real_pull_census_is_stable(real_comps) -> None:
    """The pinned counts. 5 pendings and 30 provisionals are not a rounding
    error in this evidence base: they are 35 of the 528 closed chains whose
    treatment as leased-or-not moves every bucket median and every KM curve."""
    census = dict(Counter(comp.removal_class for comp in real_comps))
    assert census == EXPECTED_CENSUS, (
        f"the removal-ladder census moved: {census} != {EXPECTED_CENSUS}. If this is deliberate, "
        "re-bless it in the same commit as the change and say why in the handoff"
    )
    assert sum(1 for c in real_comps if c.withdrawal_suspect) == 12, (
        "12 comps in this pull re-list 6 weeks to 6 months after completing a spell"
    )


def test_censored_and_classified_are_mutually_exclusive_on_real_data(real_comps) -> None:
    """NORTH_STAR's most important distinction, on the real pull: a censored
    comp has no rung, and a comp with a rung is not censored. No comp may be
    both, and none may be neither."""
    both = [(c.address, c.unit) for c in real_comps if c.censored and c.removal_class is not None]
    neither = [
        (c.address, c.unit) for c in real_comps if not c.censored and c.removal_class is None
    ]
    assert not both, f"censored comps carrying a removal class: {both[:5]}"
    assert not neither, f"removed comps with no removal class: {neither[:5]}"


def test_every_real_rung_agrees_with_the_dates_the_pull_reported(real_comps) -> None:
    """The ladder re-derived from published fields, independently of the
    classifier.

    A comp's removal date is recoverable from two fields the response already
    carries (`first_listed + effective_dom`, for a chain that ended), so the
    rung can be recomputed from the story's own sentence and compared. This is
    the check that would catch a classifier measuring from the wrong date on
    real data — the synthetic tests all hold DOM fixed, and this one does not
    (real DOMs here span 2 to 746 days)."""
    wrong = []
    for comp in real_comps:
        if comp.censored:
            continue
        removed = comp.first_listed + timedelta(days=comp.effective_dom)
        days_since = (AS_OF - removed).days
        expected = "pending" if days_since < 7 else "provisional" if days_since < 42 else "confirmed"
        if comp.removal_class != expected:
            wrong.append((comp.address, comp.unit, days_since, comp.removal_class, expected))
    assert not wrong, (
        "comps whose rung disagrees with (as_of - removal) recomputed from their own published "
        f"fields: {wrong[:5]}"
    )


def test_the_committed_pull_contains_no_day_zero_removal(real_comps) -> None:
    """THE PM'S OPEN QUESTION, ANSWERED — a census, not a requirement.

    `StitchedComp.effective_dom` is `ge=0`, so a listing removed on its own
    listing date would reach the Kaplan-Meier estimator as a genuine day-0
    *event*, dropping the survival curve below 1 before any time has passed.
    The PM ruled the estimator must report what the data says rather than clamp
    inside `stats/`, which makes "is a same-day removal a lease?" a
    removal-classification question — i.e. potentially this story's.

    **It is not, on this evidence.** There are ZERO day-0 removals in the
    committed pull: no raw spell anywhere in the 539 records has
    `listedDate == removedDate`, and the shortest stitched chain is 2 days. So
    F4-S8 does not have to answer it, and no clamping rule is invented here.

    This restates F4-S3's finding rather than inheriting it, because F4-S8 is
    the story that would own the answer if it ever changed. If this fails: it
    is an escalation, not a bug to fix here. A same-day list-and-remove is far
    more likely a mis-entry or an instant withdrawal than a real 0-day lease,
    and choosing between those changes what every KM curve *means* — a Semantic
    Change Protocol call for the owner. Do not silence it by clamping
    `effective_dom`, and do not silence it by deleting this test."""
    zero_dom = [
        (c.address, c.unit, c.first_listed, c.censored, c.removal_class)
        for c in real_comps
        if c.effective_dom == 0
    ]
    assert not zero_dom, (
        "the pull now contains a stitched chain with effective_dom == 0. Escalate per this "
        f"test's docstring — do not clamp: {zero_dom}"
    )
    assert min(c.effective_dom for c in real_comps) == 2, (
        "the shortest chain in this pull is 2 days; if that changed, re-check the day-0 question"
    )


def test_the_real_pull_cannot_pin_the_suspect_window_boundaries(real_comps) -> None:
    """The reason `test_f4s8_boundaries.py` exists, asserted rather than
    asserted-in-a-comment.

    Every suspect gap in this pull is at least 2 days clear of the 6-week floor
    and every non-suspect gap is at least 5 days clear of a 6x30-day ceiling.
    So an implementation with either boundary off by a day produces the exact
    same 12 suspects here, and only the dedicated synthetic boundary tests can
    tell them apart. If a future re-pull lands a gap ON a boundary, this test
    fails and tells whoever re-pulled that the census above just became
    boundary-sensitive."""
    suspects = sorted(c.effective_dom for c in real_comps if c.withdrawal_suspect)
    assert len(suspects) == 12

    # Re-derive the gaps from the comps themselves: a suspect's group must have
    # a later chain starting 6w-6mo after it ended.
    ends = {}
    for comp in real_comps:
        if comp.censored:
            continue
        ends.setdefault((comp.address, comp.unit), []).append(
            (comp.first_listed, comp.first_listed + timedelta(days=comp.effective_dom))
        )

    gaps = []
    for chains in ends.values():
        chains.sort()
        for (_, end), (nxt_start, _) in zip(chains, chains[1:], strict=False):
            gaps.append((nxt_start - end).days)

    inside = [g for g in gaps if 42 <= g < 180]
    outside = [g for g in gaps if g >= 180]
    assert inside and outside, f"expected gaps on both sides of the ceiling, got {sorted(gaps)}"
    assert min(inside) >= 44, (
        f"a real gap now sits within one day of the 42-day floor ({min(inside)}); the census in "
        "this file is now boundary-sensitive and must be re-read alongside "
        "test_f4s8_boundaries.py"
    )
    assert min(outside) >= 185, (
        f"a real gap now sits close to the 6-month ceiling ({min(outside)}); same warning as "
        "above — the ceiling's day count is no longer a free implementation choice"
    )
