"""F3-S4 clause 2 — "a corrupted `.tmp` (simulated crash) is never mistaken
for satisfied", across every shape a crash can leave behind. Layer 1.

WHY THIRTEEN SHAPES AND NOT ONE
--------------------------------
Because "corrupt" is not one condition, and this project has twice been shown
that a single-case test misses what matters. F4-S9's QA probed a corrupt
manifest thirteen-ways-less and still found that **four of six shapes re-bought
the whole pull**; F2-S3's route escaped as a 500 on the three shapes where the
manifest's *types* were wrong rather than its values. A crash can leave:

* a `.tmp` beside a response that landed (harmless, and must stay harmless)
* a `.tmp` **instead of** the response — the rename never happened, so the
  bytes are not evidence however complete they look
* a response file truncated, emptied, or replaced by a directory
* a response file simply gone, while the manifest still says it arrived
* a manifest that is not JSON, is JSON of the wrong kind, or has the right
  field names and the wrong value types
* a `.tmp` left beside the manifest itself

ADDING THE FOURTEENTH SHAPE IS A ONE-LINE EDIT to `CRASH_SHAPES` below — that
is the point of the table, and the reason the money and honesty invariants are
written once and parametrised rather than restated per case.

THE TWO INVARIANTS, ASSERTED FOR EVERY SHAPE
---------------------------------------------
1. **No double-pay** (D24, and the story's own [INVARIANT] wording): a
   recovery run may buy at most the responses the crash actually destroyed.
   Nothing may be re-fetched to work around a broken *manifest* — those bytes
   are on disk and were already paid for.
2. **Never mistaken for satisfied**: a pull that reports itself complete must
   be able to produce every window's evidence. Asserted through
   `load_shaped_pull`, because "the manifest says satisfied" and "the evidence
   exists" are exactly the two things a crash separates.

Both are stated in terms of calls and comps, never of filenames, so this story's
[DEFAULT] file layout stays free to change.
"""

from __future__ import annotations

import json

import pytest
from f3s4_support import (
    FETCHABLE,
    SEARCH,
    TODAY,
    Wire,
    entry_dir,
    one_record_per_query,
    planned,
    readable_raw_bodies,
    record,
)

from rentcomp.client.pull import run_pull
from rentcomp.storage.cache import CORRUPT_MANIFEST_ERRORS, raw_response_paths
from rentcomp.storage.config import Config
from rentcomp.storage.pulls import PullNotFoundError, load_shaped_pull

#: A record that exists ONLY inside a `.tmp` — its address is the tracer for
#: "did a half-written file get read as evidence".
GHOST = record(99)
GHOST_BODY = json.dumps([GHOST]).encode("utf-8")

#: What a crash mid-write leaves behind under the writer this story ships
#: (`storage/cache.py::_atomic_write_bytes` → `.<name>.tmp`) and under the
#: other obvious convention. Both must be invisible: the [DEFAULT] leaves the
#: developer free to rename the suffix, and neither name may ever be evidence.
def _tmp_names(name: str) -> tuple[str, str]:
    return f".{name}.tmp", f"{name}.tmp"


# ---------------------------------------------------------------------------
# the shapes
# ---------------------------------------------------------------------------
#
# Each entry is `(apply, destroyed)` — `destroyed` is how many paid-for
# responses the crash genuinely lost, and therefore the most a recovery run is
# allowed to buy.


def _break_manifest(text: str):
    def apply(entry, raw_paths) -> None:
        (entry / "manifest.json").write_text(text, encoding="utf-8")

    return apply


def _delete_manifest(entry, raw_paths) -> None:
    (entry / "manifest.json").unlink()


def _stray_manifest_tmp(entry, raw_paths) -> None:
    """The manifest landed; the crash left a half-written temp beside it."""
    for name in _tmp_names("manifest.json"):
        (entry / name).write_bytes(b'{"as_of": "2099-')


