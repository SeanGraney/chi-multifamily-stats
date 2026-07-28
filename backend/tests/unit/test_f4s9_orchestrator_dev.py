"""F4-S9 supporting unit tests — the seams QA's contract left to the developer.

Written during implementation (AGENT_DEVELOPER.md step 4). Everything here is
Layer 1 and none of it duplicates `test_f4s9_pull_orchestrator.py`, which owns
the six acceptance criteria. What is left over is the mechanism *behind* two of
them, plus the one trap the dispatch named as unobservable from outside:

1. **The F3-S4 misfile trap.** `storage/pulls.py::_load_cache_backed_pull`
   buckets a cached response into the Active or Inactive list by testing
   `"inactive" in path.stem`. A response filed under any other naming scheme is
   silently misfiled — and it is silent today because `shape_raw_pull`
   concatenates the two lists, so nothing downstream can see the difference.
   It becomes very loud the moment any story treats the two lists differently
   (F13-S1's refresh reconciliation is the obvious candidate). QA deliberately
   wrote no test for it; these assert on the filenames, which is where the fact
   actually lives.

2. **The record-shaping memo's staleness guard**, asserted at the storage seam
   rather than through a whole pull. `test_completing_a_partial_pull_is_visible_
   to_the_loader` pins the OUTCOME and leaves the mechanism free; this is the
   mechanism I chose (an evidence version in the memo key), tested where it can
   be debugged.

3. **The manifest's new half** (§5a's "which are satisfied, which failed and
   why"), including that a manifest written by F3-S1-era code — no query
   records at all — still reads as a whole pull rather than as a gap.

MONEY: fixture mode throughout. Not one test here sets `RENTCOMP_LIVE`, and
none needs to: filenames, the memo and the manifest are identical on both
paths, and the live path's own guarantees are QA's to assert.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.pull import pull_ref_for, run_pull
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.cache import (
    CacheMissError,
    QueryStatus,
    cache_key,
    raw_response_paths,
    read_manifest,
    write_manifest,
    write_raw_response,
)
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import Ledger, current_month, save_ledger
from rentcomp.storage.pulls import load_shaped_pull

TODAY = date(2026, 7, 27)

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

#: What `storage/pulls.py` requires of a raw response's filename, spelled out
#: here because the requirement is invisible at every other layer.
_STEM = re.compile(r"^y(?P<year>\d{4})-(?P<status>active|inactive)-off(?P<offset>\d{3})$")


def _record(n: int) -> dict:
    return {
        "id": f"f4s9-dev-{n}",
        "formattedAddress": f"{400 + n} N Seam St, Chicago, IL 60609",
        "addressLine1": f"{400 + n} N Seam St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2100 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 950 + n,
        "listedDate": "2026-03-01T00:00:00.000Z",
        "status": "Active",
    }


@pytest.fixture
def fixture_mode(tmp_path, monkeypatch) -> Path:
    """A storage root plus a saved response for every fetchable window."""
    home = tmp_path / "rentcomp-home"
    home.mkdir()
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()

    monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(home))
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))

    for index, query in enumerate(fetchable_queries(plan_pull_queries(TODAY, **SEARCH))):
        body = json.dumps([_record(index)]).encode("utf-8")
        (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)

    load_shaped_pull.cache_clear()
    yield home
    load_shaped_pull.cache_clear()


# ---------------------------------------------------------------------------
# 1. the F3-S4 misfile trap
# ---------------------------------------------------------------------------


def test_every_response_is_filed_under_the_naming_scheme_the_loader_parses(
    fixture_mode,
) -> None:
    """`y2025-inactive-off000` — F3-S4's convention, and a hard requirement.

    Not cosmetic: it carries the cohort year (so a manifest gap can be matched
    back to a file), the status (which is the ONLY thing telling the loader
    which bucket a response belongs in) and the page offset (which is what
    stops two calls of one query overwriting each other).
    """
    outcome = run_pull(**SEARCH, today=TODAY)

    stems = [path.stem for path in raw_response_paths(outcome.pull_ref)]
    assert stems, "the pull filed no raw responses at all"
    for stem in stems:
        assert _STEM.match(stem), (
            f"raw response {stem!r} does not match F3-S4's naming scheme "
            "(y<year>-<status>-off<page>). storage/pulls.py reads the status out of this "
            "filename and nothing else records it."
        )


def test_inactive_responses_are_filed_where_the_loader_looks_for_them(
    fixture_mode,
) -> None:
    """The trap itself: `"inactive" in path.stem` is the whole bucketing rule.

    Misfiling is invisible today — `shape_raw_pull` concatenates the Active and
    Inactive lists, so the comps come out identical either way — which is
    exactly why it is worth pinning here rather than waiting for the first
    story that treats the two lists differently (F13-S1) to discover it.

    Asserted both directions, because "Active" is not a substring of
    "inactive" but "active" is: a naive scheme (`y2025-Active`) would still
    bucket correctly, while `y2025-inact` would not, and only the count
    catches that.
    """
    outcome = run_pull(**SEARCH, today=TODAY)
    plan = fetchable_queries(plan_pull_queries(TODAY, **SEARCH))
    expected_inactive = sum(1 for query in plan if query.status == "Inactive")

    stems = [path.stem for path in raw_response_paths(outcome.pull_ref)]
    filed_inactive = [stem for stem in stems if "inactive" in stem]

    assert len(filed_inactive) == expected_inactive, (
        f"{len(filed_inactive)} of {len(stems)} responses will be read as Inactive by "
        f"storage/pulls.py, but the plan has {expected_inactive} Inactive windows: {stems}"
    )
    assert len(stems) - len(filed_inactive) == len(plan) - expected_inactive, (
        f"the Active responses are not filed as Active: {stems}"
    )


def test_the_year_in_a_filename_is_the_cohort_year_that_produced_it(
    fixture_mode,
) -> None:
    """A pull of 3 cohort years files responses for 3 distinct years — the
    property that makes a gap traceable from the manifest to a file on disk."""
    outcome = run_pull(**SEARCH, today=TODAY)
    plan = fetchable_queries(plan_pull_queries(TODAY, **SEARCH))

    years = {_STEM.match(path.stem)["year"] for path in raw_response_paths(outcome.pull_ref)}
    assert years == {str(query.year) for query in plan}


# ---------------------------------------------------------------------------
# 2. the memo's staleness guard, at the storage seam
# ---------------------------------------------------------------------------


def test_new_evidence_under_an_existing_ref_is_not_served_from_the_memo(
    fixture_mode,
) -> None:
    """ADR-001's erratum, at the seam where it lives.

    The memo used to key on `(root, ref, config)` on the premise that a ref is
    never rewritten. Completing a partial pull rewrites one, so the key now
    carries an evidence version. Written directly against `storage/cache` +
    `load_shaped_pull` — no orchestrator involved — so a regression here is one
    function away from the failure.

    Deliberately no `cache_clear()` between the two loads: the staleness this
    guards against only exists while the memo is warm.
    """
    key = cache_key({"dev": "memo-staleness"})
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))
    write_raw_response(key, "y2026-active-off000", json.dumps([_record(1)]).encode(), meta={})

    first = load_shaped_pull(key, Config())
    assert len(first.comps) == 1

    write_raw_response(key, "y2026-inactive-off000", json.dumps([_record(2)]).encode(), meta={})
    second = load_shaped_pull(key, Config())

    assert len(second.comps) == 2, (
        "the loader served pre-completion evidence from its memo after a second response "
        "landed under the same ref. A missing cohort skews every number downstream (D24 §5a)."
    )
    assert second.digest != first.digest, "the digest must follow the raw bytes at the ref"


def test_an_unchanged_ref_still_comes_back_from_the_memo(fixture_mode) -> None:
    """The complement — the guard must not have turned the memo off.

    Without this, "always miss" passes the test above while putting the file
    reads and the whole shaping chain back on every derive, which is precisely
    what ADR-001 §3 introduced the memo to avoid.
    """
    key = cache_key({"dev": "memo-hit"})
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))
    write_raw_response(key, "y2026-active-off000", json.dumps([_record(1)]).encode(), meta={})

    load_shaped_pull.cache_clear()
    load_shaped_pull(key, Config())
    hits_before = load_shaped_pull.cache_info().hits
    load_shaped_pull(key, Config())

    assert load_shaped_pull.cache_info().hits == hits_before + 1, (
        "an unchanged ref missed the record-shaping memo — the staleness guard is keying on "
        "something that varies between two identical calls"
    )


# ---------------------------------------------------------------------------
# 3. the manifest's new half
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("satisfied", "expected_missing", "expected_complete"),
    [
        ((True, True), (), True),
        ((True, False), ("2025 Inactive",), False),
        ((False, False), ("2026 Active", "2025 Inactive"), False),
    ],
)
def test_a_manifest_reports_exactly_the_windows_that_did_not_arrive(
    fixture_mode, satisfied, expected_missing, expected_complete
) -> None:
    """The manifest is what survives the process, so `missing` /
    `calls_to_complete` / `complete` are read off it rather than off an
    in-memory outcome. One call per missing window is F2-S3's unit."""
    key = cache_key({"dev": "manifest-round-trip", "case": expected_missing})
    labels = ("2026 Active", "2025 Inactive")
    write_manifest(
        key,
        as_of=TODAY,
        window=("01-01", "12-31"),
        planned=6,
        fetchable=2,
        queries=[
            QueryStatus(
                label=label,
                sig=label.lower().replace(" ", "-"),
                satisfied=flag,
                error=None if flag else "gone",
            )
            for label, flag in zip(labels, satisfied, strict=True)
        ],
    )

    manifest = read_manifest(key)
    assert manifest.planned == 6 and manifest.fetchable == 2
    assert manifest.missing == expected_missing
    assert manifest.calls_to_complete == len(expected_missing)
    assert manifest.complete is expected_complete


