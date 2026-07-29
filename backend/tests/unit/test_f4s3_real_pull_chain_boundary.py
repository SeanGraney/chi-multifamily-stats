"""F4-S3 [INVARIANT] — the stitcher's two known defects on the data they are
actually reachable on. QA-authored, written RED before the developer starts.

Layer 1. Decision procedure Q1: `shape_raw_pull` is importable and takes the
two committed fixture files as plain lists of dicts. Zero live calls (D17) —
`fixtures/live-samples/` is the T-S3 gate's committed evidence, not a network
resource, and the suite-wide socket kill switch in `tests/conftest.py` would
stop one anyway.

WHY THIS FILE EXISTS ALONGSIDE THE PROPERTY TESTS
--------------------------------------------------
`test_f4s3_stitch_properties.py` proves the rules. This file proves they bite
where the money is, on named real records, with the numbers spelled out. Both
defects only became reachable *because* F4-S2 landed: before it, the two
listing ids at each of these addresses were separate comps and their spells
were never stitched into one chain. A property test that goes green over
generated dates but leaves `2453 W 46th Pl` reporting 165 days when the pull
observed 205 has not fixed anything the owner can see.

It also carries two guards that are nobody's acceptance criterion and are the
reason a fix here cannot quietly cost something elsewhere:

  * the evidence base must not change size — the naive repair for the
    negative-gap defect (`0 <= gap < threshold`) *does* change it, from 567
    comps to 573, by re-splitting the four units F4-S2 de-duplicated;
  * shaping must not depend on which response a record arrived in, compared
    as a **multiset**. `test_f4s2_shape_dev.py` makes this comparison with
    `set()`, which is satisfied by a result that gained or lost a duplicate —
    the exact failure F4-S2 was about. `Counter` is strictly stronger; the
    weaker assertion should be replaced by this one rather than kept beside
    it (flagged to the PM, since it is the developer's file, not QA's).
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

#: The pull's own fetch date and the window WS-1 pinned for it — identical to
#: `storage/pulls.py`'s `ws1-real` branch, so what this file measures is what
#: `POST /api/derive` serves.
AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")

#: The comp count the committed pull shapes into today, after F4-S2 merged
#: four duplicated units out of 571. Correcting the chain boundary must not
#: move it: the defect is in where a chain *ends*, not in how spells group.
EXPECTED_COMP_COUNT = 567


def _raw() -> tuple[list[dict], list[dict]]:
    return json.loads(ACTIVE_FIXTURE.read_text()), json.loads(INACTIVE_FIXTURE.read_text())


@pytest.fixture(scope="module")
def real_comps():
    active, inactive = _raw()
    return shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)


def _comp(real_comps, address: str, first_listed: date):
    matches = [c for c in real_comps if c.address == address and c.first_listed == first_listed]
    assert len(matches) == 1, (
        f"oracle drift: expected exactly one comp at {address!r} starting {first_listed}, "
        f"found {len(matches)} ({[(c.unit, c.first_listed) for c in matches]})"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# known bug 2 — "effectiveDOM = FINAL removal - first listed"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "first_listed", "truncated", "observed_through", "correct"),
    [
        ("2350 S Leavitt St", date(2025, 9, 11), 91, date(2026, 1, 17), 128),
        ("2453 W 46th Pl", date(2025, 10, 27), 165, date(2026, 5, 20), 205),
    ],
)
def test_a_stitched_chains_dom_runs_to_the_latest_removal_the_pull_observed(
    real_comps, address, first_listed, truncated, observed_through, correct
) -> None:
    """F4-S3 [INVARIANT]: "effectiveDOM = final removal - first listed."

    These are the only two groups in the committed 539-record pull whose last
    spell *by listed date* is not the last spell to end, so they are the whole
    real-world reach of the defect — and both are 5-6 week under-reports:

        2350 S Leavitt St 1R   listed 2025-09-11, observed through 2026-01-17
                               reported  91 days, actually 128
        2453 W 46th Pl 1       listed 2025-10-27, observed through 2026-05-20
                               reported 165 days, actually 205

    `effective_dom` is the kNN *target* (D19a) and the event time the
    Kaplan-Meier estimator consumes. An under-reported DOM is not a rounding
    error there: it tells the survival curve a unit leased six weeks before it
    did, which pulls expected-vacancy-days down for every candidate rent near
    that comp's premium. NORTH_STAR's whole claim is that these numbers trace
    back to what was observed, and 2026-05-20 *was* observed — it is in the
    committed fixture, in this record's own history."""
    comp = _comp(real_comps, address, first_listed)
    assert comp.effective_dom == correct, (
        f"(pre-fix value was {truncated}) "
        f"{address} (stitched start {first_listed}) reports effective_dom={comp.effective_dom}; "
        f"the pull observed this unit listed through {observed_through}, which is {correct} days. "
        "The chain is ending at its last spell by listed date instead of at the latest removal "
        "it observed."
    )
    assert comp.censored is False, (
        f"{address} has an observed removal in the pull and must not be censored"
    )


