"""F2-S2 — Bed/bath range parser. QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

STORY, VERBATIM
----------------
"[BE] Bed/bath range parser. [INVARIANT] — correctness against RentCast's
actual query syntax, not an implementation choice. Accept "2" or "1-3" per
field; emit RentCast query syntax."
AC: "parser rejects malformed input at the form layer; single/range both
round-trip through a query-string builder unit test."

THE OPEN ITEM IS ALREADY ANSWERED (do not re-derive)
------------------------------------------------------
T-S3 verified live, and it is encoded in ``rentcomp.client.query`` today:
  * numeric ranges are ONE param, colon-separated (``rentcast_range``)
  * multi-values are ONE param, pipe-separated (``rentcast_multi``)
``query.py``'s own docstring names this story directly: "Shared syntax:
bedrooms/bathrooms ranges use this identically (F2-S2 inherits it)" and
"Parsing user input *into* these strings is F2-S2's; this module only emits."

So this story's entire job is: take the user-facing hyphen form ("2" /
"1-3") and turn it into what ``rentcast_range`` / ``build_listing_params``
already know how to speak — NOT re-verify or reinvent the wire syntax.

WHY LAYER 1 ONLY
----------------
AGENT_QA.md's decision procedure, step 1: "import a function and call it
with plain values" — a string in, a string out, no request body, no
assembled state, no app. Both AC clauses are provable this way:
  * "rejects malformed input"                -> pytest.raises(ValueError)
  * "single/range round-trip through the
     query-string builder"                    -> call build_listing_params
                                                  with the parser's output
There is no L2 or L3 coverage in this file, and none is owed: no `/api/*`
route consumes this parser yet (no search-submission endpoint exists), and
no browser surface exists at all (F2-S1, the actual form, is still
BLOCKED — see the ambiguity note below). A test at either higher layer here
would be exercising nothing but this same pure function through extra
machinery — the "wrong layer" smell AGENT_QA.md names explicitly.

CONTRACT THESE TESTS PIN (QA-PROPOSED — story names no module or function,
so this is QA's reading; PM/dev may counter-propose, QA then edits)
----------------------------------------------------------------------------
``rentcomp.client.beds_baths``

  ``parse_bed_bath_field(raw: str | None, *, field: str) -> str | None``

    field is ``"bedrooms"`` or ``"bathrooms"`` — the two differ only in
    whether fractional values are legal (bathrooms: yes, spec §2.1 —
    "Fractional allowed (1.5)"; bedrooms: no, whole units only, "0" is the
    documented studio spelling).

    * A bare value ("2") passes through UNCHANGED — bedrooms/bathrooms bare
      means EXACT match on this endpoint (schema: "the number of bedrooms
      used to search for listings matching this criteria"), which is the
      OPPOSITE of ``daysOld``'s bare-means-maximum rule already encoded in
      ``build_listing_params``. This function must not apply that rule here
      — that would be importing a neighboring endpoint quirk into a field
      it doesn't apply to.
    * A "min-max" value becomes ``rentcast_range(min, max)`` — i.e. the
      SAME colon syntax daysOld uses, because that part of the syntax
      (D-INVARIANT, T-S3) *is* shared across every RentCast range param.
      A degenerate range ("2-2") still becomes "2:2", not bare "2" — same
      rule ``rentcast_range(5, 5) == "5:5"`` already enforces for daysOld,
      so a user who explicitly typed a range gets range syntax back even
      when both ends match.
    * ``None`` means "field left blank" and returns ``None`` — the signal
      ``build_listing_params`` already needs to omit the param entirely
      (F0-S4: "an omitted bathrooms means NO bathrooms key at all — not
      '' and not '*:*', because an empty filter makes the server exclude
      records with missing bathroom data"). An empty/whitespace-only STRING
      is treated as malformed, not as omission — see the ambiguity note.
    * Anything else that cannot be read as one of the two forms above
      raises ``ValueError`` naming the field and the bad input.

AMBIGUITIES FLAGGED FOR THE PM (not resolved here — see the handoff)
----------------------------------------------------------------------------
1. **"rejects malformed input at the form layer"** — no form exists yet
   (F2-S1 is BLOCKED). This file tests the parser as a pure function; it
   does not invent a browser surface. Same gap class as the `/api/config`
   and Settings-API-key-UI reassignments already logged in QUEUE.md.
2. **min > max ("3-1")** — the epic's F2 edges say the FORM does
   "inline swap-or-error" on min>max. This story is not the form. QA
   proposes the pure function REJECTS min>max outright (a range parser
   silently reordering its own input is a worse trap than refusing it) and
   leaves any "swap" UX entirely to F2-S1. Flagged for PM confirmation.
3. **Empty/whitespace string vs `None`** — QA proposes only `None` reads as
   "omitted" (mirrors F0-S4's optional-bathrooms contract exactly); an
   empty string is refused as malformed on the theory that a real form
   sends `None`/omits the key for a blank field, never `""`, so a `""`
   reaching this function is more likely a caller bug than a blank field.
   Flagged as QA-proposed, not a story-stated requirement.
4. **Fractional bedrooms ("1.5" for the bedrooms field)** — spec §2.1 states
   fractions are a BATHROOMS-only allowance; QA proposes bedrooms rejects
   them. This is a step beyond pure wire-syntax correctness (RentCast's
   schema does not itself forbid a fractional bedrooms value) so it is
   marked PROPOSED and isolated in its own test class, not asserted as a
   bare invariant.

EXPECTED RED STATE TODAY: one import-gate test fails naming exactly what is
missing (`rentcomp.client.beds_baths`); everything else SKIPS behind it.
"""

