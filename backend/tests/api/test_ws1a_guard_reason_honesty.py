"""WS-1a — Bug 2 [INVARIANT]: `PriceTest.reason` must be a HONEST function of
what retrieval actually found, not a hardcoded default. QA-authored, written
RED before any implementation exists (AGENT_QA.md protocol).

BACKGROUND (see the WS-1a dispatch / QUEUE.md row 6a in full)
---------------------------------------------------------------
`pipeline/pricetest.py`'s `_STUB_GUARD_DECISION` makes `price_test()`
unconditionally report `reason="too_few_in_range"` whenever at least one
retrieved neighbour is not censored — regardless of how many of those
neighbours are actually within F11-S3's own ±3-premium-point window. The
justifying comment ("retrieval is a stub, so zero neighbours is genuinely
true") was voided by WS-1 making `select_neighbors` real.

WHY THE OBVIOUS "ASSERT reason==X ON THE $4,500 CANDIDATE" TEST IS NOT
ENOUGH (QA finding, worth restating so the developer doesn't chase the wrong
thing): at WS-1's own hardcoded candidate ($4,500/mo, chosen to land in
genuinely thin evidence), the real usable-in-range count is 0 (<3), so
`too_few_in_range` is ALREADY numerically correct today — by accident, not
because the code computed it. A test that only pins that one number would
stay green whether the code is honest or not, and would not catch the bug.

THE TEST THAT ACTUALLY DISTINGUISHES HONEST FROM DEFAULTED (QA finding —
this is real data, not a contrived edge case): at a candidate rent near the
real pull's OWN anchor (~$2,025/mo, i.e. close to market — a value a user
would plausibly type into the price-test field), the real evidence is NOT
thin — QA measured >= 18 of the retrieved neighbours within +/-3 premium
points and NONE censored — yet the current code still reports
`too_few_in_range`, because the code never actually looks at distance or
censoring to decide which reason to report. `test_reason_is_never_too_few_
in_range_when_the_real_evidence_is_not_thin` below is RED today because of
exactly this.

SCOPE NOTE — AN OPEN QUESTION FLAGGED TO THE PM, NOT ADJUDICATED HERE
------------------------------------------------------------------------
F11-S2 (the KM curve) and F11-S3's guard-vs-curve BRANCH SELECTION remain
explicitly out of scope for this story (`CurveResult` cannot be constructed
yet). That means: even in the well-evidenced case above, the endpoint has no
way to return anything but a `GuardResult` — there is no third `reason`
value in the wire contract (`Literal["too_few_in_range", "all_censored"]`)
for "evidence is actually sufficient." The tests below deliberately do NOT
assert what `reason` MUST equal in that well-evidenced case (that would be
QA picking an answer to a question the story doesn't own) — they only
assert what it must NOT dishonestly claim. If the developer's fix cannot
satisfy this without inventing a new wire value, that is exactly the kind of
finding that belongs back in front of the PM/owner (Semantic Change
Protocol), not something to route around silently in the implementation.

EXPECTED RED STATE TODAY: `test_reason_is_never_too_few_in_range_when_the_
real_evidence_is_not_thin` FAILS. The others pass today by coincidence
(documented per-test) but are pinned as permanent regression coverage.
"""

from __future__ import annotations

#: F11-S3 [DEFAULT] threshold, expressed in the wire units `Neighbor.distance`
#: already uses (premium is a ratio, e.g. 0.04 == 4%, so 3 points == 0.03).
IN_RANGE_DISTANCE = 0.03

#: WS-1's own hardcoded thin-evidence candidate (QUEUE.md row 6 / the WS-1
#: dispatch) — $4,500/mo for the 1,200 sqft WS-1 subject.
WS1_THIN_EVIDENCE_RENT = 4500.0


def _usable_in_range_count(neighbors: list[dict]) -> int:
    return sum(1 for n in neighbors if n["distance"] <= IN_RANGE_DISTANCE)


