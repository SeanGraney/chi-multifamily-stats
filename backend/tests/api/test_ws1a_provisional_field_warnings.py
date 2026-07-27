"""WS-1a — "fold in while this story is open" (ADR-002 finding 5)
[DEFAULT convention, [INVARIANT] outcome]. QA-authored, written RED before
any implementation exists (AGENT_QA.md protocol).

Three placeholder field values survived WS-1's global retirement of the
F0-S2 `stub_stage` warning inventory with NO marker at all, unlike the four
stages that story replaced (`test_no_placeholder_stage_survives_ws1` in
`test_derive_dev.py`):

  * `shape.py`: `sqft_suspect=False` always (F5-S1's >30% deviation flag
    isn't built yet)
  * `derive.py`: `premium_basis="selected"` always (F4-S5's pulled-set
    fallback isn't built yet)
  * `derive.py`: `partial_pull=None` always (F4-S6/D24 isn't built yet)

None of these are WRONG values today — they're honest defaults for what
WS-1 actually computed — but nothing tells a reader they're provisional.
The dispatch's own convention: each should emit a `provisional_field`-coded
`DerivedWarning` naming itself (reusing the existing `DerivedWarning`
model — no schema change), the same self-documenting pattern
`_STUB_WARNINGS` used, so a later story (F5-S1, F4-S5, F4-S6) can delete its
own warning as it lands for real.

WHAT THIS FILE PINS, DELIBERATELY LOOSELY
------------------------------------------
The OUTCOME (three distinct provisional-field warnings exist, each
identifiable by which field it names) is pinned. The exact wording of each
warning's `message` is NOT pinned — only that each names its own field
(`sqft_suspect` / `premium_basis` / `partial_pull`) somewhere in its text, so
a developer's phrasing is free to vary.

EXPECTED RED STATE TODAY: every test below FAILS — no `provisional_field`
warnings exist at all yet.
"""

from __future__ import annotations

PROVISIONAL_FIELD_CODE = "provisional_field"

#: The three fields named in the dispatch, and a substring each warning's
#: message must contain to prove it names *itself* and not something else.
EXPECTED_PROVISIONAL_FIELDS = {"sqft_suspect", "premium_basis", "partial_pull"}


def _provisional_field_warnings(derived: dict) -> list[dict]:
    return [w for w in derived["warnings"] if w["code"] == PROVISIONAL_FIELD_CODE]


def test_three_provisional_field_warnings_are_present(derived) -> None:
    """One per placeholder field (see module docstring) — not fewer (a field
    silently undocumented), not merged into one warning (a reader could not
    tell which field is still provisional from a combined message without
    reading every message's full text, and each field clears independently
    as F5-S1/F4-S5/F4-S6 land, so they cannot share a lifecycle)."""
    warnings = _provisional_field_warnings(derived)
    assert len(warnings) == 3, (
        f"expected exactly 3 '{PROVISIONAL_FIELD_CODE}' warnings (one per placeholder field "
        f"named in the WS-1a dispatch), got {len(warnings)}: {warnings}"
    )


def test_each_provisional_field_warning_names_its_own_field(derived) -> None:
    warnings = _provisional_field_warnings(derived)
    named_fields = {
        field
        for warning in warnings
        for field in EXPECTED_PROVISIONAL_FIELDS
        if field in warning["message"]
    }
    assert named_fields == EXPECTED_PROVISIONAL_FIELDS, (
        f"expected a '{PROVISIONAL_FIELD_CODE}' warning naming each of "
        f"{sorted(EXPECTED_PROVISIONAL_FIELDS)}; found warnings naming {sorted(named_fields)} — "
        f"warnings were: {warnings}"
    )


def test_provisional_field_warnings_survive_over_the_real_ws1_pull(derive) -> None:
    """Not a synthetic-fixture-only artifact — the real ws1-real pull hits
    every one of these three placeholders too (every comp's `sqft_suspect`
    is still hardcoded False, every premium's basis is still "selected", and
    `partial_pull` is still always None, regardless of which pull is
    derived)."""
    response = derive(pull_ref="ws1-real", candidate_rent=None)
    assert response.status_code == 200, response.text[:600]
    warnings = [w for w in response.json()["warnings"] if w["code"] == PROVISIONAL_FIELD_CODE]
    assert len(warnings) == 3, (
        f"expected 3 '{PROVISIONAL_FIELD_CODE}' warnings over the real ws1-real pull too, "
        f"got {len(warnings)}: {warnings}"
    )


def test_a_provisional_field_warning_is_not_mistaken_for_the_retired_stub_stage_code(
    derived,
) -> None:
    """The two warning families must stay distinguishable: `stub_stage` named
    an entire STAGE producing no real number at all (retired by WS-1);
    `provisional_field` names one honest-but-not-yet-final FIELD on an
    otherwise real computation. Conflating them would make a future
    'zero stub_stage warnings' check (`test_no_placeholder_stage_survives_
    ws1`) accidentally pass for the wrong reason if a developer reused the
    old code by mistake."""
    codes = {w["code"] for w in derived["warnings"]}
    assert "stub_stage" not in codes, (
        "a 'stub_stage' warning reappeared — WS-1 already retired this family; "
        "provisional-field warnings must use their own code"
    )