from __future__ import annotations

import pytest

try:
    from rentcomp.client.beds_baths import parse_bed_bath_field

    _PARSER_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F2-S2 lands
    _PARSER_ERROR = _exc

from rentcomp.client.query import build_listing_params, rentcast_range

needs_parser = pytest.mark.skipif(
    _PARSER_ERROR is not None,
    reason=f"rentcomp.client.beds_baths missing (F2-S2 red): {_PARSER_ERROR}",
)


# ---------------------------------------------------------------------------
# existence — legible red
# ---------------------------------------------------------------------------


def test_parser_module_imports_its_contract() -> None:
    assert _PARSER_ERROR is None, (
        "Cannot import parse_bed_bath_field from rentcomp.client.beds_baths "
        f"(QA-proposed, see this module's docstring): {_PARSER_ERROR}"
    )


# ---------------------------------------------------------------------------
# single values — bare passthrough, NOT the daysOld bare-means-max rule
# ---------------------------------------------------------------------------

SINGLE_BEDROOM_CASES = [
    ("2", "2"),
    ("0", "0"),  # studio, schema-documented spelling
    ("4", "4"),
    (" 2 ", "2"),  # whitespace around a bare value is trimmed
]

SINGLE_BATHROOM_CASES = [
    ("2", "2"),
    ("1.5", "1.5"),  # fractional, schema-documented allowance
    ("2.5", "2.5"),
    (" 1.5 ", "1.5"),
]


@needs_parser
@pytest.mark.parametrize(("raw", "expected"), SINGLE_BEDROOM_CASES)
def test_a_bare_bedroom_value_passes_through_unchanged(raw: str, expected: str) -> None:
    """Bedrooms/bathrooms bare means EXACT match on this endpoint — the
    opposite of daysOld's "bare is the range maximum" rule. Emitting "2:2"
    or otherwise mutating a single value the user did not range would be
    importing a neighboring field's quirk into one it doesn't apply to."""
    assert parse_bed_bath_field(raw, field="bedrooms") == expected


@needs_parser
@pytest.mark.parametrize(("raw", "expected"), SINGLE_BATHROOM_CASES)
def test_a_bare_bathroom_value_passes_through_unchanged(raw: str, expected: str) -> None:
    assert parse_bed_bath_field(raw, field="bathrooms") == expected


# ---------------------------------------------------------------------------
# ranges — the shared T-S3 colon syntax
# ---------------------------------------------------------------------------

RANGE_BEDROOM_CASES = [
    ("1-3", "1:3"),
    ("0-2", "0:2"),
    (" 1 - 3 ", "1:3"),  # whitespace around the hyphen is tolerated
    ("2-2", "2:2"),  # degenerate range still emits colon syntax
]

