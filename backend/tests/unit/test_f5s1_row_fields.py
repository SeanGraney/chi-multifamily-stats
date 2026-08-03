"""F5-S1 — the two BACKEND halves of the comp row, at Layer 1.

QA-authored, written RED before the developer's branch exists (AGENT_QA.md
protocol step 3). Zero network (D17); nothing here reads a pull off disk.

===========================================================================
WHY A FRONTEND-TAGGED STORY HAS A LAYER-1 FILE AT ALL
===========================================================================
Two of F5-S1's fifteen acceptance criteria cannot be satisfied in the view
without breaking an architecture decision, so they are asserted here instead:

* **AC6 — `sqft_suspect`.** "$/sqft deviates >~30% from its cohort median" is
  a derivation, and D5 puts every derivation in Python. The view is handed a
  boolean and renders a badge; it never divides `initial_ask / sqft`, and it
  never learns what a cohort median is. The flag is hardcoded `False` today
  (`pipeline/shape.py`), with `pipeline/derive.py` carrying an explicit
  `provisional_field` warning saying the test is unbuilt.

* **AC9 — the cut-history line's day offset.** The row copy is
  `✂ 2,150 → 2,050 (day 21)`. `Cut` (`models/responses.py`) carries only
  `on` / `from_price` / `to_price`, so "day 21" today could only be produced
  by subtracting two dates **in the browser** — JS date arithmetic across a
  timezone boundary, which D15 keeps out of this codebase entirely, and a
  second derivation site, which D5 forbids. It ships as a number, exactly as
  `days_since_removal` did for the same reason.

===========================================================================
WHAT IS PINNED AND WHAT IS NOT
===========================================================================
* **Pinned (INVARIANT):** what the flag *means* (deviation from the comp's
  OWN cohort's median $/sqft, in either direction), that it is `False` — never
  `None`, never `True` — for a comp with no $/sqft, that it is monotone in
  deviation, and that a cut's day offset is `int | None` measured from the
  chain's first listing.
* **NOT pinned (DEFAULT):** the exact threshold. The story says "~30%", so
  the cases below sit at 10% (must not fire) and 60%/157% (must fire), well
  clear of the edge. A developer who lands on 0.28 or 0.32 passes.
* **NOT pinned:** the *name* of the wire field carrying the day offset. The
  helper below finds it structurally — "the field on `Cut` that is not `on`,
  `from_price` or `to_price`" — so `day_offset`, `day`, `days_from_listing`
  all pass.

===========================================================================
⚠ ONE THING HERE IS A PROPOSAL, NOT A CONTRACT — PM PLEASE RELAY
===========================================================================
The `sqft_suspect` tests need to call the computation directly, and it does
not exist yet, so this file proposes a signature:

    rentcomp.pipeline.premium.compute_sqft_suspects(
        psfs, cohort_years, cohort_medians
    ) -> list[bool]

— the same three arguments `compute_premiums` already takes, in the same
module, because that is the one place in the pipeline where a cohort median
and a comp's $/sqft are both in scope (AC6's note). `_suspect_fn()` below
accepts any public callable in that module whose name contains "suspect", so
naming is free. If the developer wants a different *shape* (e.g. folding it
into `compute_premiums`' return value), that is a fine call — say so through
the PM and QA amends this file. **Layer 2
(`tests/api/test_f5s1_row_fields_on_the_wire.py`) asserts the same behaviour
over HTTP and is signature-free, so nothing about the flag's meaning depends
on this proposal being accepted.**

EXPECTED RED STATE TODAY: every test in this file fails.
"""

from __future__ import annotations

from dataclasses import replace as _dc_replace  # noqa: F401  (kept for readers)
from datetime import date
from typing import Any, Callable

import pytest

from rentcomp.models.domain import PriceCut, StitchedComp
from rentcomp.models.requests import DeriveRequest, Subject
from rentcomp.pipeline.derive import DeriveContext, derive
from rentcomp.storage.config import Config

# ---------------------------------------------------------------------------
# AC6 — sqft_suspect
# ---------------------------------------------------------------------------

#: The real datum this flag exists for (AC7, measured on `ws1-real`):
#: 2350 S Leavitt St 1R — a 3bd/3ba listed as 700 sqft at $2,995.
LEAVITT_PSF = 4.2785714285714285
#: Its 2025 cohort's median $/sqft, from the same real pull.
LEAVITT_COHORT_MEDIAN = LEAVITT_PSF / (1.0 + 1.5671428571428572)

