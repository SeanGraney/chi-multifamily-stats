"""F3-S4 [BE] Durable response cache + resumable pulls — Layer 1.

**AC (story text, verbatim):** a deliberately-thrown validation error still
leaves the raw response on disk and a re-run parses it with zero calls; a
corrupted `.tmp` (simulated crash) is never mistaken for satisfied; re-running
an identical complete pull issues **zero** calls; re-running a 3-of-4 pull
issues exactly 1; failed calls recorded distinctly from successes.

This file owns the resume arithmetic (clauses 1, 3, 4 and 5);
`test_f3s4_crash_safety.py` owns clause 2; `test_f3s4_as_of_and_memo.py` owns
the two acceptance criteria added at dispatch.

WHY LAYER 1 (AGENT_QA.md decision procedure, run per acceptance criterion)
---------------------------------------------------------------------------
Step 1 matches every clause: `run_pull(...)`, `pull_status(...)` and
`read_manifest(...)` are importable functions callable with plain values, and
what they claim is about *what was sent* and *what is on disk* — neither of
which is observable over HTTP or from a browser. The one route-visible
consequence of this story is in
`backend/tests/api/test_f3s4_search_evidence_honesty.py` (Layer 2), and it is
about honesty, not arithmetic.

WHAT THIS FILE DELIBERATELY DOES NOT PIN
-----------------------------------------
* The per-call filename scheme (`y2025-inactive-off000`) and `meta.json` as a
  name — both are this story's own [DEFAULT]. Every assertion below is about
  calls issued, bytes surviving and windows accounted for, so any mechanism
  that achieves the AC passes.
* Whether a window whose 2xx body failed to parse then reads as *satisfied* or
  as a *named gap*. Both are defensible; what may not happen is either of them
  costing a call, and `calls_to_complete` promising a call that `run_pull`
  would refuse to make.
* Whether a failed query stops the pull or it carries on: every failure case
  below fails the LAST window, so the expected gap and the expected resume
  cost are identical either way.
"""

from __future__ import annotations

import json

import httpx
import pytest
from f3s4_support import (
    FETCHABLE,
    SEARCH,
    TODAY,
    NoPurchase,
    Wire,
    body,
    entry_snapshot,
    identity,
    one_record_per_query,
    ok,
    planned,
    readable_raw_bodies,
    record,
    seed_fixtures,
    unseed_fixtures,
)

from rentcomp.client.pull import pull_status, run_pull
from rentcomp.storage.cache import raw_response_paths, read_manifest
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import load_ledger
from rentcomp.storage.pulls import load_shaped_pull

#: 200s whose bodies are valid JSON and are not what the client can use —
#: D24's "a validation bug **or a schema surprise** must never cost an API
#: call". The call was made, the money is gone, and the bytes are the only
#: thing standing between us and paying for them again.
#:
#: TWO shapes, because one of them is not enough and this file learned that the
#: hard way. The first was all this spec originally carried; it reaches the
#: client's own "I cannot read this" refusal, so it exercised the *typed*
#: failure path only. The second is the canonical shape of a RentCast schema
#: change, and until the fix round it was read by the fetch path as an ordinary
#: empty answer while the store read the same bytes as unusable — a window
#: filed satisfied, demoted on every read, and **bought again on every attempt
#: with no stopping condition** (measured at verify: 4 of 4 windows, 4 calls
#: per attempt, 24 of 50 in six tries). The AC below was green against the
#: first shape and false against the second, which is precisely why the story's
#: money invariant has to be asserted over the *class* of unusable body rather
#: than over one specimen of it.
UNUSABLE_BODIES = {
    # `listings` present, not a list -> the client raises on its own.
    "a listings value that is not a list": b'{"listings": "this is not a list"}',
    # No `listings` key at all -> what a schema change actually looks like.
    "an envelope this client has never seen": b'{"data": [{"id": "x"}]}',
}
POISON = UNUSABLE_BODIES["a listings value that is not a list"]