# ---------------------------------------------------------------------------
# the guards — a fix here must not cost anything elsewhere
# ---------------------------------------------------------------------------


def test_correcting_the_chain_boundary_does_not_change_the_size_of_the_evidence_base(
    real_comps,
) -> None:
    """The counterweight, and the reason the merge rule cannot simply refuse
    negative gaps.

    Both defects live in the same few lines, and the obvious repair for the
    first one — requiring `0 <= gap` before merging — splits the six
    overlapping spell pairs in this pull into extra chains, taking the pull
    from 567 comps to 573 and putting four physical units back on the wire
    twice each. That is a regression of F4-S2 dressed as a fix for F4-S3.

    Measured against the three candidate rules over this pull:

        `gap < t`         (today)          567 comps, but merges at threshold 0
        `0 <= gap < t`    (naive repair)   573 comps  <- F4-S2 regression
        `max(0, gap) < t` (days off market) 567 comps

    A deliberate change to this number is re-blessable; an accidental one is
    the thing this catches."""
    assert len(real_comps) == EXPECTED_COMP_COUNT, (
        f"the committed pull shaped into {len(real_comps)} comps, expected "
        f"{EXPECTED_COMP_COUNT}. More means chains are being split (most likely by treating an "
        "overlap as an off-market gap, which re-double-counts F4-S2's four duplicated units); "
        "fewer means chains are being over-merged or records dropped."
    )


def test_shaping_the_real_pull_does_not_depend_on_which_response_a_record_arrived_in(
    real_comps,
) -> None:
    """Order is not evidence — asserted as a **multiset**.

    Strictly stronger than the `set()` comparison in `test_f4s2_shape_dev.py`:
    a set is equal whether a comp appears once or twice, so that assertion
    cannot fail for the duplication F4-S2 exists to prevent, and cannot fail
    for a stitcher that loses one copy of a doubled comp in one order only.
    Both of those are live risks in this story, because the six overlapping
    spell pairs it touches are exactly the records that arrive split across
    the two responses."""
    active, inactive = _raw()
    swapped = shape_raw_pull(inactive, active, Config(), AS_OF, *FULL_YEAR)

    forward = Counter(comp.model_dump_json() for comp in real_comps)
    reverse = Counter(comp.model_dump_json() for comp in swapped)
    assert forward == reverse, (
        "shaping the real pull depends on which response a record arrived in; multiset "
        f"difference: {sorted((forward - reverse) | (reverse - forward))[:5]}"
    )


def test_no_stitched_chain_reports_a_negative_or_absent_dom(real_comps) -> None:
    """`effective_dom` is `ge=0` on `StitchedComp`, so a chain whose end date
    precedes its start would raise a `ValidationError` deep inside shaping
    rather than surface as a number. Asserting it here means the failure names
    the comp instead of the field."""
    bad = [(c.address, c.unit, c.first_listed, c.effective_dom) for c in real_comps if c.effective_dom < 0]
    assert not bad, bad


# ---------------------------------------------------------------------------
# the open question the PM asked to be checked, pinned as a census
# ---------------------------------------------------------------------------


def test_the_committed_pull_contains_no_day_zero_removal(real_comps) -> None:
    """CENSUS, NOT A REQUIREMENT — and the answer to the PM's open question.

    `StitchedComp.effective_dom` is `ge=0`, so a listing removed on its own
    listing date would reach the Kaplan-Meier estimator as a genuine day-0
    *event*, putting the survival curve's left boundary below 1 before any
    time has passed. The PM ruled that the estimator must report what the data
    says rather than clamp inside `stats/`, which makes this a removal-
    classification question for F4-S3/F4-S8 rather than an estimator one.

    **There are none in the committed pull.** Zero spells anywhere in the 539
    records have `listedDate == removedDate`, and the shortest stitched chain
    is 2 days. So the question stays queued rather than becoming this story's
    problem — that is the finding, and this test is what makes it stop being
    true out loud.

    If this ever fails: it is an escalation, not a bug to fix here. A same-day
    list-and-remove is far more likely a mis-entry or an instant withdrawal
    than a real 0-day lease, and deciding which of those it is changes what
    every KM curve *means*. That is a Semantic Change Protocol call for the
    owner. Do **not** silence it by clamping `effective_dom` to 1, and do not
    silence it by deleting this test."""
    zero_dom = [
        (c.address, c.unit, c.first_listed, c.censored, c.removal_class)
        for c in real_comps
        if c.effective_dom == 0
    ]
    assert not zero_dom, (
        "the pull now contains a stitched chain with effective_dom == 0. This is a "
        "removal-classification question (is it a mis-entry, an instant withdrawal, or a real "
        "same-day lease?), not a number to clamp — escalate to the PM per this test's "
        f"docstring: {zero_dom}"
    )