def _stray_raw_tmp(entry, raw_paths) -> None:
    """A `.tmp` beside a response that DID land. Harmless — and it carries a
    record that exists nowhere else, so if it is ever read the tracer shows up
    in the comps."""
    target = raw_paths[0]
    for name in _tmp_names(target.name):
        target.with_name(name).write_bytes(GHOST_BODY)


def _tmp_instead_of_the_response(entry, raw_paths) -> None:
    """The crash landed between write and rename: complete bytes, in a file
    that was never promoted. Not evidence, however whole it looks."""
    target = raw_paths[0]
    payload = target.read_bytes()
    target.unlink()
    target.with_name(f".{target.name}.tmp").write_bytes(payload)


def _delete_a_response(entry, raw_paths) -> None:
    raw_paths[0].unlink()


def _truncate_a_response(entry, raw_paths) -> None:
    raw_paths[0].write_bytes(raw_paths[0].read_bytes()[: len(raw_paths[0].read_bytes()) // 2])


def _empty_a_response(entry, raw_paths) -> None:
    raw_paths[0].write_bytes(b"")


def _response_is_a_directory(entry, raw_paths) -> None:
    target = raw_paths[0]
    target.unlink()
    target.mkdir()


CRASH_SHAPES: dict[str, tuple] = {
    # --- the manifest broke; every paid-for response is still on disk -------
    "manifest deleted": (_delete_manifest, 0),
    "manifest unparseable": (_break_manifest("{ not json"), 0),
    "manifest truncated to nothing": (_break_manifest(""), 0),
    "manifest is json but not an object": (_break_manifest("null"), 0),
    "manifest is a json array": (_break_manifest("[]"), 0),
    "manifest queries are not records": (
        _break_manifest(
            json.dumps({"as_of": "2026-07-27", "window": ["01-01", "12-31"], "queries": ["nope"]})
        ),
        0,
    ),
    "manifest window is not a sequence": (
        _break_manifest(json.dumps({"as_of": "2026-07-27", "window": 5})),
        0,
    ),
    "manifest as_of is not a date": (
        _break_manifest(json.dumps({"as_of": "someday", "window": ["01-01", "12-31"]})),
        0,
    ),
    "a half-written manifest tmp was left behind": (_stray_manifest_tmp, 0),
    # --- a response is intact, with debris beside it ------------------------
    "a half-written response tmp was left beside a good response": (_stray_raw_tmp, 0),
    # --- a response was genuinely lost --------------------------------------
    "a response tmp was left INSTEAD of the response": (_tmp_instead_of_the_response, 1),
    "a response was deleted": (_delete_a_response, 1),
    "a response was truncated mid-json": (_truncate_a_response, 1),
    "a response is a zero-byte file": (_empty_a_response, 1),
    "a directory stands where a response belongs": (_response_is_a_directory, 1),
}


@pytest.fixture
def crashed(home, live_env, no_sockets):
    """A healthy 4-window pull, then a crash of the requested shape.

    Returns `(ref, destroyed, readable_before)`.
    """

    def _crash(shape: str):
        outcome = run_pull(**SEARCH, today=TODAY, transport=Wire(one_record_per_query).transport)
        assert outcome.complete is True and outcome.calls_spent == FETCHABLE, (
            "the healthy pull this file corrupts did not complete, so nothing below would "
            "prove anything"
        )
        raw_paths = raw_response_paths(outcome.pull_ref)
        assert len(raw_paths) == FETCHABLE

        apply, destroyed = CRASH_SHAPES[shape]
        apply(entry_dir(home, outcome.pull_ref), raw_paths)
        load_shaped_pull.cache_clear()  # the crash = a new process
        return outcome.pull_ref, destroyed, FETCHABLE - destroyed

    return _crash


#: The shapes that break the manifest and leave every response intact — the
#: subset the "unhandled error" test below is about.
MANIFEST_SHAPES = sorted(
    shape for shape, (_apply, destroyed) in CRASH_SHAPES.items()
    if destroyed == 0 and shape.startswith("manifest")
)


def _recover(shape: str, *, base: int):
    """The recovery run every test below makes, with one shared translation of
    "it crashed" into a sentence that says why that matters.

    A manifest-shaped failure escaping `run_pull` is a real finding and it has
    its own test; here it would otherwise surface as a bare `TypeError` in the
    middle of a money assertion and read like a broken test rather than a
    broken guarantee.
    """
    wire = Wire(lambda request, n: one_record_per_query(request, base + n))
    try:
        outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)
    except CORRUPT_MANIFEST_ERRORS as exc:
        pytest.fail(
            f"[{shape}] run_pull raised {type(exc).__name__}: {exc}. It could not get far "
            "enough to be judged on money or honesty — see "
            "test_no_shape_of_a_broken_manifest_escapes_the_pull_as_an_unhandled_error, "
            "which owns that claim."
        )
    return wire, outcome


def _comps(ref):
    """The evidence the pull can actually produce, or `None` if it refuses.

    A refusal is an acceptable answer to a corrupt entry (loud beats silent);
    what is not acceptable is a *complete* pull that quietly produces less than
    it claims.
    """
    try:
        return len(load_shaped_pull(ref, Config()).comps)
    except (PullNotFoundError, OSError, ValueError):
        return None


# ===========================================================================
# 1. MONEY — a recovery buys at most what the crash destroyed
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CRASH_SHAPES))
def test_a_crash_never_costs_more_calls_than_the_responses_it_destroyed(
    crashed, shape
) -> None:
    """The story's [INVARIANT], in its own words: **no double-pay**.

    Eight of these shapes destroy nothing at all — the manifest is an index,
    and every response it indexes is still sitting in `raw/`. Re-fetching them
    to recover from a broken index is the whole pull bought a second time to
    work around a file the user could have deleted for free: on the live path,
    4 of the 50 calls the owner has this month.

    RED as at dispatch for the manifest shapes: `client/pull.py::_known_queries`
    answers "nothing is known to be satisfied" for any manifest it cannot read,
    which makes every window owed again. The bytes on disk are the evidence
    that they are not.
    """
    ref, destroyed, _ = crashed(shape)

    recovery, _outcome = _recover(shape, base=300)

    assert len(recovery.requests) <= destroyed, (
        f"[{shape}] the recovery bought {len(recovery.requests)} call(s) where at most "
        f"{destroyed} response(s) were actually lost ({recovery.sent}). A response that has "
        "been paid for is never bought again, whatever happened to the file that indexes it "
        "(D24 / ARCHITECTURE.md §5a)."
    )