def test_reason_is_never_too_few_in_range_when_the_real_evidence_is_not_thin(derive) -> None:
    """The distinguishing case (see module docstring): a candidate near the
    real pull's own anchor has plenty of usable, uncensored evidence within
    +/-3 premium points. `too_few_in_range` cannot be honestly reported here.

    Finds the anchor itself first (candidate_rent=None still returns an
    anchor), then re-derives at that rent so this test does not hardcode a
    dollar figure that could drift if the real fixtures or the anchor
    formula ever change.
    """
    anchor_response = derive(pull_ref="ws1-real", candidate_rent=None)
    assert anchor_response.status_code == 200, anchor_response.text[:600]
    anchor = anchor_response.json()["anchor"]
    assert anchor is not None, "the real ws1-real pull must produce an anchor"
    at_market_rent = anchor["rent"]["mid"]

    response = derive(pull_ref="ws1-real", candidate_rent=at_market_rent)
    assert response.status_code == 200, response.text[:600]
    price_test = response.json()["price_test"]
    assert price_test is not None

    # F11-S2 EDIT (developer, flagged to the PM for QA's independent review).
    # The original assertion here was `state == "insufficient_evidence"`,
    # justified by this module's own SCOPE NOTE: "F11-S2 ... remain explicitly
    # out of scope for this story (`CurveResult` cannot be constructed yet).
    # That means: even in the well-evidenced case above, the endpoint has no
    # way to return anything but a `GuardResult`." That premise is exactly
    # what F11-S2 retires — the estimator now exists, and this rent (the real
    # pull's own anchor, 21 uncensored neighbours all within +/-3 premium
    # points) is precisely where the curve branch is supposed to win.
    #
    # The test's actual CLAIM is untouched: "`too_few_in_range` cannot be
    # honestly reported here." A curve satisfies that strictly more strongly
    # than a differently-reasoned guard does, so the claim is asserted against
    # whichever branch the endpoint returns rather than against a branch
    # choice this module never owned.
    if price_test["state"] == "curve":
        assert "reason" not in price_test, (
            "a CurveResult carries a guard reason — the two arms must not be confusable"
        )
        return

    in_range = _usable_in_range_count(price_test["neighbors"])
    all_censored = bool(price_test["neighbors"]) and all(
        n["censored"] for n in price_test["neighbors"]
    )
    assert in_range >= 3 and not all_censored, (
        "fixture/environment assumption broke: QA measured >= 18 in-range, uncensored "
        f"neighbours at the real anchor rent (${at_market_rent}); got in_range={in_range}, "
        f"all_censored={all_censored} — re-verify this is still the distinguishing case before "
        "trusting the assertion below"
    )
    assert price_test["reason"] != "too_few_in_range", (
        f"at ${at_market_rent}/mo (the real pull's own anchor), {in_range} usable neighbours "
        "are within +/-3 premium points and none are censored — reporting "
        "'too_few_in_range' here is a stated reason with no evidence behind it, exactly the "
        "honesty failure NORTH_STAR names. See this test module's SCOPE NOTE if the fix has "
        "nowhere honest to land this case in the current wire contract."
    )


def test_too_few_in_range_implies_fewer_than_three_usable_neighbors_in_range(derive) -> None:
    """The general consistency invariant, over every candidate this story's
    real data can produce: whenever the response claims 'too_few_in_range',
    that must be true of the neighbours it actually returned. Passes today
    only by coincidence at the two rents tried (see module docstring) — kept
    as permanent regression coverage, not proof of a fix."""
    for candidate_rent in (WS1_THIN_EVIDENCE_RENT, 2000.0, 2500.0):
        response = derive(pull_ref="ws1-real", candidate_rent=candidate_rent)
        assert response.status_code == 200, response.text[:600]
        price_test = response.json()["price_test"]
        if price_test is None or price_test["state"] != "insufficient_evidence":
            continue
        if price_test["reason"] != "too_few_in_range":
            continue
        in_range = _usable_in_range_count(price_test["neighbors"])
        assert in_range < 3, (
            f"candidate_rent={candidate_rent}: reason='too_few_in_range' but {in_range} "
            "usable neighbours are within +/-3 premium points — the stated reason is false"
        )


def test_all_censored_implies_every_returned_neighbor_is_censored(derive) -> None:
    """The mirror invariant for the other reason value. This direction is
    not the bug (the current code already computes it correctly) — pinned so
    a future refactor of the honest computation cannot silently break it."""
    for candidate_rent in (WS1_THIN_EVIDENCE_RENT, 2000.0, 2500.0):
        response = derive(pull_ref="ws1-real", candidate_rent=candidate_rent)
        assert response.status_code == 200, response.text[:600]
        price_test = response.json()["price_test"]
        if price_test is None or price_test["state"] != "insufficient_evidence":
            continue
        if price_test["reason"] != "all_censored":
            continue
        assert price_test["neighbors"], "reason='all_censored' with an empty neighbor list"
        assert all(n["censored"] for n in price_test["neighbors"]), (
            f"candidate_rent={candidate_rent}: reason='all_censored' but at least one "
            "returned neighbor is not censored — the stated reason is false"
        )


def test_the_ws1_thin_evidence_candidate_still_trips_the_guard_for_a_named_reason(derive) -> None:
    """The real WS-1 regression named in the dispatch: the hardcoded $4,500
    candidate must still land on GuardResult (never a curve — out of scope),
    with a reason drawn from the real enum. This does not by itself prove
    the reason is honestly COMPUTED (see the coincidence note in the module
    docstring) — `test_too_few_in_range_implies_fewer_than_three_usable_
    neighbors_in_range` and the anchor-rent test above own that."""
    response = derive(pull_ref="ws1-real", candidate_rent=WS1_THIN_EVIDENCE_RENT)
    assert response.status_code == 200, response.text[:600]
    price_test = response.json()["price_test"]
    assert price_test is not None
    assert price_test["state"] == "insufficient_evidence", (
        f"expected the guard at ${WS1_THIN_EVIDENCE_RENT}/mo; got state={price_test['state']!r}"
    )
    assert price_test["reason"] in ("too_few_in_range", "all_censored")
