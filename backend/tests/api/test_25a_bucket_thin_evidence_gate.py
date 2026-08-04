"""Row 25a — the bucket panel renders per-statistic evidence with no gate.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). No code implements this story yet, so every test below is
expected to fail loudly (missing dict keys) rather than to error opaquely —
see the first test in each class, which names the missing field explicitly.

======================================================================
THE STORY, IN ONE SENTENCE (QUEUE.md row 25a + its two PM rulings)
======================================================================
F5-S3 (row 25, DONE) gates the ANALYSIS button on `anchor.n_comps` — a
question about whether there is enough evidence for an anchor and a price
test AT ALL. The bucket table (`<BucketTable>` in `Results.tsx`) renders
unconditionally in Results, with no gate of its own, so a bucket cell's
`leased_dom_median`/`min`/`max`/`cut_before_lease_rate` can be computed from
as few as ONE leased observation, with nothing on screen disclosing that.

PM Ruling 1 (2026-08-03): 25a is disjoint from row 12b (a `cohorts.py`
evidence-citation defect) — this file never touches `cohorts.py`.

PM Ruling 2 (2026-08-03), the load-bearing one: **the gate is PER-STATISTIC,
on the observation count actually behind the specific cell being rendered —
NOT a reused F5-S3-style threshold on total included comps.** A search can
clear F5-S3's >=5-included gate overall while one premium bucket still holds
only 1-2 leased outcomes; a reused total-included threshold would not catch
that. `TestPerCellGateCountsTheRightThing` below is the direct test of this
ruling: it constructs a bucket where total membership (`count`) AND the
search's total included count both stay well above any plausible
total-based threshold, while the bucket's *leased* count is 1 — and asserts
the gate still trips.

======================================================================
WIRE CONTRACT PROPOSED HERE (QA-proposed, needs PM/dev confirmation —
the `TestProposedContract` convention this repo already uses, e.g.
`test_derive_contract.py`)
======================================================================
`BucketStat` gains two fields, modeled directly on `CohortStat.thin`
(`models/responses.py`) — same name, same "disclose, do not fabricate a
wider set" spirit, adapted to a surface that (per NORTH_STAR's
bucket-boundary row) must never interpolate or borrow from elsewhere:

* `leased_count: int` — the size of the SAME leased set
  (`removal_class in ("provisional", "confirmed")`) that
  `leased_dom_median`/`min`/`max`/`cut_before_lease_rate` are already
  computed over in `pipeline/buckets.py::bucket_stats`. Distinct from
  `count`, which is the bucket's total membership (leased + censored +
  provisional/withdrawal-suspect flags + pending, all of which already
  exist as separate fields). One field suffices for all four leased-outcome
  statistics because `buckets.py` computes all four from the identical
  `leased` list — there is only one evidence count to gate on per bucket.
* `thin: bool` — true when `leased_count` is below the evidence threshold
  for those four statistics to be shown without a caveat. Threshold VALUE
  and SOURCE (reuse `min_cohort_size`, or a new dedicated `[DEFAULT]`
  constant) are the developer's call per Ruling 2 — logged, not silently
  chosen. This suite deliberately does NOT pin an exact boundary value
  (that would pin a `[DEFAULT]`); it pins only the outcomes that are
  actually invariant: N=1 must read as thin (it is the story's own named
  failure mode) and a well-evidenced bucket (tens of leased comps, real
  data) must not.

======================================================================
WHAT "GATED" MEANS HERE — DISCLOSURE, NOT SUPPRESSION (recorded, not
silently assumed — see QA's handoff for the full reasoning)
======================================================================
NORTH_STAR's "Guard state" row describes a MUTUALLY EXCLUSIVE render
(curve XOR guard) — that is F11-S3's shape for the price test, a different
surface with a real fallback-free alternative (an explicit
"insufficient evidence" state). Buckets have no such alternative: an empty
leased set already renders `None`/dash (existing F10-S1 behaviour,
unaffected by this story), but a NON-empty, merely THIN leased set has real
observed evidence behind it. The F10-S1 PM ruling on this exact panel
(QUEUE.md row 26) already decided the adjacent question the same way:
withholding real DOM evidence because a DIFFERENT prerequisite (the anchor)
was not ready would be "the identical failure mode row 25a exists to name,
just the inverse direction" — confident output withheld from evidence that
is actually there. This suite follows that precedent: `thin=True` must
disclose, not suppress. `TestDisclosureNotSuppression` pins this directly.
"""

from __future__ import annotations