def test_a_manifest_with_no_query_records_reads_as_a_whole_pull(fixture_mode) -> None:
    """Every manifest F3-S1 wrote, and every one a hand-built fixture writes,
    has no query records at all. Those pulls are whole by construction — the
    alternative (unknown ⇒ report a gap) would put a permanent, false
    "evidence incomplete" warning on the fixtures the whole build runs on."""
    key = cache_key({"dev": "legacy-manifest"})
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))

    manifest = read_manifest(key)
    assert manifest.complete is True
    assert manifest.missing == ()
    assert manifest.calls_to_complete == 0


def test_reading_a_pull_that_was_never_run_is_a_typed_miss(fixture_mode) -> None:
    """`pull_status` on a ref with no entry must be the same typed miss every
    other absent-cache path raises — the API edge branches on it to tell "never
    searched" from "searched and empty"."""
    from rentcomp.client.pull import pull_status

    with pytest.raises(CacheMissError):
        pull_status(pull_ref_for(**SEARCH))


# ---------------------------------------------------------------------------
# 4. the ref
# ---------------------------------------------------------------------------


def test_the_ref_ignores_the_clock_but_not_the_search(fixture_mode) -> None:
    """`pull_ref_for` is a function of the search alone.

    Both halves matter: a ref that varied with the day would re-buy the same
    evidence every morning, and a ref that ignored a search param would serve
    one search's comps for another.
    """
    ref = pull_ref_for(**SEARCH)
    assert ref == pull_ref_for(**SEARCH)

    for field, value in [
        ("radius", 5.0),
        ("bedrooms", "2:3"),
        ("years_back", 2),
        ("window_start_mmdd", "06-01"),
        ("window_end_mmdd", "06-30"),
        ("bathrooms", "1:2"),
    ]:
        assert pull_ref_for(**{**SEARCH, field: value}) != ref, (
            f"changing {field} did not change the ref — two different searches would share "
            "(and overwrite) one another's evidence"
        )


def test_a_retyped_address_still_finds_the_evidence_already_paid_for(
    fixture_mode,
) -> None:
    """F3-S1's address normalization, reaching the ref: a user retyping "S WOOD
    ST" must not silently miss a cache they already paid 6 calls for."""
    shouty = {**SEARCH, "address": "  3651 S WOOD ST,  Chicago, IL 60609 "}
    assert pull_ref_for(**shouty) == pull_ref_for(**SEARCH)
