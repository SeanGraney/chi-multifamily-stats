"""F4-S9 QA VERIFY — the three facts the implementation made newly assertable.

QA-authored *after* the developer's diff, unlike
`test_f4s9_pull_orchestrator.py` / `test_f4s9_live_guard.py` /
`tests/api/test_f4s9_search_route.py`, which were written red against the six
acceptance criteria before any code existed. Nothing here is a new AC. Each
one pins an OUTCOME that the chosen implementation put in reach and that no
existing spec covers, so a later refactor cannot quietly undo it:

1. **The memo's staleness guard, on a REWRITE rather than an append.**
   `storage/pulls.py::_evidence_version` fingerprints `(name, size,
   mtime_ns)`. The developer's own
   `test_new_evidence_under_an_existing_ref_is_not_served_from_the_memo`
   covers "a new file appeared", which changes the NAME SET whatever the
   clock did. `force_refresh` is the other case: it overwrites an existing
   signature in place, so the name set is unchanged and the guard rests
   entirely on size-or-mtime. Pinned as the outcome (fresh comps, warm memo),
   never as the fingerprint's composition, so restoring a content hash or
   moving to a monotonic write counter passes unchanged.

2. **A truncated query's already-paid-for page survives.** Resume granularity
   is per-QUERY, not per-page (a logged `[DEFAULT]` in `client/pull.py`), so a
   query that dies after page 0 re-buys page 0 on the next attempt. That is a
   cost, and it is disclosed. What must NOT also be true is the D24
   `[INVARIANT]` half — that the bytes page 0 already cost are still on disk.
   Asserted here because AC2's Layer-1 file only ever fails a query at its
   FIRST call, so no existing spec exercises a half-finished query at all.

3. **A `force_refresh` that fails does not destroy what it was refreshing.**
   REFRESH re-sends every fetchable window (the PM's ruling: the alternative
   is a button that reports success having fetched nothing). A window whose
   re-fetch then fails must keep its previous evidence AND be named in
   `missing`. Spec §7's "never mix fresh and stale" reconciliation is
   explicitly deferred to F3-S3/F13-S1 — this pins the honesty floor that has
   to hold in the meantime: the mixture is DISCLOSED, not silent.

MONEY: every test here that sets `RENTCOMP_LIVE=1` also injects an
`httpx.MockTransport` and asserts the suite's autouse socket kill switch saw
nothing — the same discipline as the three red-first files.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.pull import run_pull
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.cache import raw_response_paths
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger
from rentcomp.storage.pulls import load_shaped_pull

TODAY = date(2026, 7, 27)

#: 3 cohort years, 6 fetchable windows, none structurally empty.
SEARCH = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}

#: The same search over ONE cohort year — 2 fetchable windows. Used where a
#: test needs to reason about individual calls rather than about a whole plan.
ONE_YEAR = {**SEARCH, "years_back": 1}

FAKE_KEY = "qa-f4s9-verify-fake-key-not-a-real-credential"


def _record(n: int, *, price: int) -> dict:
    """One synthetic record. `price` is what these tests watch travel from the
    wire to `StitchedComp.initial_ask`."""
    return {
        "id": f"f4s9-verify-{n}",
        "formattedAddress": f"{700 + n} S Verify Ave, Chicago, IL 60609",
        "addressLine1": f"{700 + n} S Verify Ave",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": price,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 950,
        "listedDate": "2026-03-01T00:00:00.000Z",
        "status": "Active",
    }


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    load_shaped_pull.cache_clear()
    yield root
    load_shaped_pull.cache_clear()


@pytest.fixture
def live_env(home, monkeypatch) -> Path:
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return home


@pytest.fixture
def fixture_env(home, tmp_path, monkeypatch) -> Path:
    """Fixture mode with a saved response per fetchable window of `SEARCH`.

    `reseed(price)` rewrites every saved response with a new price, which is
    how test 1 produces *different* evidence at the *same* signature.
    """
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return fixtures


def reseed(fixtures: Path, price: int) -> None:
    for index, query in enumerate(fetchable_queries(plan_pull_queries(TODAY, **SEARCH))):
        body = json.dumps([_record(index, price=price + index)]).encode("utf-8")
        (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)


# ===========================================================================
# 1. the memo's staleness guard, on a REWRITE
# ===========================================================================


def test_a_force_refresh_that_changes_the_evidence_is_not_served_stale(
    fixture_env, home
) -> None:
    """`force_refresh` overwrites existing signatures IN PLACE.

    Every raw response is re-written under the same `y<year>-<status>-off000`
    name, so the file NAME SET is identical before and after — the one signal
    an append-only fingerprint could rely on. If the memo goes on serving the
    pre-refresh comps, the user clicked REFRESH, was told it succeeded, and is
    still looking at the old numbers.

    Deliberately no `cache_clear()` between the two loads: the staleness only
    exists while the memo is warm, and clearing it would make this pass
    against the bug (the same note `test_completing_a_partial_pull_is_visible_
    to_the_loader` carries).

    The new prices are the SAME BYTE LENGTH as the old ones (2100 -> 2900), so
    a fingerprint that watched only file size would not save this.
    """
    reseed(fixture_env, 2100)
    outcome = run_pull(**SEARCH, today=TODAY)
    before = load_shaped_pull(outcome.pull_ref, Config())
    assert sorted(c.initial_ask for c in before.comps) == [2100.0 + n for n in range(6)]

    names_before = {p.name for p in raw_response_paths(outcome.pull_ref)}
    sizes_before = {p.stat().st_size for p in raw_response_paths(outcome.pull_ref)}

    reseed(fixture_env, 2900)
    run_pull(**SEARCH, today=TODAY, force_refresh=True)

    assert {p.name for p in raw_response_paths(outcome.pull_ref)} == names_before, (
        "this test is only meaningful while a refresh reuses the same filenames; it no "
        "longer does, so fix the test rather than the assertion"
    )
    assert {p.stat().st_size for p in raw_response_paths(outcome.pull_ref)} == sizes_before, (
        "the refreshed bodies are no longer the same byte length as the originals, so this "
        "test no longer exercises a size-blind fingerprint — fix the fixture prices"
    )

    after = load_shaped_pull(outcome.pull_ref, Config())
    assert sorted(c.initial_ask for c in after.comps) == [2900.0 + n for n in range(6)], (
        "after a force_refresh replaced every response in place, the loader still returns "
        "the PRE-refresh comps from its memo. The user pressed REFRESH, was told it "
        "worked, and is reading stale numbers. `storage/pulls.py::_evidence_version` must "
        "distinguish two different bodies filed under the same name (ADR-001's erratum, "
        "restored by F4-S9)."
    )


def test_a_refresh_that_changes_nothing_still_hits_the_memo(fixture_env, home) -> None:
    """The complement, so "always miss" cannot satisfy the test above.

    ADR-001 §3 introduced the memo to keep the file reads and the whole
    shaping chain off the derive path; a guard that defeated it would trade a
    staleness bug for a performance one (F0-S2's AC2 is <100ms at 100 comps).
    """
    reseed(fixture_env, 2100)
    outcome = run_pull(**SEARCH, today=TODAY)

    load_shaped_pull.cache_clear()
    load_shaped_pull(outcome.pull_ref, Config())
    hits_before = load_shaped_pull.cache_info().hits
    load_shaped_pull(outcome.pull_ref, Config())

    assert load_shaped_pull.cache_info().hits == hits_before + 1, (
        "two identical loads of an untouched ref both missed the memo — the staleness "
        "guard is keying on something that varies between two identical calls"
    )


# ===========================================================================
# 2. a truncated query's paid-for page survives
# ===========================================================================


def test_a_query_that_dies_after_page_one_keeps_the_page_it_paid_for(
    live_env, no_network
) -> None:
    """AC2's [INVARIANT] half at page granularity — the half that must hold.

    A query claiming 3 records answers page 0 with 2 and then 500s on page 1.
    The query is not satisfied, so the pull is partial and says so. But page 0
    was a 200, it was billed, and D24 is unconditional about it: those bytes
    are on disk and no later failure rolls them back.

    **The COST half is a logged `[DEFAULT]`, not an invariant, and is
    deliberately not asserted here.** Resume is per-query, so the retry re-buys
    page 0 — 1 wasted call per truncated window. That is disclosed in
    `client/pull.py`'s docstring and reported to the PM as a follow-up
    candidate; pinning it as a test would cement it. What this test forbids is
    the strictly worse outcome: paying for page 0 twice AND having lost it in
    between.
    """
    sent: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        offset = request.url.params.get("offset") or "0"
        sent.append(offset)
        if offset == "0":
            return httpx.Response(
                200,
                json=[_record(1, price=2000), _record(2, price=2001)],
                headers={"X-Total-Count": "3"},
            )
        return httpx.Response(500, json={"error": "page 1 never arrived"})

    outcome = run_pull(**ONE_YEAR, today=TODAY, transport=httpx.MockTransport(answer))

    assert "0" in sent and any(o != "0" for o in sent), (
        f"this test needs a query that actually paginated; it sent offsets {sent}"
    )
    assert outcome.complete is False, (
        "a query that got 2 of the 3 records it was told exist is not satisfied — "
        "reporting it complete would hide a real gap in the evidence"
    )

    stored = {p.stem for p in raw_response_paths(outcome.pull_ref)}
    assert stored == {"y2026-active-off000", "y2026-inactive-off000"}, (
        f"the page-0 response of each truncated query is not on disk: {sorted(stored)}. "
        "It was a 200, it was billed, and D24 forbids a later failure from rolling it "
        "back — the naive atomic batch that discards 3 good responses because the 4th "
        "429'd is the exact mistake the write-through seam exists to prevent."
    )
    assert no_network == [], f"a live-path test reached for a real socket: {no_network}"


# ===========================================================================
# 3. a failed force_refresh does not destroy what it was refreshing
# ===========================================================================


def test_a_failed_refresh_keeps_the_old_evidence_and_names_the_gap(
    live_env, no_network
) -> None:
    """REFRESH re-sends every fetchable window; some of them can fail.

    Two things must be true at once, and neither is obvious:

    * the window that failed still holds the evidence the user paid for on the
      first pull — a refresh that destroyed evidence on the way to replacing
      it would turn one bad afternoon at RentCast into permanent data loss,
      with a whole cohort gone;
    * the response NAMES that window as missing, because what is on disk for
      it is now older than the rest of the pull. Spec §7's "never mix fresh
      and stale" reconciliation is F3-S3/F13-S1's; the floor that has to hold
      until then is that the mixture is disclosed rather than silent (D24 §5a).
    """
    first = run_pull(
        **ONE_YEAR,
        today=TODAY,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=[_record(1, price=1800)])
        ),
    )
    assert first.complete is True
    original = {p.stem: p.read_bytes() for p in raw_response_paths(first.pull_ref)}
    assert len(original) == 2

    def refresh(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("status") == "Inactive":
            return httpx.Response(503, json={"error": "gone"})
        return httpx.Response(200, json=[_record(2, price=1900)])

    second = run_pull(
        **ONE_YEAR, today=TODAY, force_refresh=True, transport=httpx.MockTransport(refresh)
    )

    assert second.pull_ref == first.pull_ref
    assert second.complete is False
    assert list(second.missing) == ["2026 Inactive"], (
        f"a refresh whose Inactive window failed reports {list(second.missing)!r}. What is "
        "on disk for that window is now older than the rest of the pull, and §7's "
        "reconciliation is deferred — so the least this may do is say so."
    )

    now = {p.stem: p.read_bytes() for p in raw_response_paths(first.pull_ref)}
    assert now["y2026-inactive-off000"] == original["y2026-inactive-off000"], (
        "the refresh destroyed the evidence for the window whose re-fetch then failed. A "
        "response is only ever replaced by a response that actually arrived (D24) — "
        "otherwise a single bad upstream afternoon costs a cohort permanently."
    )
    assert now["y2026-active-off000"] != original["y2026-active-off000"], (
        "the window whose refresh SUCCEEDED still holds its old bytes — force_refresh did "
        "not actually go back to the source (the PM's ruling: a REFRESH that skipped "
        "satisfied windows would report success having fetched nothing)"
    )

    assert load_ledger().calls_this_month == 4, (
        f"expected 2 calls for the first pull and 2 for the refresh, ledger says "
        f"{load_ledger().calls_this_month}"
    )
    assert no_network == [], f"a live-path test reached for a real socket: {no_network}"