RANGE_BATHROOM_CASES = [
    ("1.5-2.5", "1.5:2.5"),
    ("1-2.5", "1:2.5"),
    ("2.5-2.5", "2.5:2.5"),
]


@needs_parser
@pytest.mark.parametrize(("raw", "expected"), RANGE_BEDROOM_CASES)
def test_a_bedroom_range_becomes_colon_syntax(raw: str, expected: str) -> None:
    assert parse_bed_bath_field(raw, field="bedrooms") == expected


@needs_parser
@pytest.mark.parametrize(("raw", "expected"), RANGE_BATHROOM_CASES)
def test_a_bathroom_range_becomes_colon_syntax(raw: str, expected: str) -> None:
    assert parse_bed_bath_field(raw, field="bathrooms") == expected


@needs_parser
@pytest.mark.parametrize(("raw", "field"), [("1-3", "bedrooms"), ("1.5-2.5", "bathrooms")])
def test_a_range_cannot_drift_from_rentcast_range(raw: str, field: str) -> None:
    """ONE answer, one source. The parser must not carry its own copy of the
    colon-syntax rule — it must literally emit what ``rentcast_range``
    produces, or the two will silently diverge the next time that function
    changes."""
    lo_str, hi_str = raw.split("-")
    lo = float(lo_str) if "." in lo_str else int(lo_str)
    hi = float(hi_str) if "." in hi_str else int(hi_str)
    assert parse_bed_bath_field(raw, field=field) == rentcast_range(lo, hi)


# ---------------------------------------------------------------------------
# omission — None means "no filter", per F0-S4's bathrooms contract
# ---------------------------------------------------------------------------


@needs_parser
@pytest.mark.parametrize("field", ["bedrooms", "bathrooms"])
def test_none_means_the_field_was_left_blank(field: str) -> None:
    """QA-PROPOSED (ambiguity #3 in this file's docstring): None round-trips
    to None, the signal build_listing_params already needs to omit a
    filter entirely rather than send "" or "*:*"."""
    assert parse_bed_bath_field(None, field=field) is None


@needs_parser
@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_an_empty_string_is_malformed_not_omitted(blank: str) -> None:
    """QA-PROPOSED (ambiguity #3): only ``None`` is "blank"; an empty STRING
    reaching this function is treated as a caller bug, not a blank field —
    a real form sends None (or omits the key) for an untouched field."""
    with pytest.raises(ValueError):
        parse_bed_bath_field(blank, field="bedrooms")
    with pytest.raises(ValueError):
        parse_bed_bath_field(blank, field="bathrooms")


# ---------------------------------------------------------------------------
# malformed input — the AC's "rejects malformed input", pure-function form
# ---------------------------------------------------------------------------

MALFORMED_FOR_BOTH_FIELDS = [
    "abc",
    "two",
    "1-abc",
    "abc-3",
    "1x3",
    "-1",  # negative — no bedroom/bathroom count is negative
    "1--3",
    "1-3-5",  # more than one hyphen is ambiguous, not a 3-part range
    "1-",  # trailing hyphen, no upper bound given
    "-3",  # bare negative, not a valid range or count
    "1–3",  # en dash, not the hyphen the spec documents ("1-3")
    "1.5.5",
    "inf-5",
    "NaN",
]


@needs_parser
@pytest.mark.parametrize("raw", MALFORMED_FOR_BOTH_FIELDS)
def test_malformed_bedroom_input_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_bed_bath_field(raw, field="bedrooms")


@needs_parser
@pytest.mark.parametrize("raw", MALFORMED_FOR_BOTH_FIELDS)
def test_malformed_bathroom_input_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_bed_bath_field(raw, field="bathrooms")


@needs_parser
@pytest.mark.parametrize("raw", ["3-1", "2.5-1", "10-2"])
def test_min_greater_than_max_is_rejected_not_swapped(raw: str) -> None:
    """QA-PROPOSED (ambiguity #2 in this file's docstring): the epic's
    "inline swap-or-error" edge belongs to the F2-S1 FORM, not this pure
    parser. A range parser that silently reorders its own input is a worse
    trap than one that refuses it — flagged for PM confirmation."""
    with pytest.raises(ValueError):
        parse_bed_bath_field(raw, field="bedrooms")
    with pytest.raises(ValueError):
        parse_bed_bath_field(raw, field="bathrooms")