REAL_PULL_REF = "ws1-real"
SYNTHETIC_PULL_REF = "synthetic-basic"

_LEASED_CLASSES = ("provisional", "confirmed")


def _leased_count_from_comps(comp_keys: list[str], comps_by_key: dict) -> int:
    """Ground truth, computed the same way `buckets.py` does — from the wire,
    never from the pipeline internals — so this suite keeps working
    unmodified once the real implementation lands."""
    return sum(1 for key in comp_keys if comps_by_key[key]["removal_class"] in _LEASED_CLASSES)


class TestWireContractLeasedCount:
    """`BucketStat.leased_count` exists, and is distinct from `count`, on
    fixtures that need zero curation to prove the point."""

    def test_a_bucket_stat_names_the_field_at_all(self, derived) -> None:
        """The single loud failure this file leads with (AGENT_QA.md commit
        discipline: one named failure, the rest legible against it)."""
        bucket = derived["buckets"][0]
        assert "leased_count" in bucket, (
            "BucketStat has no `leased_count` field yet — row 25a's per-cell gate has "
            f"nothing to count. Bucket payload keys: {sorted(bucket.keys())}"
        )
        assert "thin" in bucket, (
            "BucketStat has no `thin` field yet — row 25a's disclosure has nothing to key "
            f"off. Bucket payload keys: {sorted(bucket.keys())}"
        )

    def test_leased_count_differs_from_count_on_the_default_synthetic_fixture(self, derive) -> None:
        """No exclusion needed: `synthetic-basic`'s default `above` bucket already
        holds 3 members (one censored, one provisional, one confirmed) — 2 of
        which are leased. If this fixture ever changes shape, this test names
        the gap rather than passing vacuously."""
        data = derive(pull_ref=SYNTHETIC_PULL_REF).json()
        above = next(b for b in data["buckets"] if b["id"] == "above")
        assert above["count"] == 3, (
            "synthetic-basic's `above` bucket no longer has 3 members — this test's "
            f"construction needs updating. Got: {above}"
        )
        assert above["leased_count"] == 2, (
            f"expected leased_count=2 (1 provisional + 1 confirmed, 1 censored excluded), "
            f"got {above.get('leased_count')!r}"
        )
        assert above["count"] != above["leased_count"], (
            "this is the exact shape row 25a exists to name: a bucket's total membership "
            "and its LEASED evidence count can differ, and a gate keyed on `count` (or on "
            "the search's total included count) would miss the difference"
        )

    def test_leased_count_matches_the_leased_members_named_in_comp_keys(self, derive) -> None:
        """Exhaustive, not representative (AGENT_QA.md split rule): every
        non-empty bucket, on two independently-shaped pulls, cross-checked
        against the comps array's own `removal_class` — never against a
        pipeline internal."""
        checked_any = False
        for pull_ref in (SYNTHETIC_PULL_REF, REAL_PULL_REF):
            data = derive(pull_ref=pull_ref).json()
            comps_by_key = {c["key"]: c for c in data["comps"]}
            for bucket in data["buckets"]:
                if bucket["count"] == 0:
                    continue
                checked_any = True
                expected = _leased_count_from_comps(bucket["comp_keys"], comps_by_key)
                assert bucket["leased_count"] == expected, (
                    f"[{pull_ref}] bucket {bucket['id']!r} reports leased_count="
                    f"{bucket.get('leased_count')!r} but {expected} of its {bucket['count']} "
                    "members carry a leased removal_class (provisional/confirmed)"
                )
        assert checked_any, "no non-empty bucket found on either fixture — construction is stale"