SUSPECT_NAMES = (
    "compute_sqft_suspects",
    "sqft_suspects",
    "compute_sqft_suspect",
    "flag_sqft_suspects",
)

CONTRACT_HINT = (
    "F5-S1 AC6: no callable computing `sqft_suspect` exists in "
    "rentcomp.pipeline.premium. QA proposes "
    "`compute_sqft_suspects(psfs, cohort_years, cohort_medians) -> list[bool]` "
    "— the same arguments `compute_premiums` already takes, in the module that "
    "already holds the cohort median. Any public name containing 'suspect' in "
    "that module satisfies this file; a different SHAPE is a conversation with "
    "the PM, who will have QA amend this file rather than the developer work "
    "around it."
)


def _suspect_fn() -> Callable[..., Any] | None:
    """The AC6 computation, however the developer named it."""
    import rentcomp.pipeline.premium as premium

    for name in SUSPECT_NAMES:
        candidate = getattr(premium, name, None)
        if callable(candidate):
            return candidate
    for name in dir(premium):
        if name.startswith("_"):
            continue
        candidate = getattr(premium, name)
        if callable(candidate) and "suspect" in name.lower():
            return candidate
    return None


@pytest.fixture
def suspects() -> Callable[..., list[bool]]:
    """`suspects(psfs, years, medians) -> list[bool]`, or a loud failure.

    A FAILING precondition, never a skip: a skip that exits 0 is
    indistinguishable from a pass (WORKFLOW.md §2), and this whole file is
    about a computation that does not exist yet.

    The failure is raised from inside the returned callable rather than from
    the fixture body so the red state reads `FAILED` on every test, not
    `ERROR` — an error in setup is easy to scan past as "harness problem".
    """
    fn = _suspect_fn()

    def _call(psfs, years, medians) -> list[bool]:
        if fn is None:
            pytest.fail(CONTRACT_HINT)
        return list(fn(psfs, years, medians))

    return _call


def test_the_flag_is_computed_somewhere_the_cohort_median_is_in_scope() -> None:
    """AC6. The one ungated failure that names what is missing.

    It lives in `pipeline/premium.py` rather than `pipeline/shape.py` for the
    reason that module's own docstring gives: the cohort median is taken over
    the SELECTED comps (F4-S5 [INVARIANT]) and therefore changes every time
    the user toggles one. A flag computed once per pull, in record shaping,
    would be answering against a median nobody is looking at.
    """
    assert _suspect_fn() is not None, CONTRACT_HINT


def test_a_comp_far_above_its_cohort_median_is_suspect(suspects) -> None:
    """AC6/AC7 in miniature: the Leavitt numbers, as plain values."""
    flags = suspects([LEAVITT_PSF], [2025], {2025: LEAVITT_COHORT_MEDIAN})
    assert flags == [True], (
        f"a comp at ${LEAVITT_PSF:.2f}/sqft against a cohort median of "
        f"${LEAVITT_COHORT_MEDIAN:.2f} is +157% off it — the case this flag exists for"
    )


def test_a_comp_far_below_its_cohort_median_is_suspect(suspects) -> None:
    """AC6 [INVARIANT]: deviation is a DISTANCE, not a signed excess.

    An sqft typed with an extra digit makes a comp's $/sqft far too LOW, and
    that corrupts a premium exactly as badly as one that is too high. A
    one-sided test would silently miss half the defect class the flag exists
    to catch.
    """
    flags = suspects([1.0], [2025], {2025: 2.0})
    assert flags == [True], "a comp at half its cohort's median $/sqft is 50% off it"


def test_a_comp_at_its_cohort_median_is_not_suspect(suspects) -> None:
    assert suspects([2.0], [2025], {2025: 2.0}) == [False]


def test_a_comp_a_little_off_its_cohort_median_is_not_suspect(suspects) -> None:
    """±10% — well inside "~30%" whatever the developer picks (see the
    module docstring on what is and is not pinned)."""
    flags = suspects([2.2, 1.8], [2025, 2025], {2025: 2.0})
    assert flags == [False, False], (
        "a comp 10% off its cohort median is ordinary market variation, not a "
        "data-entry error — flagging it makes the badge noise"
    )


def test_a_comp_with_no_psf_is_not_suspect(suspects) -> None:
    """AC5/AC6 [INVARIANT]: `False`, never `None`, never `True`.

    A comp with no square footage has no $/sqft to doubt. It is already
    reported by the *missing-sqft* badge, which is a different statement about
    a different thing: "we do not know" is not "this looks wrong". `False` is
    also what the wire type says (`DerivedComp.sqft_suspect: bool`).
    """
    flags = suspects([None], [2025], {2025: 2.0})
    assert flags == [False], "no $/sqft means nothing to compare, not a suspicion"
    assert flags[0] is False, "the wire type is `bool` — `None` does not serialize as one"