# ===========================================================================
# 0. the harness itself — a FAILING precondition, never a skip
# ===========================================================================
#
# "A skip that exits 0 is indistinguishable from a pass" (WORKFLOW.md §2, the
# defect class that has recurred four times here). If the search below stops
# producing 4 fetchable windows, or a healthy pull stops shaping into one comp
# per window, every count in this file becomes meaningless — so that is a
# failure, loudly, rather than a quietly weakened suite.


def test_the_harness_pulls_four_windows(live_env, no_sockets) -> None:
    """4 fetchable queries, 4 responses kept, 4 comps shaped.

    The evidence meter the rest of the file reads: because each window answers
    with one record at a unique address, `len(comps)` is exactly "how many
    windows' evidence survived".
    """
    queries = planned()
    assert len(queries) == FETCHABLE, (
        f"the harness search now plans {len(queries)} fetchable windows, not {FETCHABLE}. "
        "The story's AC is literally '3-of-4' — fix the search, not the assertion."
    )

    wire = Wire(one_record_per_query)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    assert len(wire.requests) == FETCHABLE
    assert outcome.complete is True and outcome.calls_spent == FETCHABLE
    assert len(raw_response_paths(outcome.pull_ref)) == FETCHABLE
    assert len(load_shaped_pull(outcome.pull_ref, Config()).comps) == FETCHABLE, (
        "a healthy pull of 4 windows, each answering with one unique in-window record, "
        "must shape into exactly 4 comps — otherwise the comp count cannot be used as "
        "this file's evidence meter"
    )


# ===========================================================================
# AC clause 1 — a validation error leaves the bytes on disk, and the re-run
#               parses them for FREE
# ===========================================================================


@pytest.mark.parametrize("shape", sorted(UNUSABLE_BODIES))
def test_a_response_our_parser_rejects_is_kept_and_never_bought_again(
    live_env, no_sockets, shape
) -> None:
    """The AC's first clause, both halves, and the one D24 exists for.

    ARCHITECTURE.md §5a: "Persist the response body to disk, *then* run
    Pydantic. A validation bug or a schema surprise must never cost an API
    call — the bytes are already safe and the parse can be re-run for free
    forever after."

    Window 3 answers 200 with a body the client cannot use. The call is spent
    either way. So:

    * the bytes must be on disk, byte-identical, because they are the only
      thing that makes the next parse free; and
    * the re-run must not go back to the source for them — `NoPurchase` raises
      at the reach rather than counting it (see `f3s4_support`).

    **Parametrised over both unusable shapes, and that is the point.** Written
    with one specimen, this test was green while the AC it names was false:
    the shape it used reached the client's typed refusal, and the shape it did
    not use reached nothing at all — the fetch path called it an empty answer,
    the store called it unusable, and the window was re-bought on every attempt
    forever. A money invariant asserted over one specimen of a class is an
    invariant asserted over none of it. See `UNUSABLE_BODIES`.
    """
    unusable = UNUSABLE_BODIES[shape]

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        return (
            httpx.Response(200, content=unusable)
            if n == 3
            else one_record_per_query(request, n)
        )

    wire = Wire(answer)
    first = run_pull(**SEARCH, today=TODAY, transport=wire.transport)
    ref = first.pull_ref

    assert len(wire.requests) == FETCHABLE
    stored = {path.read_bytes() for path in raw_response_paths(ref)}
    assert unusable in stored, (
        f"[{shape}] the response that failed to parse was not kept. It was a 200, it was "
        "billed, and the raw bytes are what make re-parsing it free forever (D24)."
    )
    assert len(stored) == FETCHABLE, (
        f"{len(stored)} of {FETCHABLE} paid-for responses are on disk — a call was spent "
        "and its answer thrown away"
    )

    assert first.calls_to_complete == 0, (
        f"[{shape}] the pull says {first.calls_to_complete} call(s) would complete it, but every "
        "window's response is already on disk and re-buying window 3 would return the same "
        "bytes. The number the user is shown and the number a resume actually spends must "
        "be one number (the F2-S3 invariant) — and here both must be zero."
    )

    second = run_pull(**SEARCH, today=TODAY, transport=NoPurchase("its 200 body is on disk").transport)

    assert second.pull_ref == ref
    assert second.calls_spent == 0
    assert unusable in {path.read_bytes() for path in raw_response_paths(ref)}, (
        f"[{shape}] the re-run overwrote or dropped the kept bytes"
    )