class TestPerCellGateCountsTheRightThing:
    """The direct test of PM Ruling 2: the gate must count the real N behind
    the specific cell being rendered, not the bucket's total membership and
    not the search's total included count."""

    def test_one_leased_comp_in_a_bucket_is_flagged_thin(self, derive) -> None:
        """The story's own named failure mode, constructed on real data via
        comp exclusion (the row 12b technique, named in QA's dispatch) —
        weight every comp to 0 except a hand-picked set that leaves exactly
        one leased comp, plus a few censored ones, in the `above` bucket.

        QA verified this construction empirically before writing the
        assertion (see QA's handoff for the probe transcript): the exact set
        below currently reproduces `above: count=4, leased=1,
        breakdown.included=6` — total bucket membership (4) and total
        included (6) BOTH comfortably clear F5-S3's own >=5-included shape,
        while the cell's actual leased evidence is exactly 1.
        """
        default = derive(pull_ref=REAL_PULL_REF).json()
        comps_by_key = {c["key"]: c for c in default["comps"]}

        keep = [
            "1317 w 31st pl|1fl",  # leased (confirmed) — the one real observation
            "1922 w 47th st|2",  # censored
            "2241 w 21st st|",  # censored
            "2241 w 21st st|2",  # censored
            "2345 w 21st st|2",  # censored
            "2624 w 24th st|1f|2026-06-12",  # censored
        ]
        missing = [key for key in keep if key not in comps_by_key]
        assert not missing, (
            f"ws1-real no longer contains {missing} — this construction is stale and needs "
            "rebuilding against the current fixture (re-probe per QA's handoff)"
        )

        weights = {key: 0 for key in comps_by_key if key not in keep}
        thin_derive = derive(pull_ref=REAL_PULL_REF, weights=weights).json()

        assert thin_derive["breakdown"]["included"] >= 5, (
            "construction drifted: total included must stay >= F5-S3's own gate shape, or "
            "this stops proving the per-cell point"
        )

        above = next(b for b in thin_derive["buckets"] if b["id"] == "above")
        assert above["leased_count"] == 1, (
            f"construction drifted: expected exactly 1 leased comp in the `above` bucket, "
            f"got {above.get('leased_count')!r} — {above}"
        )
        assert above["count"] > above["leased_count"], (
            "construction must keep total bucket membership strictly above the leased "
            "count, or this does not distinguish a per-cell gate from a total-membership one"
        )
        assert above["thin"] is True, (
            f"a bucket with exactly ONE leased comp behind its DOM median must be flagged "
            f"thin, regardless of its total membership ({above['count']}) or the search's "
            f"total included count ({thin_derive['breakdown']['included']}) — both clear "
            "F5-S3's >=5-included shape here, which is exactly why a reused total-included "
            "threshold would miss this cell (PM Ruling 2)"
        )

    def test_a_well_evidenced_bucket_on_real_data_is_not_flagged_thin(self, derive) -> None:
        """The other side of the boundary, deliberately far from any plausible
        threshold value so this test never pins the exact `[DEFAULT]` the
        developer picks. On the unmodified real pull every bucket clears
        dozens of leased comps."""
        data = derive(pull_ref=REAL_PULL_REF).json()
        checked_any = False
        for bucket in data["buckets"]:
            if bucket["leased_count"] < 20:
                continue
            checked_any = True
            assert bucket["thin"] is False, (
                f"bucket {bucket['id']!r} has {bucket['leased_count']} leased comps behind "
                "its DOM statistics and must not be flagged thin"
            )
        assert checked_any, "ws1-real no longer has a well-evidenced bucket — construction stale"


class TestDisclosureNotSuppression:
    """`thin=True` must disclose, never suppress — see the module docstring's
    reasoning (F10-S1's row-26 precedent on this exact panel)."""

    def test_a_thin_buckets_dom_statistics_still_render_and_are_correct(self, derive) -> None:
        default = derive(pull_ref=REAL_PULL_REF).json()
        comps_by_key = {c["key"]: c for c in default["comps"]}

        keep = [
            "1317 w 31st pl|1fl",
            "1922 w 47th st|2",
            "2241 w 21st st|",
            "2241 w 21st st|2",
            "2345 w 21st st|2",
            "2624 w 24th st|1f|2026-06-12",
        ]
        weights = {key: 0 for key in comps_by_key if key not in keep}
        thin_derive = derive(pull_ref=REAL_PULL_REF, weights=weights).json()
        above = next(b for b in thin_derive["buckets"] if b["id"] == "above")

        assert above["thin"] is True, "construction must land on the thin branch to be a real test"

        the_one_leased_comp = comps_by_key["1317 w 31st pl|1fl"]
        expected_dom = the_one_leased_comp["effective_dom"]
        assert above["leased_dom_median"] == expected_dom, (
            f"a thin bucket must still report its real DOM median ({expected_dom}), not "
            f"suppress it — got {above['leased_dom_median']!r}. A flag is a caveat, not a "
            "guard-state replacement (NORTH_STAR's guard-state row is a different surface, "
            "F11-S3's price test, with a real fallback-free alternative; a bucket's non-empty "
            "leased set has no such alternative and F10-S1's own row-26 ruling on this panel "
            "already decided real evidence must not be withheld for a different prerequisite)"
        )
        assert above["leased_dom_min"] == expected_dom
        assert above["leased_dom_max"] == expected_dom