def test_a_comp_whose_cohort_has_no_median_is_not_suspect(suspects) -> None:
    """Same reasoning from the other side: nothing to deviate FROM."""
    assert suspects([9.9], [2019], {2025: 2.0}) == [False]


def test_a_zero_or_negative_cohort_median_is_not_suspect(suspects) -> None:
    """`compute_premiums` already guards `median <= 0.0` and answers `None`
    rather than dividing. The flag must not be the one place that divides by
    zero — or that reports "suspect" off a median that is itself nonsense."""
    assert suspects([2.0, 2.0], [2025, 2024], {2025: 0.0, 2024: -1.0}) == [False, False]


def test_the_flag_compares_a_comp_to_ITS_OWN_cohort(suspects) -> None:
    """AC6 [INVARIANT], and the one mistake that would still pass every test
    above: comparing every comp to a single pull-wide median.

    Two cohorts a long way apart. Each comp sits exactly on its own cohort's
    median and is far off the other's. Neither is suspect. A pull-wide median
    (~$2.50) would flag both.
    """
    flags = suspects([1.0, 4.0], [2024, 2025], {2024: 1.0, 2025: 4.0})
    assert flags == [False, False], (
        "a comp priced exactly at its own cohort's median is the definition of "
        "unremarkable — premium is time-local (NORTH_STAR) and so is this flag"
    )


def test_the_flag_is_monotone_in_deviation(suspects) -> None:
    """AC6 [INVARIANT] without pinning the [DEFAULT] threshold.

    Whatever cutoff the developer picks, a comp further from its cohort median
    can never be *less* suspect than a nearer one. This is the property that
    survives a legitimate threshold change; an equality test against 0.30
    would not.
    """
    median = 2.0
    ladder = [2.0, 2.1, 2.4, 2.8, 3.4, 5.0, 10.0]
    flags = suspects(ladder, [2025] * len(ladder), {2025: median})
    assert len(flags) == len(ladder)
    seen_true = False
    for psf, flag in zip(ladder, flags):
        if flag:
            seen_true = True
        elif seen_true:
            pytest.fail(
                f"$/sqft {psf} is further from the median ({median}) than a comp "
                f"already flagged, yet is not flagged: {list(zip(ladder, flags))}"
            )
    assert seen_true, f"nothing on this ladder flagged — it reaches 5x the median: {flags}"
    assert flags[0] is False, "a comp exactly on the median must never flag"


def test_the_flag_answers_one_value_per_comp(suspects) -> None:
    """Positional agreement with `compute_premiums`' output — the derive stage
    zips these lists together, so a length or order mismatch would attach one
    comp's suspicion to another comp's row."""
    psfs = [2.0, None, 9.0, 1.0]
    flags = suspects(psfs, [2025] * 4, {2025: 2.0})
    assert len(flags) == len(psfs)
    assert all(isinstance(flag, bool) for flag in flags), flags
    assert flags == [False, False, True, True]


# ---------------------------------------------------------------------------
# AC9 — the cut-history line's day offset
# ---------------------------------------------------------------------------

AS_OF = date(2026, 7, 1)
KNOWN_CUT_FIELDS = {"on", "from_price", "to_price"}

DAY_OFFSET_HINT = (
    "F5-S1 AC9: `Cut` on the wire carries only `on`/`from_price`/`to_price`, so "
    "the row copy `✂ 2,150 → 2,050 (day 21)` has no day to render. The offset is "
    "`(cut.on - StitchedComp.first_listed).days`, computed in the pipeline and "
    "shipped as a number — the same call `days_since_removal` made, for the same "
    "D5/D15 reasons. It must be `int | None`: `first_listed` is `date | None`."
)


def _day_offset(cut: Any) -> Any:
    """The offset field on a wire `Cut`, whatever it was named.

    Structural rather than by-name on purpose: the AC is that the day travels
    from the server, not that it is spelled `day_offset`.
    """
    payload = cut.model_dump() if hasattr(cut, "model_dump") else dict(cut)
    extras = {key: value for key, value in payload.items() if key not in KNOWN_CUT_FIELDS}
    assert extras, f"{DAY_OFFSET_HINT} (fields present: {sorted(payload)})"
    assert len(extras) == 1, (
        f"expected exactly one new field on `Cut` carrying the day offset, found "
        f"{sorted(extras)} — QA cannot tell which one the row is meant to print"
    )
    return next(iter(extras.values()))