def test_the_kept_bytes_are_exactly_what_arrived_so_a_later_parse_can_use_them(
    live_env, no_sockets
) -> None:
    """"...and a re-run **parses it**": the bytes have to be re-parseable.

    A cache that re-serialized through `json.dumps`, or that stored the client's
    *interpretation* of a response, would satisfy "on disk" while destroying the
    only thing that makes the next parse honest — `cache/` is the immutable
    evidence every number traces back to (NORTH_STAR).
    """
    exact = (
        b'[{"id":"byte-exact","formattedAddress":"9 Raw St","price":2000,'
        b'"listedDate":"2026-03-01T00:00:00.000Z","bedrooms":3,"squareFootage":900}]'
    )

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        return httpx.Response(200, content=exact if n == 1 else body(n))

    wire = Wire(answer)
    outcome = run_pull(**SEARCH, today=TODAY, transport=wire.transport)

    on_disk = {path.read_bytes() for path in raw_response_paths(outcome.pull_ref)}
    assert exact in on_disk, (
        "the stored response is not byte-identical to the body that arrived, so what a "
        "later parse reads is this tool's opinion of the evidence rather than the evidence"
    )
    assert len(readable_raw_bodies(outcome.pull_ref)) == FETCHABLE, (
        "every kept response must still parse as JSON off disk — that is what 'a re-run "
        "parses it with zero calls' means"
    )


# ===========================================================================
# AC clause 3 — an identical complete pull costs ZERO
# ===========================================================================


def test_rerunning_an_identical_complete_pull_issues_zero_calls_in_fixture_mode(
    home, fixture_mode, no_sockets
) -> None:
    """The AC's third clause, in the mode the product actually runs in.

    `test_f4s9_pull_orchestrator.py::test_rerunning_a_complete_pull_issues_zero_calls`
    owns the live-path half with a raising transport. This is the same claim on
    the fixture path, which every test, every dev server and every Playwright
    run takes — and the two paths persist their pages through *different* code
    (`_fetch_from_fixtures` never reaches the sink), so one being right does
    not make the other right.

    Structural, not a counter: the saved responses are deleted before the
    second run, so a pull that went back to the source finds nothing and flips
    its windows to unsatisfied. Nothing else in the system can turn a complete
    pull incomplete.
    """
    seed_fixtures(fixture_mode)
    first = run_pull(**SEARCH, today=TODAY)
    assert first.complete is True, f"the seeded pull did not complete: {first.missing}"

    before = entry_snapshot(home, first.pull_ref)
    unseed_fixtures(fixture_mode)

    second = run_pull(**SEARCH, today=TODAY)

    assert second.complete is True, (
        f"re-running an identical complete pull went back to the source: {second.missing} "
        "now reads as missing, and the only way that can happen is a re-fetch of windows "
        "whose responses were already on disk (D24)."
    )
    assert second.calls_spent == 0
    assert entry_snapshot(home, first.pull_ref) == before, (
        "the cache entry was rewritten by a pull that had nothing to buy. Bytes AND mtimes "
        "are compared here: re-fetching a response and writing back identical bytes is "
        "still a call spent twice."
    )