@pytest.mark.parametrize("shape", sorted(CRASH_SHAPES))
def test_a_crash_never_destroys_a_response_that_survived_it(crashed, shape) -> None:
    """Recovery must not tidy away the evidence it is recovering.

    The responses that are still readable after the crash must still be
    readable after the recovery — a repair that deleted the entry and started
    over would satisfy every "is it consistent now" test while throwing away
    calls the owner has already paid for.
    """
    ref, destroyed, survivors = crashed(shape)
    assert len(readable_raw_bodies(ref)) == survivors, (
        f"[{shape}] the crash left {len(readable_raw_bodies(ref))} readable responses, not "
        f"{survivors} — the shape table's `destroyed` count is wrong, so fix the table"
    )

    _recover(shape, base=400)

    assert len(readable_raw_bodies(ref)) >= survivors, (
        f"[{shape}] recovery left {len(readable_raw_bodies(ref))} readable responses where "
        f"{survivors} survived the crash. Paid-for bytes are never rolled back, and never "
        "cleaned up either."
    )


@pytest.mark.parametrize("shape", MANIFEST_SHAPES)
def test_no_shape_of_a_broken_manifest_escapes_the_pull_as_an_unhandled_error(
    crashed, shape
) -> None:
    """`storage.cache.CORRUPT_MANIFEST_ERRORS` is the project's one list of
    "this entry exists and cannot be read", and it is imported here rather than
    re-spelled so that the day a fourteenth shape needs a sixth error type,
    adding it to that tuple covers this test too.

    `client/pull.py::_known_queries` catches `(LookupError, OSError,
    ValueError)` — three of the five. The other two (`AttributeError` from a
    `queries` list of non-records, `TypeError` from a `window` that is not a
    sequence) walk straight out of `run_pull`, and this is the module that
    reaches them *before* the route's own guard can. F2-S3's QA found exactly
    this gap one layer up and the fix was to widen both `except` clauses to the
    shared name; the pull was not widened with them.
    """
    crashed(shape)

    try:
        run_pull(
            **SEARCH,
            today=TODAY,
            transport=Wire(lambda request, n: one_record_per_query(request, 700 + n)).transport,
        )
    except CORRUPT_MANIFEST_ERRORS as exc:
        pytest.fail(
            f"[{shape}] a broken manifest escaped run_pull as {type(exc).__name__}: {exc}. "
            "The pull's own contract is that a per-query failure is returned rather than "
            "raised, and `_known_queries` already means to swallow an unreadable manifest — "
            "it just enumerates three of the five error types "
            "`storage.cache.CORRUPT_MANIFEST_ERRORS` names. Every caller of run_pull turns "
            "this into an anonymous 500."
        )


