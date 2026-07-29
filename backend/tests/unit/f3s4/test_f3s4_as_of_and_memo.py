"""F3-S4's two ACs added at dispatch — Layer 1.

ADDED AC A (PM RULING, dispatch): **`as_of` advances only when bytes actually
arrive.** `run_pull` restamps the manifest's `as_of` to the day of the attempt
whenever any query is owed — including when every one of them then fails and
not one byte is written (`client/pull.py:262-267`; the "don't restamp a fetch
that did not happen" guard at line 249 covers only the nothing-owed branch).
Because a censored comp's DOM is `as_of − listed`, the same disk yields two
different answers across a process restart, and it propagates into premium,
buckets and the KM curve. The PM's ruling records that this needs no Semantic
Change Protocol write-up: `as_of` already means "when this evidence was
observed", and restamping it for a fetch that delivered nothing contradicts
that definition rather than redefining it.

ADDED AC B (architecture checkpoint 2, ADR-002 note 3): completing a partial
pull appends evidence to an existing ref, which is the premise ADR-001 dropped
`pull_digest` on. F4-S9 already restored a raw-file fingerprint
(`storage/pulls.py::_evidence_version`); this file pins the outcome from both
directions so that whichever mechanism the developer picks — digest in the memo
key, or a new ref on completion — is covered, and so that the half
`_evidence_version` does **not** cover (the manifest) is visible.

NOT DUPLICATED HERE: F1-S2's QA already pinned the user-visible symptom of AC A
at Layer 2 — `backend/tests/api/f1s2/test_f1s2_memo_interaction.py::
test_a_reopened_workspace_derives_the_same_numbers_before_and_after_a_restart`,
`xfail(strict=True)`, measuring `effective_dom [150..155]` warm versus
`[180..185]` cold. That marker is **expected to XPASS-fail on the day this
story lands, by design**; removing it is part of landing F3-S4. Everything
below asserts the cause (what the manifest says) rather than the symptom (what
a derive computes), one layer lower, so the two files fail for different
reasons and neither hides the other.
"""

from __future__ import annotations

from datetime import timedelta

from f3s4_support import (
    FETCHABLE,
    SEARCH,
    TODAY,
    NoPurchase,
    Wire,
    one_record_per_query,
    planned,
    seed_fixtures,
    unseed_fixtures,
)

from rentcomp.client.pull import run_pull
from rentcomp.client.rentcast import fixture_signature
from rentcomp.storage.cache import read_manifest, write_manifest
from rentcomp.storage.config import Config
from rentcomp.storage.pulls import load_shaped_pull

A_MONTH_LATER = TODAY + timedelta(days=30)
TWO_MONTHS_LATER = TODAY + timedelta(days=60)


def _seed_one(fixtures, index: int, *, today) -> None:
    """Make exactly one window's saved response appear — the fixture-mode
    equivalent of the call that finally succeeds.

    `today` must be the date of the run that will fetch it: the planner's
    `daysOld` window slides with the calendar, so the same cohort year has a
    different wire signature (and therefore a different saved-response
    filename) a month later. The pull's own resume diff is keyed on the cohort
    year and status instead, which is why the resume still recognises the three
    windows it already holds.
    """
    from f3s4_support import body

    query = planned(today)[index]
    (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body(index))


# ===========================================================================
# ADDED AC A — as_of advances only when bytes actually arrive
# ===========================================================================


def test_a_retry_that_fetched_nothing_does_not_move_the_evidence_date(
    home, fixture_mode, no_sockets
) -> None:
    """The PM ruling, at the field it is about.

    A pull that holds 3 of 4 windows is retried a month later and the fourth
    still does not arrive. Nothing was observed on that day — no byte was
    written, no market was looked at — so the date this evidence was observed
    is unchanged. `as_of` is the pipeline's only "now" (owner ruling 1): every
    censored comp's `effective_dom` is measured from it, so moving it forward
    by 30 days adds a month of vacancy to comps nobody re-observed, and that
    lands in premium, in the buckets and in the KM curve.

    Deliberately the **resume** path rather than F1-S2's forced-refresh path:
    it is the ordinary case (a partial pull, retried, still partial) and it
    reaches the same restamp through a different branch.
    """
    seed_fixtures(fixture_mode, skip=(3,))
    first = run_pull(**SEARCH, today=TODAY)
    assert first.complete is False and first.calls_to_complete == 1, (
        f"the setup did not hold: {first.missing!r}"
    )
    assert read_manifest(first.pull_ref).as_of == TODAY

    retry = run_pull(**SEARCH, today=A_MONTH_LATER)

    assert retry.complete is False, "the setup did not hold: the retry found the fourth window"
    assert read_manifest(first.pull_ref).as_of == TODAY, (
        f"the manifest now says this evidence was observed on "
        f"{read_manifest(first.pull_ref).as_of}, but the retry a month later fetched "
        "nothing and wrote nothing. `as_of` means 'when this evidence was observed'; "
        "restamping it for a fetch that did not happen makes every censored comp's DOM "
        "30 days longer for a vacancy nobody watched."
    )