def test_rerunning_an_identical_complete_pull_does_not_reach_for_the_wire(
    live_env, no_sockets
) -> None:
    """The same clause where calls are countable, asserted by the absence of
    the mechanism rather than by `calls_this_month == 0`.

    A ledger assertion is inert in fixture mode and merely *lagging* on the
    live path — the ledger increments at send time, so a test that only reads
    it afterwards is describing the damage rather than preventing it.
    """
    run_pull(**SEARCH, today=TODAY, transport=Wire(one_record_per_query).transport)
    spent = load_ledger().calls_this_month

    outcome = run_pull(
        **SEARCH, today=TODAY, transport=NoPurchase("the pull is already whole").transport
    )

    assert outcome.calls_spent == 0
    assert outcome.complete is True
    assert load_ledger().calls_this_month == spent


# ===========================================================================
# AC clause 4 — a 3-of-4 pull resumes for exactly 1
# ===========================================================================


def test_rerunning_a_three_of_four_pull_issues_exactly_one_call(live_env, no_sockets) -> None:
    """The AC's fourth clause, literally: 4 windows, 3 held, 1 owed.

    Asserted on the *identity* of the request as well as the count — a resume
    that sent one call for the wrong window would satisfy a bare count while
    buying a response it already had and still leaving the gap open.
    """
    queries = planned()

    def three_then_fail(request: httpx.Request, n: int) -> httpx.Response:
        if n <= 3:
            return one_record_per_query(request, n)
        return httpx.Response(503, json={"error": "gone"})

    first = run_pull(**SEARCH, today=TODAY, transport=Wire(three_then_fail).transport)
    assert first.complete is False and first.calls_to_complete == 1, (
        f"the setup did not hold: {first.calls_to_complete} calls to complete a 3-of-4 pull"
    )
    spent_after_first = load_ledger().calls_this_month

    resume = Wire(lambda request, n: one_record_per_query(request, 100 + n))
    second = run_pull(**SEARCH, today=TODAY, transport=resume.transport)

    assert len(resume.requests) == 1, (
        f"the resume sent {len(resume.requests)} requests ({resume.sent}); 3 of 4 windows "
        "were already on disk, so exactly 1 was owed. Re-fetching a response already paid "
        "for is paying twice (D24 / §5a)."
    )
    missing_window = queries[3]
    assert identity(resume.requests[0])[:2] == (
        missing_window.params["status"],
        missing_window.params["daysOld"],
    ), (
        f"the resume bought {identity(resume.requests[0])}, but the window that never "
        f"arrived was {missing_window.year} {missing_window.status}. One call, spent on "
        "the wrong thing, leaves the gap open and the budget shorter."
    )
    assert load_ledger().calls_this_month == spent_after_first + 1
    assert second.complete is True
    assert len(load_shaped_pull(second.pull_ref, Config()).comps) == FETCHABLE, (
        "after the remainder arrived, every window's evidence must be derivable"
    )


def test_the_calls_to_complete_a_pull_is_what_completing_it_actually_costs(
    live_env, no_sockets
) -> None:
    """The clause behind the clause: the number the user consents to and the
    number the resume spends are one number.

    F4-S6 renders `calls_to_complete` verbatim ("2025 inactive missing - 1 call
    to complete") and F3-S2's modal is where the user says yes to it. A pull
    that quotes 1 and spends 2 has taken money the user did not agree to.
    Parametrised over how much of the pull failed, at Layer 1, because that is
    where a loop belongs.
    """
    for failures in (1, 2, 3, 4):
        # A fresh cache entry per iteration: the ref is a function of the
        # search, so the search is what has to differ. `SEARCH` itself is never
        # mutated — a module-level dict edited in place would leak into every
        # other test in this directory in file order.
        search = {**SEARCH, "address": f"{3651 + failures} S Wood St, Chicago, IL 60609"}

        def answer(request: httpx.Request, n: int, _f=failures) -> httpx.Response:
            if n <= FETCHABLE - _f:
                return one_record_per_query(request, n)
            return httpx.Response(503, json={"error": "gone"})

        first = run_pull(**search, today=TODAY, transport=Wire(answer).transport)
        quoted = first.calls_to_complete
        assert quoted == failures, (
            f"{failures} of {FETCHABLE} windows never arrived but the pull quotes {quoted} "
            "call(s) to complete"
        )

        resume = Wire(lambda request, n: one_record_per_query(request, 200 + n))
        second = run_pull(**search, today=TODAY, transport=resume.transport)

        assert len(resume.requests) == quoted, (
            f"[{failures} failed] the pull quoted {quoted} call(s) to complete and the "
            f"resume spent {len(resume.requests)}"
        )
        assert second.complete is True and second.calls_to_complete == 0