@needs_parser
def test_the_rejection_names_the_field_and_the_bad_input() -> None:
    """A malformed-input error that doesn't say what was wrong or which
    field is malformed too — the AC is "rejects", but a silent or generic
    rejection is barely more useful to a form than accepting garbage."""
    with pytest.raises(ValueError) as excinfo:
        parse_bed_bath_field("abc", field="bedrooms")
    message = str(excinfo.value)
    assert "bedrooms" in message
    assert "abc" in message


@needs_parser
def test_an_unknown_field_name_is_a_caller_bug() -> None:
    """Defensive, not core to the AC: field is one of exactly two strings.
    A typo'd field name (e.g. "bedroom" singular) should fail loudly rather
    than silently apply the wrong fraction rule."""
    with pytest.raises(ValueError):
        parse_bed_bath_field("2", field="beds")


# ---------------------------------------------------------------------------
# QA-proposed: bedrooms rejects fractions (ambiguity #4), isolated
# ---------------------------------------------------------------------------


class TestProposedBedroomWholeNumberRule:
    """QA-proposed behaviour the story text does NOT state (ambiguity #4).
    The PM confirms or overrides before this counts as locked; isolated in
    its own class so a PM override is a one-class deletion, not a rewrite
    of the file."""

    @needs_parser
    @pytest.mark.parametrize("raw", ["1.5", "2.5-3.5", "0.5"])
    def test_fractional_bedrooms_are_rejected(self, raw: str) -> None:
        with pytest.raises(ValueError):
            parse_bed_bath_field(raw, field="bedrooms")


# ---------------------------------------------------------------------------
# THE AC's second clause — round-trip through the query-string builder
# ---------------------------------------------------------------------------


@needs_parser
def test_a_single_bedroom_value_round_trips_through_the_builder() -> None:
    parsed = parse_bed_bath_field("2", field="bedrooms")
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=1.0,
        bedrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert params["bedrooms"] == "2"


@needs_parser
def test_a_bedroom_range_round_trips_through_the_builder() -> None:
    parsed = parse_bed_bath_field("1-3", field="bedrooms")
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=1.0,
        bedrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert params["bedrooms"] == "1:3" == rentcast_range(1, 3)


@needs_parser
def test_a_single_bathroom_value_round_trips_through_the_builder() -> None:
    parsed = parse_bed_bath_field("1.5", field="bathrooms")
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=1.0,
        bedrooms="2",
        bathrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert params["bathrooms"] == "1.5"


@needs_parser
def test_a_bathroom_range_round_trips_through_the_builder() -> None:
    parsed = parse_bed_bath_field("1.5-2.5", field="bathrooms")
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=1.0,
        bedrooms="2",
        bathrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert params["bathrooms"] == "1.5:2.5" == rentcast_range(1.5, 2.5)


@needs_parser
def test_an_omitted_bathroom_field_round_trips_to_no_bathrooms_key() -> None:
    """The F0-S4 invariant this parser must not break: omitted means NO key
    at all, not "" and not "*:*" — an empty bathrooms filter makes the
    server exclude records with missing bathroom data (T-S3 round 2)."""
    parsed = parse_bed_bath_field(None, field="bathrooms")
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=1.0,
        bedrooms="2",
        bathrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert "bathrooms" not in params


@needs_parser
def test_a_degenerate_range_round_trips_to_colon_syntax_not_bare() -> None:
    """Mirrors ``test_a_degenerate_range_is_still_a_range`` in
    test_rentcast_client.py, one field over: a user who explicitly typed
    "2-2" gets range syntax back, not bare "2" (which reads correctly to
    RentCast either way for bedrooms, but the parser's OWN contract is
    "range in -> range syntax out", not "simplify if it happens to be
    degenerate")."""
    parsed = parse_bed_bath_field("2-2", field="bedrooms")
    assert parsed == "2:2"
    params = build_listing_params(
        address="a",
        radius=1.0,
        bedrooms=parsed,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert params["bedrooms"] == "2:2"
