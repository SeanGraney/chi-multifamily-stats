"""F4-S3 + T-S1 [INVARIANT] — the stitcher golden file. QA-authored, written
RED before the developer starts (AGENT_QA.md protocol).

Layer 1 (pytest unit). Decision procedure Q1: `shape_raw_pull` is importable
and takes plain lists of dicts, a `Config`, a date and two month-day strings.
No request body, no curation state, no browser. A golden file is not a
different *kind* of test — it is one assertion with a large expected value,
and it belongs at the lowest layer that can hold it.

WHAT THE FIXTURE IS
-------------------
`fixtures/synthetic/golden/f4s3-stitcher/`:

    raw_active.json     4 raw RentCast records with no `removedDate`
    raw_inactive.json   17 raw records with one
    expected.json       22 `StitchedComp`s, in `shape_raw_pull` order

21 raw records across **18 groups** — 16 hand-built pathological ones and 2
copied verbatim from the committed live pull, per the PM's dispatch:

    S01  laundered DOM        a re-list resets `listedDate`; the chain is censored
    S02  triple re-list       three spells, two cuts, all leased
    S03  price-UP re-list     a raise is NOT a cut (`cut_history` stays empty)
    S04  fell-through lease   re-listed at week 5 (35d gap) - the AC's own case
    S05  gap == threshold     42d => two comps, the first withdrawal-suspect
    S06  gap == threshold-1   41d => one comp
    S07  OVERLAPPING spells   two listing ids whose intervals overlap (gap < 0)
    S08  no `history` object   the 44-real-record top-level fallback path
    S09  simple censored      one open spell, DOM measured to `as_of`
    S10  300-day gap          two comps, first NOT suspect (gap >= 6 months)
    S11  removal 3d old       `pending`
    S12  no `squareFootage`   survives with `sqft=None`, never dropped
    S13  contiguous chain     gap exactly 0 - a price change, not a re-list
    S14  censored WITH a gap  a 31d gap chain whose final spell is still open
    S15  merge AND split      three spells, one gap of 15d and one of 73d
    S16  two cohorts          the same unit vacant in 2024 and again in 2026
    R01  2453 W 46th Pl       REAL - the pull's 5-event churn extreme (2 ids)
    R02  2411 W 34th Pl #2    REAL - the price-change chain, still active

WHERE `expected.json` CAME FROM — this is the part that makes it a test
-----------------------------------------------------------------------
It was **not** produced by snapshotting `pipeline/shape.py`. It was computed
by a reference stitcher written from the F4-S3 story text alone:

    merge iff days-off-market < threshold, where days-off-market is
      max(0, nextListed - the LATEST removal observed so far in the chain)
    initialAsk       = first spell's price
    effectiveDOM     = final removal - first listed  (gap days count)
    censored         = the chain ends in an open spell; DOM runs to `as_of`
    cutHistory       = every price DROP across consecutive spells
    relistCount      = len(chain) - 1        (see the note on S13/R01 below)
    gapDays          = sum of the off-market days merged over

`extract_spells` (F4-S2), the month-day window rule (F4-S4) and the
pending/provisional/confirmed thresholds (F4-S8) are reused rather than
re-derived — this story does not redefine them, and they already have their
own passing specs.

Against the implementation as it stands today the expected set differs in
**exactly two records**, both the same known defect (`_build_comp` ends the
chain at `chain[-1].removed`, the last spell *by listed date*, instead of the
latest removal observed):

    106 N Golden Ave Unit 1   effective_dom  47  ->  73    (S07, synthetic)
    2453 W 46th Pl   Unit 1   effective_dom 165  -> 205    (R01, real)

Everything else in the file already matches, which is the property a golden
file wants: it is not a wall of unexplained numbers, it is a wall of numbers
that are all currently right except the ones this story exists to fix.

RE-BLESSING (T-S1's actual requirement)
---------------------------------------
`expected.json` is a committed artifact. A pipeline change that moves any
number in it fails this test by design; the fix is to re-author the file
**deliberately**, with the reason recorded in the story that changed it -
never by regenerating it from whatever the code now produces. Two fields are
known to be re-blessable without a semantic change and are called out so a
future diff is not mistaken for a regression:

  * `unit` on R01 - the two real listing ids spell the same unit `# 1` and
    `Unit 1`. Both are correct; which one is displayed is presentational.
  * `relist_count` on S13/R01 - the story lists `relistCount` among the
    merged record's fields but never defines it. Today it counts merged
    *spells*, so a contiguous price change badges as a re-list. That is
    pinned as an open question in `test_f4s3_shape_pickups.py`, not here.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.pipeline.shape import shape_raw_pull
from rentcomp.storage.config import Config

GOLDEN_DIR = (
    Path(__file__).resolve().parents[3] / "fixtures" / "synthetic" / "golden" / "f4s3-stitcher"
)

#: Frozen inputs. `as_of` is the committed pull's own pull date, so the two
#: real records behave here exactly as they do behind `pull_ref="ws1-real"`.
AS_OF = date(2026, 7, 27)
FULL_YEAR = ("01-01", "12-31")

#: The fields whose values this story defines. Compared field-by-field so a
#: failure names the number that moved instead of printing two 22-record
#: blobs. Identity fields are compared too (via `_KEY`), just not diffed.
_SEMANTIC_FIELDS = (
    "initial_ask",
    "effective_dom",
    "censored",
    "removal_class",
    "cohort_year",
    "cut_history",
    "relist_count",
    "gap_days",
    "withdrawal_suspect",
    "sqft",
    "beds",
    "baths",
    "lat",
    "lng",
)


def _load(name: str):
    return json.loads((GOLDEN_DIR / name).read_text())


def _key(comp: dict) -> tuple[str, str | None, str | None]:
    """A comp's identity for pairing expected against actual: the group it
    belongs to plus the chain it started. Two disjoint chains of one unit are
    two comps (WS-1a), so `first_listed` is part of the identity."""
    return (comp["address"], comp["unit"], comp["first_listed"])


@pytest.fixture(scope="module")
def expected() -> list[dict]:
    return _load("expected.json")


@pytest.fixture(scope="module")
def actual() -> list[dict]:
    comps = shape_raw_pull(
        _load("raw_active.json"), _load("raw_inactive.json"), Config(), AS_OF, *FULL_YEAR
    )
    return [comp.model_dump(mode="json") for comp in comps]


# ---------------------------------------------------------------------------
# the fixture itself — oracle-drift guards, so a golden failure is never
# ambiguous between "the code changed" and "the fixture changed"
# ---------------------------------------------------------------------------


def test_the_golden_fixture_is_present_and_is_the_size_it_claims_to_be() -> None:
    active, inactive, exp = _load("raw_active.json"), _load("raw_inactive.json"), _load("expected.json")
    assert len(active) == 4
    assert len(inactive) == 17
    assert len(exp) == 22
    assert len({_key(c) for c in exp}) == 22, "expected.json has two records with the same identity"


def test_the_two_real_records_are_still_verbatim_copies_of_the_committed_pull() -> None:
    """T-S1's real half (PM dispatch): `2453 W 46th Pl`'s churn record and a
    price-change chain are in the fixture *because they are real*. If someone
    edits them to make a test pass they stop being evidence, so their raw
    bytes are checked against `fixtures/live-samples/` on every run."""
    live = Path(__file__).resolve().parents[3] / "fixtures" / "live-samples"
    real = json.loads((live / "fe9de5158f036802.json").read_text()) + json.loads(
        (live / "6327600317b11d16.json").read_text()
    )
    by_id = {record["id"]: record for record in real}

    golden = _load("raw_active.json") + _load("raw_inactive.json")
    copied = [record for record in golden if record["id"] in by_id]
    assert len(copied) == 3, (
        f"expected 3 real records in the golden fixture (2 listing ids at 2453 W 46th Pl + "
        f"1 at 2411 W 34th Pl), found {len(copied)}"
    )
    for record in copied:
        assert record == by_id[record["id"]], (
            f"golden fixture record {record['id']!r} no longer matches the committed pull — "
            "a real record was edited, which makes it synthetic"
        )


@pytest.mark.parametrize(
    ("case", "address", "why"),
    [
        ("S01 laundered DOM", "100 N Golden Ave", "a re-list resets listedDate"),
        ("S02 triple re-list", "101 N Golden Ave", "three spells in one chain"),
        ("S03 price-up re-list", "102 N Golden Ave", "a raise is not a cut"),
        ("S04 fell-through lease", "103 N Golden Ave", "re-listed at week 5"),
        ("S05 gap == threshold", "104 N Golden Ave", "42d does not merge"),
        ("S06 gap == threshold-1", "105 N Golden Ave", "41d merges"),
        ("S07 overlapping spells", "106 N Golden Ave", "negative gap"),
        ("S08 no history object", "107 N Golden Ave", "top-level fallback"),
        ("S09 simple censored", "108 N Golden Ave", "one open spell"),
        ("S10 300-day gap", "109 N Golden Ave", "two comps, no suspicion"),
        ("S11 pending removal", "110 N Golden Ave", "removed 3d before the pull"),
        ("S12 missing sqft", "111 N Golden Ave", "sqft=None survives"),
        ("S13 contiguous chain", "112 N Golden Ave", "gap exactly 0"),
        ("S14 censored with a gap", "113 N Golden Ave", "open chain, 31d gap"),
        ("S15 merge and split", "114 N Golden Ave", "one group, two chains"),
        ("S16 two cohorts", "115 N Golden Ave", "2024 and 2026"),
        ("R01 real churn", "2453 W 46th Pl", "5 history events, 2 listing ids"),
        ("R02 real price chain", "2411 W 34th Pl", "3 contiguous segments, active"),
    ],
)
def test_every_pathological_case_the_fixture_claims_to_cover_is_in_it(
    expected, case, address, why
) -> None:
    """The coverage the docstring advertises, asserted rather than asserted-in-
    prose. A golden file that quietly loses a case still passes; this is what
    stops that."""
    assert any(comp["address"] == address for comp in expected), (
        f"{case} ({why}) is missing from expected.json — the golden fixture no longer covers it"
    )


# ---------------------------------------------------------------------------
# the golden comparison itself
# ---------------------------------------------------------------------------


def test_the_golden_pull_shapes_into_exactly_the_expected_comps(expected, actual) -> None:
    """Nothing appears, nothing disappears.

    Split out from the field comparison below on purpose: a merge-rule change
    that *splits* chains shows up here as extra comps, and that is a different
    diagnosis from a wrong number on the right comp. The naive repair for the
    negative-gap defect (`0 <= gap < threshold`) fails exactly this assertion
    and not the next one."""
    missing = sorted(set(map(_key, expected)) - set(map(_key, actual)))
    extra = sorted(set(map(_key, actual)) - set(map(_key, expected)))
    assert not missing and not extra, (
        f"the golden pull shaped into {len(actual)} comps, expected {len(expected)}\n"
        f"  missing (expected, not produced): {missing}\n"
        f"  extra   (produced, not expected): {extra}"
    )


def test_the_golden_pull_shapes_into_exactly_the_expected_values(expected, actual) -> None:
    """T-S1: the snapshot. Every field this story defines, on every comp.

    Fails today on two records — both `effective_dom`, both the chain-boundary
    defect (`chain[-1].removed` is the last spell *by listed date*, not the
    latest removal the chain observed). See this module's docstring."""
    by_actual = {_key(comp): comp for comp in actual}
    diffs: list[str] = []
    for want in expected:
        got = by_actual.get(_key(want))
        if got is None:
            continue  # already reported by the test above
        for field in _SEMANTIC_FIELDS:
            if want[field] != got[field]:
                diffs.append(f"  {_key(want)} .{field}: expected {want[field]!r}, got {got[field]!r}")
    assert not diffs, (
        "the stitcher's output has drifted from the golden file. If this is a deliberate "
        "change, re-author expected.json from the story's definitions and record why in the "
        "story that changed it — never regenerate it from the code's current output "
        "(T-S1).\n" + "\n".join(diffs)
    )