# ===========================================================================
# 2. HONESTY — never mistaken for satisfied
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(CRASH_SHAPES))
def test_a_pull_that_calls_itself_complete_can_produce_every_windows_evidence(
    crashed, shape
) -> None:
    """The AC's second clause, generalised past `.tmp` to every crash shape.

    "Complete" is a claim about evidence, not about a flag: each of the four
    windows answered with one record at a unique address, so a complete pull
    derives exactly four comps. A pull that says complete and derives three has
    mistaken a lost response for a satisfied one — and a missing cohort skews
    every number downstream while nothing on screen says so (§5a).

    RED as at dispatch for the five shapes that destroy a response: the
    manifest's `satisfied` flag survives the file it describes, so the pull
    reports itself whole over evidence that is gone.
    """
    ref, destroyed, _ = crashed(shape)

    _recovery, outcome = _recover(shape, base=500)

    if not outcome.complete:
        return  # an honestly-incomplete pull is a legitimate answer to a crash
    assert _comps(outcome.pull_ref) == FETCHABLE, (
        f"[{shape}] the pull reports itself complete but derives "
        f"{_comps(outcome.pull_ref)} of {FETCHABLE} windows' evidence. Either the missing "
        "window must be named (`missing`/`calls_to_complete`) or its response must be "
        "re-fetched — silently short is the one answer that is not available."
    )


@pytest.mark.parametrize(
    "shape",
    [
        "a half-written response tmp was left beside a good response",
        "a response tmp was left INSTEAD of the response",
    ],
)
def test_a_half_written_response_is_never_read_as_evidence(crashed, shape) -> None:
    """The AC's literal words: a corrupted `.tmp` is never mistaken for
    satisfied — nor for evidence.

    The planted `.tmp` carries a record that exists nowhere else. If its
    address ever reaches a comp, then a file the atomic writer had not yet
    promoted was read as if it were a paid-for response — which is exactly the
    failure `write .tmp → fsync → rename` exists to make impossible.
    """
    ref, _, _ = crashed(shape)

    _recovery, outcome = _recover(shape, base=600)

    try:
        comps = load_shaped_pull(outcome.pull_ref, Config()).comps
    except (PullNotFoundError, OSError, ValueError):
        return  # refusing to read a damaged entry is fine; reading the tmp is not

    addresses = {str(getattr(comp, "address", "")) for comp in comps}
    addresses |= {str(getattr(comp, "formatted_address", "")) for comp in comps}
    assert GHOST["formattedAddress"] not in addresses, (
        f"[{shape}] a record that exists only inside a half-written `.tmp` reached the "
        "shaped comps. A file the rename never promoted is not a response; treating it as "
        "one is how a crash turns into a wrong number."
    )