def test_as_of_advances_when_bytes_actually_arrive(home, fixture_mode, no_sockets) -> None:
    """The complement, so the test above cannot be satisfied by freezing
    `as_of` forever.

    A refresh that really does bring new responses is a new observation of the
    market, and the evidence date must follow it — otherwise every comp's DOM
    would be frozen at the first pull's date and a genuinely refreshed
    workspace would understate vacancy instead of overstating it.
    """
    seed_fixtures(fixture_mode)
    first = run_pull(**SEARCH, today=TODAY)
    assert read_manifest(first.pull_ref).as_of == TODAY

    seed_fixtures(fixture_mode, today=A_MONTH_LATER)
    refreshed = run_pull(**SEARCH, today=A_MONTH_LATER, force_refresh=True)

    assert refreshed.complete is True
    assert read_manifest(refreshed.pull_ref).as_of == A_MONTH_LATER, (
        "a refresh that fetched every window did not move the evidence date. The comps "
        "were re-observed on that day and their DOM-so-far has to be measured from it."
    )


def test_as_of_never_names_a_day_on_which_no_evidence_arrived(
    home, fixture_mode, no_sockets
) -> None:
    """The general form of the ruling, over a three-attempt history — and
    deliberately agnostic about the one question the ruling does not settle.

    Attempt 1 (day 0) buys 3 of 4. Attempt 2 (day 30) completes the fourth.
    Attempt 3 (day 60) is a forced refresh in which nothing arrives at all.

    After attempt 2 the entry holds evidence observed on two different days,
    and a single scalar `as_of` cannot be true of both. **This test does not
    adjudicate that** — it asserts only membership: `as_of` is one of the days
    on which something actually arrived. (Which one is a genuine semantic
    question, raised with the PM at handoff rather than decided here.)

    Attempt 3 is the load-bearing assertion, and it is unambiguous under either
    answer: day 60 delivered nothing, so day 60 is not an answer.
    """
    seed_fixtures(fixture_mode, skip=(3,))
    first = run_pull(**SEARCH, today=TODAY)
    assert first.complete is False

    _seed_one(fixture_mode, 3, today=A_MONTH_LATER)
    second = run_pull(**SEARCH, today=A_MONTH_LATER)
    assert second.complete is True, "the setup did not hold: the fourth window never arrived"

    after_completion = read_manifest(second.pull_ref).as_of
    assert after_completion in (TODAY, A_MONTH_LATER), (
        f"as_of is {after_completion}, which is neither of the two days this entry's "
        "evidence actually arrived on"
    )

    unseed_fixtures(fixture_mode)
    third = run_pull(**SEARCH, today=TWO_MONTHS_LATER, force_refresh=True)
    assert third.complete is False, "the setup did not hold: something arrived on day 60"

    assert read_manifest(second.pull_ref).as_of == after_completion, (
        f"a forced refresh on {TWO_MONTHS_LATER} that fetched nothing moved the evidence "
        f"date from {after_completion} to {read_manifest(second.pull_ref).as_of}. Spec §7: "
        "a failed refresh keeps the old cache untouched — and the date the old cache was "
        "observed is part of it."
    )