def _comp(**overrides: Any) -> StitchedComp:
    """A minimal shaped comp; overrides are the thing under test."""
    base: dict[str, Any] = dict(
        address="1758 W 35th St",
        unit="3F",
        lat=41.83,
        lng=-87.66,
        beds=2.0,
        baths=1.0,
        sqft=900.0,
        initial_ask=1700.0,
        effective_dom=90,
        censored=False,
        removal_class="confirmed",
        cohort_year=2024,
        first_listed=date(2024, 7, 22),
    )
    base.update(overrides)
    return StitchedComp(**base)


def _derive(comps: list[StitchedComp]):
    """One derivation pass over hand-built comps — no HTTP, no pull on disk.

    Layer 1 by the decision procedure's first question: a function called
    directly with plain values. It is the lowest layer that can hold AC9,
    because the offset is attached where a `StitchedComp` becomes a
    `DerivedComp`.
    """
    request = DeriveRequest(
        pull_ref="unit-test",
        subject=Subject(
            address="3651 S Wood St", lat=41.83, lng=-87.67, sqft=1000.0, beds=3.0, baths=1.0
        ),
    )
    context = DeriveContext(config=Config(), comps=tuple(comps), as_of=AS_OF)
    return derive(request, context)


def test_a_cut_carries_the_day_of_the_spell_it_landed_on() -> None:
    """AC9. `1758 w 35th st|3f` on the real pull: listed 2024-07-22, cut
    1700 → 1600 on 2024-10-01. That is day 71, and it is the number the row
    prints. Same comp, same dates, as plain values."""
    state = _derive(
        [
            _comp(
                cut_history=(
                    PriceCut(on=date(2024, 10, 1), from_price=1700.0, to_price=1600.0),
                )
            )
        ]
    )
    (comp,) = state.comps
    assert len(comp.cut_history) == 1
    cut = comp.cut_history[0]
    assert _day_offset(cut) == 71, (
        "2024-10-01 is 71 days after the chain's first listing on 2024-07-22"
    )
    assert cut.from_price == 1700.0 and cut.to_price == 1600.0, (
        "the existing fields must survive the addition — the row prints all three"
    )
    assert cut.on == date(2024, 10, 1)


def test_a_cut_on_a_comp_that_never_said_when_it_was_listed_carries_no_day() -> None:
    """AC9 + the row-10b trap, in its second incarnation.

    `StitchedComp.first_listed` is `date | None` — `None` on any comp not
    built by `shape_raw_pull`, which is every pre-shaped synthetic fixture and
    every hand-built test comp. Exactly the shape that made
    `days_since_removal` render *"removed nulld — classifying"*.

    So the offset is `int | None`, the derive does not raise, and the row is
    expected to print the cut WITHOUT a day rather than `(day nulld)`. The
    render half of that is
    `e2e/tests/f5-s1-comp-row.spec.ts`; this is the contract half.
    """
    state = _derive(
        [
            _comp(
                first_listed=None,
                cut_history=(
                    PriceCut(on=date(2024, 10, 1), from_price=1700.0, to_price=1600.0),
                ),
            )
        ]
    )
    (comp,) = state.comps
    cut = comp.cut_history[0]
    assert _day_offset(cut) is None, (
        "with no first-listed date there is no day to count from — `None`, never 0 "
        "(which would read as 'cut on the day it listed') and never a raise"
    )
    assert cut.from_price == 1700.0 and cut.to_price == 1600.0


def test_two_cuts_each_carry_their_own_day_and_keep_their_order() -> None:
    """`2453 w 46th pl|1` on the real pull: listed 2025-10-27, cut on
    2025-12-25 (day 59) and again on 2026-03-15 (day 139)."""
    state = _derive(
        [
            _comp(
                first_listed=date(2025, 10, 27),
                cohort_year=2025,
                initial_ask=2000.0,
                cut_history=(
                    PriceCut(on=date(2025, 12, 25), from_price=2000.0, to_price=1900.0),
                    PriceCut(on=date(2026, 3, 15), from_price=1900.0, to_price=1800.0),
                ),
            )
        ]
    )
    (comp,) = state.comps
    assert [_day_offset(cut) for cut in comp.cut_history] == [59, 139]


def test_a_comp_with_no_cuts_has_no_cut_history_to_render() -> None:
    """The absence case, so the row's third line is genuinely conditional."""
    state = _derive([_comp()])
    (comp,) = state.comps
    assert comp.cut_history == []
