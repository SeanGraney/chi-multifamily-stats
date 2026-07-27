"""WS-1 — the whole vertical slice, over the REAL committed fixtures
(fixtures/live-samples/fe9de5158f036802.json = Active, 39 records;
6327600317b11d16.json = Inactive, 500 of 690 records — the exact T-S3 gate
pull, GO'd at 337 in-window distinct comps). QA-authored, written RED before
any implementation exists (AGENT_QA.md protocol).

Zero live API calls (D17): both files are already on disk, committed. This
file only reads them.

WHY `derive()` DIRECTLY, NOT `POST /api/derive` OVER HTTP
-----------------------------------------------------------
`storage/pulls.py::load_shaped_pull` currently only resolves `pull_ref`
against pre-shaped synthetic JSON under `fixtures/synthetic/pulls/`. WS-1
also needs a way to route the two REAL raw fixtures through the real
record-shaping chain (`rentcomp.pipeline.shape.shape_raw_pull`, pinned in
`test_ws1_record_shaping.py`) via some `pull_ref` — but exactly how that
wiring should work (a new named ref? a raw-fixture branch inside
`_shape()`? something else?) is the developer's call, not pinned by any ADR
or story text QA has seen. Rather than inventing a THIRD unnecessary
contract on top of the two already flagged in this story's tests, this file
calls `pipeline.derive.derive()` directly — already a real, fixed, pinned
function (F0-S2) — with a `DeriveContext` built from `shape_raw_pull()`'s
own output. This exercises the ENTIRE real pipeline end to end exactly as
`/api/derive` would, without depending on unspecified storage plumbing.
**The pull_ref -> real-fixture wiring itself is exercised only by the L3
Playwright spec** (which drives the actual built app in fixture mode) — see
`e2e/tests/ws1-results-view.spec.ts`. Flagged in the handoff.

SUBJECT PARAMETERS -- QA-INVENTED, FLAG FOR PM CONFIRMATION
-------------------------------------------------------------
Nothing in the PM's scope ruling states a subject sqft, subject lat/lng, or
a specific seasonal window (only "3-year window", no month-day bounds). QA
picked, and flags for confirmation:

* subject_sqft = 1200 (matches a real record's squareFootage in the Active
  fixture; a plausible 3bd Chicago apartment size)
* subject lat/lng ~= the fixture's own comp cluster centroid (41.83,
  -87.665) — the real geocode for 3651 S Wood St was never returned to QA
* window = the full calendar year (01-01..12-31) -- read literally, "3-year
  window" with no month-day narrowing given anywhere in this dispatch
* as_of = 2026-07-27, matching the fixtures' own `lastSeenDate` timestamps
* candidate_rent = $4,500 for a 1,200 sqft unit (== $3.75/sqft) -- NOT
  picked blind: QA measured the real fixtures' own $/sqft distribution
  (`python3` one-liner over both files) -- median ~$1.67/sqft, max observed
  ~$4.18/sqft across 460 records with sqft. $3.75/sqft sits at the extreme
  edge of the observed distribution, well outside any plausible ±3-point
  cluster once premiums are computed against real (not stub) cohort
  medians, which is what makes "the guard trips for a genuine reason"
  (PM's WS-1 scope requirement) a property of the DATA, not a contrived
  input.

EXPECTED RED STATE TODAY: `test_shape_module_exists` FAILS (shared cause
with the other WS-1 record-shaping tests); the rest of this file SKIPS.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.models.requests import DeriveRequest, Subject
from rentcomp.pipeline.derive import DeriveContext, derive
from rentcomp.storage.config import Config

try:
    from rentcomp.pipeline.shape import shape_raw_pull

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - red state before WS-1 lands
    _IMPORT_ERROR = exc

needs_shape = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.pipeline.shape is not importable yet (WS-1 red): {_IMPORT_ERROR}",
)

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
ACTIVE_FIXTURE = FIXTURES_DIR / "fe9de5158f036802.json"
INACTIVE_FIXTURE = FIXTURES_DIR / "6327600317b11d16.json"

FULL_YEAR = ("01-01", "12-31")
AS_OF = date(2026, 7, 27)

SUBJECT = Subject(
    address="3651 S Wood St, Chicago, IL 60609",
    lat=41.83,
    lng=-87.665,
    sqft=1200.0,
    beds=4.0,
    baths=2.0,
)

#: See module docstring: measured off the real fixtures, well past the
#: observed $/sqft ceiling -- chosen to make thin evidence a fact of the
#: data, not an artificially unreachable premium.
CANDIDATE_RENT = 4500.0


def _load_real_comps():
    active = json.loads(ACTIVE_FIXTURE.read_text())
    inactive = json.loads(INACTIVE_FIXTURE.read_text())
    return shape_raw_pull(active, inactive, Config(), AS_OF, *FULL_YEAR)


def test_shape_module_exists() -> None:
    assert _IMPORT_ERROR is None, f"rentcomp.pipeline.shape.shape_raw_pull is required: {_IMPORT_ERROR}"


def test_the_committed_fixtures_exist_and_are_the_gate_sample() -> None:
    """Guards this file's own oracle: if these files ever move, this test
    fails loudly instead of every test below silently skipping/erroring."""
    assert ACTIVE_FIXTURE.is_file(), ACTIVE_FIXTURE
    assert INACTIVE_FIXTURE.is_file(), INACTIVE_FIXTURE
    active = json.loads(ACTIVE_FIXTURE.read_text())
    inactive = json.loads(INACTIVE_FIXTURE.read_text())
    assert len(active) == 39, f"expected the gate's 39 Active records, got {len(active)}"
    assert len(inactive) == 500, f"expected the gate's 500 (of 690) Inactive records, got {len(inactive)}"


@needs_shape
def test_real_data_shapes_into_a_nonempty_set_of_stitched_comps() -> None:
    comps = _load_real_comps()
    assert len(comps) > 0, "the real 539-record pull must shape into at least some StitchedComps"


@needs_shape
def test_no_stub_warning_survives_ws1_over_the_real_pull() -> None:
    """The four F0-S2 `stub_stage` warnings (record shaping, anchor, bucket
    outcome stats, price test) must all be gone once WS-1's real
    implementations replace them."""
    comps = _load_real_comps()
    ctx = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    req = DeriveRequest(pull_ref="ws1-real", subject=SUBJECT, candidate_rent=CANDIDATE_RENT)
    state = derive(req, ctx)

    stub_warnings = [w for w in state.warnings if w.code == "stub_stage"]
    assert stub_warnings == [], (
        f"expected zero stub_stage warnings after WS-1, got: "
        f"{[w.message for w in stub_warnings]}"
    )
    assert "+stubs" not in state.meta.pipeline_version, (
        f"PIPELINE_VERSION still names itself a stub build: {state.meta.pipeline_version!r}"
    )


@needs_shape
def test_a_real_anchor_is_computed_over_the_real_pull() -> None:
    comps = _load_real_comps()
    ctx = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    req = DeriveRequest(pull_ref="ws1-real", subject=SUBJECT, drift_pct=7.0)
    state = derive(req, ctx)
    assert state.anchor is not None, "the real pull has plenty of psf'd comps; an anchor must exist"
    assert state.anchor.rent.mid > 0.0
    assert state.anchor.psf.mid != 1.0, (
        "PLACEHOLDER_ANCHOR_PSF's $1.00/sqft sentinel must not survive over real Chicago data "
        "(the real market here is ~$1.50-2.50/sqft per the gate's own analysis)"
    )


@needs_shape
def test_at_least_one_bucket_has_real_leased_outcome_stats() -> None:
    """Not every bucket needs evidence, but with 337+ in-window comps
    across three cohort years, at least one bucket must have a non-null
    leased_dom_median -- the stub reports `None` in EVERY bucket
    unconditionally, so this alone distinguishes real from stub."""
    comps = _load_real_comps()
    ctx = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    req = DeriveRequest(pull_ref="ws1-real", subject=SUBJECT)
    state = derive(req, ctx)
    assert any(b.count > 0 for b in state.buckets), "some bucket must have members over 337+ comps"
    assert any(b.leased_dom_median is not None for b in state.buckets), (
        "every bucket reporting leased_dom_median=None means the F10-S1 stub is still active"
    )


@needs_shape
def test_the_guard_trips_for_a_genuine_reason_at_the_chosen_thin_evidence_candidate() -> None:
    """PM scope requirement (QUEUE.md row 6): "the hardcoded candidate rent
    for this story must be chosen so the guard genuinely trips ... this
    makes 'always returns GuardResult' the correct answer for this one
    scenario, not a shortcut." Asserted here as: a GuardResult (never a
    CurveResult, F11-S2 is out of scope), whose reason names real thin
    evidence."""
    from rentcomp.models.responses import GuardResult

    comps = _load_real_comps()
    ctx = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    req = DeriveRequest(pull_ref="ws1-real", subject=SUBJECT, candidate_rent=CANDIDATE_RENT)
    state = derive(req, ctx)
    assert state.anchor is not None, "the price test needs an anchor to express the candidate premium against"
    assert state.price_test is not None
    assert isinstance(state.price_test, GuardResult), (
        f"expected the insufficient-evidence guard at ${CANDIDATE_RENT}/mo for a 1,200 sqft "
        f"unit (~$3.75/sqft, far past the real pull's observed $/sqft ceiling); got a "
        f"{type(state.price_test).__name__} instead"
    )
    assert state.price_test.reason in ("too_few_in_range", "all_censored")
