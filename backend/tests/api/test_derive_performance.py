"""F0-S2 AC2 — "full derive at 100 comps < 100ms server-side". QA-authored,
written RED before any implementation exists (AGENT_QA.md protocol).

HOW THIS IS KEPT NON-FLAKY (the PM asked explicitly)
----------------------------------------------------
A wall-clock assertion is the one kind of test that fails for reasons that
have nothing to do with the code. Four things make this one trustworthy:

1. **Warm-up runs are excluded.** The first derive pays for Pydantic model
   construction, route resolution and — by design (ADR-001 §3) — the
   once-per-(pull, config) record-shaping memo. The AC is about the
   per-request chain, so the memo is warm before the clock starts. If the
   warm-up ever became the thing under test, the AC's own budget would be
   measuring I/O rather than derivation.
2. **Best-of-N, not mean-of-N.** Scheduler noise, GC pauses and a busy CI box
   only ever make a sample *slower*; the minimum is the closest honest
   estimate of the code's own cost. ``N = 7``.
3. **Headroom is reported, not just pass/fail.** The failure message prints
   min/median/max so a regression is distinguishable from a slow machine at a
   glance.
4. **The budget is overridable** with ``RENTCOMP_PERF_BUDGET_MS`` for a
   genuinely slow box, but it is NOT skippable by default — it is an AC, and
   ADR-001 §3 predicts single-digit milliseconds against this 100ms budget
   (10-50x headroom). If this test is close to the line, the ADR's core
   assumption is wrong and the PM should hear about it.

The measurement is taken around ``TestClient.post``, which includes request
parsing, the whole derivation chain and response serialization, all
in-process. That is "server-side" in the sense the AC means: everything the
Python process does between receiving the body and handing back the bytes,
excluding only the browser and the socket.

EXPECTED RED STATE TODAY: both tests SKIP (conftest's ``derive_client`` skips
while ``POST /api/derive`` is absent).
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

BUDGET_MS = float(os.environ.get("RENTCOMP_PERF_BUDGET_MS", "100"))
WARMUPS = 3
SAMPLES = 7


def test_perf_fixture_really_has_about_one_hundred_comps(derive, perf_pull_ref) -> None:
    """A budget test against a 12-comp fixture proves nothing. The AC names
    100 comps, so the fixture must supply them."""
    response = derive(pull_ref=perf_pull_ref)
    assert response.status_code == 200, (
        f"the 100-comp perf fixture pull {perf_pull_ref!r} did not derive: "
        f"{response.status_code} {response.text[:400]}"
    )
    pulled = response.json()["breakdown"]["pulled"]
    assert 90 <= pulled <= 120, (
        f"perf fixture {perf_pull_ref!r} has {pulled} comps; AC2 is stated at 100 "
        "(90-120 accepted so the fixture can be a realistic pull rather than padded)"
    )


def test_full_derive_at_one_hundred_comps_is_under_the_budget(derive, perf_pull_ref) -> None:
    """AC2. A candidate rent is supplied so the measured chain is the FULL
    one — cohort medians → premiums → anchor → buckets → kNN → guard → KM,
    and per ADR-001 §2.2 everything from the anchor down runs three times, one
    per drift point in the sensitivity band."""
    kwargs = dict(pull_ref=perf_pull_ref, candidate_rent=2100.0, drift_pct=7.0)

    for _ in range(WARMUPS):
        warm = derive(**kwargs)
        assert warm.status_code == 200, f"derive failed during warm-up: {warm.text[:400]}"

    timings_ms: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        response = derive(**kwargs)
        timings_ms.append((time.perf_counter() - started) * 1000.0)
        assert response.status_code == 200

    best = min(timings_ms)
    assert best < BUDGET_MS, (
        f"full derive at 100 comps took {best:.1f}ms (best of {SAMPLES}); budget is "
        f"{BUDGET_MS:.0f}ms. median={statistics.median(timings_ms):.1f}ms "
        f"max={max(timings_ms):.1f}ms. ADR-001 §3 predicts single-digit ms here, so a "
        "result near the line means an unexpected cost (I/O in the chain? the record-shaping "
        "memo missing on every request?) rather than a slow machine. Override the budget with "
        "RENTCOMP_PERF_BUDGET_MS only after ruling that out."
    )