def test_the_golden_pull_shapes_the_same_way_twice(actual) -> None:
    """Shaping is a pure function of its arguments — the precondition that
    makes a golden file meaningful at all. If this fails, every other
    assertion in this file is measuring luck."""
    again = shape_raw_pull(
        _load("raw_active.json"), _load("raw_inactive.json"), Config(), AS_OF, *FULL_YEAR
    )
    assert [comp.model_dump(mode="json") for comp in again] == actual


def test_the_golden_pull_does_not_depend_on_which_response_a_record_arrived_in(actual) -> None:
    """The order-independence invariant `_dedupe_by_id` claims, over a pull
    built to stress it: S01 and S07 each put two listing ids for one unit on
    opposite sides of the Active/Inactive split.

    Compared as a multiset, not a set — a set comparison is satisfied by a
    result that gained or lost a *duplicate*, which is precisely the failure
    F4-S2 was about."""
    from collections import Counter

    swapped = shape_raw_pull(
        _load("raw_inactive.json"), _load("raw_active.json"), Config(), AS_OF, *FULL_YEAR
    )
    swapped_rows = Counter(json.dumps(c.model_dump(mode="json"), sort_keys=True) for c in swapped)
    actual_rows = Counter(json.dumps(c, sort_keys=True) for c in actual)
    assert swapped_rows == actual_rows, (
        "shaping the golden pull depends on which response a record arrived in; "
        f"symmetric difference: {sorted((swapped_rows - actual_rows) | (actual_rows - swapped_rows))}"
    )