def test_a_recovery_from_a_lost_manifest_does_not_restamp_the_evidence(
    home, live_env, no_sockets
) -> None:
    """The same ruling where the prior date is hardest to keep: the manifest
    that held it is gone.

    A crash that loses `manifest.json` leaves the responses and their `meta`
    sidecars — which carry the day each call arrived — so the date is
    recoverable. What must not happen is the recovery quietly stamping *today*
    on evidence that arrived a month ago: that is the same 30-day DOM shift,
    reached through the repair path instead of the retry path.

    Asserted as an upper bound rather than an equality, so a developer who
    recovers the date from the sidecars and one who keeps a coarser record
    both pass, and only "stamp the day of the repair" fails.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=Wire(one_record_per_query).transport)
    assert outcome.complete is True
    (home / "cache" / outcome.pull_ref / "manifest.json").unlink()
    load_shaped_pull.cache_clear()

    repaired = run_pull(
        **SEARCH,
        today=A_MONTH_LATER,
        transport=NoPurchase("every response is still in raw/").transport,
    )

    assert read_manifest(repaired.pull_ref).as_of <= TODAY, (
        f"the repair stamped {read_manifest(repaired.pull_ref).as_of} on responses that "
        f"arrived on {TODAY}. Nothing was fetched on the day of the repair — the sidecars "
        "beside each raw response record when it really arrived."
    )


# ===========================================================================
# ADDED AC B — completing a partial pull is never served from a stale view
# ===========================================================================


def test_completing_a_partial_pull_is_never_served_from_a_stale_memo(
    home, fixture_mode, no_sockets
) -> None:
    """ADR-002 note 3, pinned as an outcome so either mechanism passes.

    ADR-001 dropped `pull_digest` from the record-shaping memo key on the
    premise "a refresh writes a new ref, it never rewrites one". F4-S6's
    partial-pull resume breaks that premise: completing a "2025 inactive
    missing" cohort appends evidence *under the existing ref*. If completion
    lands under the same ref and the memo ignores content, the memo returns the
    pre-completion comps — every statistic short by a cohort, with nothing on
    screen to say so.

    Free to satisfy either way: mint a new ref on completion, or key the memo
    on the evidence. What may not happen is a caller finishing a pull and still
    being shown the incomplete version of it.

    NOTE TO ANYONE EDITING THIS TEST: it must NOT call `cache_clear()` between
    the two loads. The staleness it exists to catch only exists while the memo
    is warm, and clearing it would make the test pass against the bug.
    """
    seed_fixtures(fixture_mode, skip=(3,))
    first = run_pull(**SEARCH, today=TODAY)
    before = load_shaped_pull(first.pull_ref, Config())
    assert len(before.comps) == FETCHABLE - 1, (
        f"the setup did not hold: a 3-of-4 pull shaped {len(before.comps)} comps"
    )

    _seed_one(fixture_mode, 3, today=A_MONTH_LATER)
    second = run_pull(**SEARCH, today=A_MONTH_LATER)
    assert second.complete is True

    after = load_shaped_pull(second.pull_ref, Config())

    assert len(after.comps) == FETCHABLE, (
        f"after the pull was completed the loader still sees {len(after.comps)} of "
        f"{FETCHABLE} windows' evidence. The record-shaping memo is serving the "
        "pre-completion set (ADR-002 note 3: restore the digest to the memo key, or mint a "
        "new ref on completion)."
    )
    assert after.as_of == read_manifest(second.pull_ref).as_of, (
        f"the loader reports as_of {after.as_of} while the manifest on disk says "
        f"{read_manifest(second.pull_ref).as_of}. What the pipeline computes DOM from and "
        "what the disk records must be the same date — a memo that holds one while the "
        "disk holds the other is the F1-S2 finding in a second place."
    )


def test_a_manifest_change_alone_is_not_hidden_by_the_memo(
    home, fixture_mode, no_sockets
) -> None:
    """The half `_evidence_version` does not cover — flagged at handoff as
    going one step past the literal ruling, for the PM to confirm or drop.

    `storage/pulls.py::_evidence_version` fingerprints
    `(name, size, mtime_ns)` over `cache/<key>/raw/` only. But the shaped
    result also depends on the **manifest**: `_load_cache_backed_pull` reads
    `as_of` and `window` from it and hands both to `shape_raw_pull`. So a
    manifest that changes while the raw bytes do not is invisible to the memo,
    and the process serves numbers derived from a date that is no longer on
    disk.

    Fixing the restamp (the PM's ruling) removes the way the *product* reaches
    this state today; it does not remove the state. A restored backup, a
    hand-edited manifest, or the next story that has a reason to rewrite one
    all reach it again, and the failure mode is silent by construction.
    """
    seed_fixtures(fixture_mode)
    outcome = run_pull(**SEARCH, today=TODAY)
    warm = load_shaped_pull(outcome.pull_ref, Config())
    assert warm.as_of == TODAY

    manifest = read_manifest(outcome.pull_ref)
    write_manifest(
        outcome.pull_ref,
        as_of=A_MONTH_LATER,
        window=manifest.window,
        planned=manifest.planned,
        fetchable=manifest.fetchable,
        queries=manifest.queries,
    )

    assert load_shaped_pull(outcome.pull_ref, Config()).as_of == A_MONTH_LATER, (
        "the manifest on disk says the evidence was observed on "
        f"{A_MONTH_LATER} and the loader still reports {TODAY}, because the memo key "
        "covers the raw responses and not the manifest they are shaped with. Same disk, "
        "two answers — and the one the user sees depends on how long the process has been "
        "running."
    )