# ===========================================================================
# AC clause 5 — failures recorded DISTINCTLY from successes
# ===========================================================================


def test_a_failed_window_is_recorded_distinctly_from_a_satisfied_one(
    live_env, no_sockets
) -> None:
    """The AC's fifth clause, on disk, where §5a puts it: "planned queries,
    which are satisfied, which failed **and why**".

    Distinctness is asserted three ways, because "recorded" can be true and
    useless: the two states are separable, the failures carry a reason, the
    successes do not carry a spurious one, and the two reasons differ from each
    other (a 429 and a 500 demand opposite actions — waiting versus retrying —
    and a record that flattens them is a record nobody can act on).
    """

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n == 2:
            return httpx.Response(429, json={"error": "slow down"})
        if n == 4:
            return httpx.Response(500, json={"error": "upstream is having a day"})
        return one_record_per_query(request, n)

    outcome = run_pull(**SEARCH, today=TODAY, transport=Wire(answer).transport)

    manifest = read_manifest(outcome.pull_ref)
    satisfied = [query for query in manifest.queries if query.satisfied]
    failed = [query for query in manifest.queries if not query.satisfied]

    assert len(satisfied) == 2 and len(failed) == 2, (
        f"2 windows answered and 2 failed, but the manifest records {len(satisfied)} "
        f"satisfied and {len(failed)} failed"
    )
    assert all(query.error for query in failed), (
        "a failed window is recorded with no reason. §5a: 'which failed and why' — a "
        "retry with no idea what went wrong is blind, and one of them (a 429) must not be "
        "retried at all."
    )
    assert not any(query.error for query in satisfied), (
        "a window that answered carries an error string, so 'failed' and 'succeeded' are "
        "not actually separable on disk"
    )
    assert len({query.error for query in failed}) == 2, (
        "the rate-limit failure and the server failure are recorded identically. They "
        "demand opposite actions from the user, and a shared message sends them the wrong "
        "way."
    )


def test_a_failed_call_is_billed_but_files_no_evidence(live_env, no_sockets) -> None:
    """The other half of "distinctly": a failure costs a call and produces
    nothing that a resume could mistake for an answer.

    The F0-S4 ruling this story inherits (QUEUE.md, 2026-07-27): the sink fires
    on **2xx only**, because writing a 401's error blob into `cache/<key>/raw/`
    would close the gap on nothing. The ledger is where failures live — and it
    still counts them, because the quota was spent the moment the request left.
    """

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n >= 3:
            return httpx.Response(401, json={"error": "bad key"})
        return one_record_per_query(request, n)

    outcome = run_pull(**SEARCH, today=TODAY, transport=Wire(answer).transport)

    assert len(raw_response_paths(outcome.pull_ref)) == 2, (
        "an error body was filed in the raw-response store. A resume diffing against those "
        "files would believe the failed window had been answered, and the gap would close "
        "on an error blob."
    )
    ledger = load_ledger()
    assert ledger.calls_this_month == FETCHABLE, (
        f"{ledger.calls_this_month} calls recorded for {FETCHABLE} requests sent. The quota "
        "is spent when a request leaves, not when it succeeds (§5a)."
    )
    statuses = [call.status for call in ledger.history]
    assert statuses.count(401) == 2 and statuses.count(200) == 2, (
        f"the ledger's history does not separate the failures from the successes: {statuses}"
    )


