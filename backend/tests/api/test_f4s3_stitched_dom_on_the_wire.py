"""F4-S3 [INVARIANT] — the stitched DOM at the contract boundary.
QA-authored, written RED before the developer starts.

Layer 2 (pytest API contract, `POST /api/derive` over `TestClient`). One
file, two assertions — deliberately thin, and this is the whole of this
story's L2 coverage.

WHY ANYTHING AT L2 AT ALL, WHEN THE VALUE IS FULLY ASSERTABLE AT L1
-------------------------------------------------------------------
AGENT_QA.md's split rule: exhaustive below, representative above.
`tests/unit/test_f4s3_stitch_properties.py` owns the merge rule over
generated dates and `tests/unit/test_f4s3_real_pull_chain_boundary.py` owns
the two real records' corrected numbers. Neither can see whether the
corrected number is the one that leaves the process.

`effective_dom` is not an ordinary field. It is the kNN **target** (D19a) and
the event time `stats/` feeds to the Kaplan-Meier estimator, and it crosses
from `StitchedComp` to `DerivedComp` inside `pipeline/derive.py`. A stitcher
that computes 205 correctly while the wire still carries 165 — because the
DOM was re-derived, cached, or copied from a stale field somewhere along that
crossing — passes every L1 test in this story and still mis-prices the unit.
That failure is invisible from L1 and invisible from a browser.

Decision procedure: "given curation state X the API returns Y" — Q2, L2.

Deliberately NOT here: the merge boundary (a loop over thresholds — L1's
job), the golden file, the evidence-base count, and anything about how the
number is rendered. There is **no Playwright spec for this story at all**:
F4 is a system flow, this story changes no component, no layout, no
navigation and no user interaction, and a browser assertion on a computed
integer is the exact smell AGENT_QA.md names.

REAL DATA, ZERO LIVE CALLS
--------------------------
`pull_ref="ws1-real"` routes the committed 539-record gate pull
(`fixtures/live-samples/`) through the real shaping chain — the same ref
`test_f4s2_duplicate_units_real_pull.py` uses. Nothing here touches the
network (D17).
"""

from __future__ import annotations

import pytest

REAL_PULL_REF = "ws1-real"

#: The two comps in the committed pull whose chain ends after their last
#: spell by listed date. Verbatim from `fixtures/live-samples/`; see
#: `tests/unit/test_f4s3_real_pull_chain_boundary.py` for the derivation.
#:
#:   address              stitched start   reported   observed through   correct
TRUNCATED_DOMS = [
    ("2350 S Leavitt St", "2025-09-11", 91, "2026-01-17", 128),
    ("2453 W 46th Pl", "2025-10-27", 165, "2026-05-20", 205),
]


@pytest.fixture
def real_comps(derive):
    response = derive(pull_ref=REAL_PULL_REF)
    assert response.status_code == 200, (
        f"POST /api/derive with pull_ref={REAL_PULL_REF!r} returned {response.status_code}: "
        f"{response.text[:600]}"
    )
    comps = response.json()["comps"]
    assert comps, "the real ws1-real pull must shape into at least some comps"
    return comps


@pytest.mark.parametrize(
    ("address", "first_listed", "truncated", "observed_through", "correct"), TRUNCATED_DOMS
)
def test_the_corrected_stitched_dom_is_the_one_that_reaches_the_wire(
    real_comps, address, first_listed, truncated, observed_through, correct
) -> None:
    """F4-S3 [INVARIANT] "effectiveDOM = final removal - first listed", at the
    boundary where it becomes evidence the user acts on.

    Both of these under-report by five to six weeks. `effective_dom` is what
    the Kaplan-Meier curve treats as the observed time-to-lease, so a
    truncated value does not widen a band — it moves the curve, and every
    expected-vacancy-days figure derived near that comp's premium moves with
    it. The pull *observed* this unit listed through {observed_through}; that
    date is in the committed fixture, in the record's own history."""
    # Matched on address alone: `DerivedComp` carries no `first_listed` (and no
    # listing id — F13-S1 keys curation by address+unit because ids churn), and
    # each of these units is one group with one stitched chain after F4-S2.
    matches = [comp for comp in real_comps if comp["address"] == address]
    assert matches, (
        f"oracle drift: no comp at {address!r} on the wire "
        f"(addresses present: {sorted({c['address'] for c in real_comps})[:5]}...)"
    )
    assert len(matches) == 1, (
        f"{address!r} reaches the wire as {len(matches)} comps — F4-S2 merged this unit's two "
        "listing ids into one group, so a second comp here means the chain was split"
    )
    comp = matches[0]
    assert comp["effective_dom"] == correct, (
        f"{address} (stitched start {first_listed}) reaches the wire with "
        f"effective_dom={comp['effective_dom']}; the pull observed it listed through "
        f"{observed_through}, which is {correct} days (pre-fix value: {truncated}). "
        "Either the stitcher still ends the chain at its last spell by listed date, or the "
        "corrected value is not the one `pipeline/derive.py` carries onto the wire."
    )
    assert comp["censored"] is False, (
        f"{address} has an observed removal in the pull and must not reach the wire censored — "
        "NORTH_STAR: censored is never leased, and the converse is just as load-bearing"
    )


def test_the_stitcher_fix_does_not_change_how_many_comps_reach_the_wire(real_comps) -> None:
    """The counterweight, at the layer the user meets it.

    A merge rule that refuses negative gaps fixes the threshold-0 defect and
    splits the six overlapping spell pairs in this pull into extra comps —
    putting four physical units back on the wire twice each, which is F4-S2's
    bug returning through F4-S3's front door. On the wire that is 573 comps
    instead of 567, and every one of the four is double-weighted in the cohort
    median behind `premium`, in every bucket count, and in the KM risk set."""
    assert len(real_comps) == 567, (
        f"the real pull reached the wire as {len(real_comps)} comps, expected 567. More means "
        "stitched chains are being split (most likely by treating an overlap as an off-market "
        "gap); fewer means chains are over-merging or records are being dropped."
    )