def test_the_gap_and_its_reason_survive_the_process_that_made_it(
    live_env, no_sockets
) -> None:
    """A failure recorded only in a returned object is gone when the tool
    closes. The user who reopens the search tomorrow must still be told what is
    absent and why — and finding out must not cost a call.
    """

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n == FETCHABLE:
            return httpx.Response(503, json={"error": "gone"})
        return one_record_per_query(request, n)

    outcome = run_pull(**SEARCH, today=TODAY, transport=Wire(answer).transport)
    spent = load_ledger().calls_this_month

    reread = pull_status(outcome.pull_ref)

    assert reread.complete is False
    assert list(reread.missing) == list(outcome.missing)
    assert reread.calls_to_complete == outcome.calls_to_complete
    assert load_ledger().calls_this_month == spent, (
        "reading a pull's status spent a call. Reading must never be a mutation and must "
        "certainly never be a purchase."
    )


# ===========================================================================
# PAGE-LEVEL RESUME — scope confirmation requested from the PM
# ===========================================================================
#
# `client/pull.py`'s own docstring defers this here by name: "Page-level resume
# (re-entering a truncated query at its last offset) is F3-S4's". It is not in
# the five AC clauses, but it is a straightforward instance of the [INVARIANT]
# the story states in its own words — "no double-pay" — and on the live path it
# is a real call re-spent. Flagged rather than assumed: if the PM rules it out
# of scope, this one test comes out and nothing else in the file changes.


@pytest.mark.xfail(
    strict=False,
    reason=(
        "SCOPE QUESTION for the PM, not a defect claim. F4-S9's `client/pull.py` defers "
        "page-level resume to F3-S4 by name; the story's five AC clauses do not mention it. "
        "Non-strict on purpose: it is allowed to pass (the developer implemented it) and "
        "allowed to fail (the PM ruled it out), and neither should break the gate before "
        "the ruling."
    ),
)
def test_a_query_interrupted_between_pages_does_not_re_buy_the_page_it_holds(
    live_env, no_sockets
) -> None:
    """§5a's "a pull interrupted at 3/4 costs exactly 1 call to finish", one
    level down: the unit that was paid for is the CALL, not the query.

    Window 1 needs two calls; the first arrives and the second fails. The
    resume owes the second page only — re-entering at offset 0 buys a page
    that is already filed under this ref.
    """
    first_page = [record(1), record(2)]

    def answer(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return ok(first_page, total=3)
        if n == 2:
            return httpx.Response(503, json={"error": "gone"})
        return one_record_per_query(request, n + 10)

    first = run_pull(**SEARCH, today=TODAY, transport=Wire(answer).transport)
    held = {path.read_bytes() for path in raw_response_paths(first.pull_ref)}
    assert json.dumps(first_page).encode("utf-8") in {
        json.dumps(json.loads(blob)).encode("utf-8") for blob in held
    }, "the setup did not hold: page 0 of the truncated window is not on disk"

    resume = Wire(lambda request, n: ok([record(3)], total=3))
    second = run_pull(**SEARCH, today=TODAY, transport=resume.transport)

    offsets = [identity(request)[2] for request in resume.requests]
    assert not any(offset in (None, "0") for offset in offsets), (
        f"the resume re-entered a truncated window at offset 0 ({resume.sent}), buying a "
        "page whose bytes are already filed under this ref. Per-call file granularity is "
        "what makes a partial set meaningful; resuming per *query* throws that away (§5a)."
    )
    # The other half, and the reason this test is not satisfied by silence: a
    # resume that decided the window was already held and skipped it entirely
    # would send nothing at all, spend nothing, and leave the gap open forever.
    # (Caught by probing a candidate fix against this file — the naive
    # "any bytes under this signature means satisfied" rule XPASSes the line
    # above while never fetching page 1.)
    assert len(resume.requests) == 1, (
        f"the resume sent {resume.sent} — the truncated window owes exactly its second "
        "page: one call, at the offset where the interruption happened"
    )
    assert second.complete is True, (
        "the resume issued no call for the page that never arrived, so the window is still "
        "truncated. Not re-buying page 0 is only half the requirement; the other half is "
        "that page 1 is still owed and still bought."
    )
