"""F3-S4 — developer-authored Layer 1, under the AC that QA's files leave open.

QA's `backend/tests/unit/f3s4/` owns the five AC clauses and the two ACs added
at dispatch, stated in calls-spent and comps-derived so this story's [DEFAULT]
file layout stays free to change. That is the right level for the acceptance
criteria and the wrong level for the pieces they are built out of: every
assertion there passes or fails for a dozen reasons at once, so a mutation to
any single helper is caught by six tests and diagnosed by none.

This file pins the parts, and deliberately pins things QA's files cannot:

* the **third state** F3-S4 introduces on a window (satisfied / owed /
  answered-but-unreadable) including how manifests written *before* it read
  back, which no end-to-end test can reach because no code path writes one;
* the line between "a response" and "a file" (`read_raw_records`) — the single
  place `pull_status`, the resume diff and the shaped loader all consult, so
  that "the pull says complete" and "the loader can derive it" cannot disagree;
* the **rule** the reconcile applies, including the direction QA's suite is
  silent on: disk never promotes a window the manifest recorded as *failed*;
* the writer's behaviour against debris (a directory standing where a response
  belongs), which is otherwise only observable as one parametrised crash shape.

MONEY (WORKFLOW.md §6): fixture mode, `RENTCOMP_LIVE` never set except by
`live_env` alongside a fake key and an `httpx.MockTransport` that answers
inside the process. The `no_network` autouse backstop in
`backend/tests/conftest.py` is asserted clean wherever a pull is run.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

import rentcomp
from rentcomp.client.pull import (
    _held_as_of,
    _page_sig,
    _read_manifest_or_none,
    _query_sig_of,
    pull_ref_for,
    pull_status,
    run_pull,
)
from rentcomp.client.rentcast import (
    RentCastClient,
    ResponseUnusableError,
    UpstreamError,
    fixture_signature,
)
from rentcomp.storage.cache import (
    CORRUPT_MANIFEST_ERRORS,
    CacheMissError,
    Manifest,
    QueryStatus,
    manifest_fingerprint,
    raw_response_paths,
    read_manifest,
    read_raw_meta,
    read_raw_records,
    write_manifest,
    write_raw_response,
)
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger
from rentcomp.storage.pulls import load_shaped_pull

# ===========================================================================
# 0. which tree is under test — checked at COLLECTION, before anything runs
# ===========================================================================
#
# `.venv/Lib/site-packages/*.pth` points at the MAIN checkout's `backend/src`,
# so a run from a git worktree without `PYTHONPATH` executes the worktree's
# TESTS against MAIN's SOURCE. It does not error and it does not skip — it
# answers confidently about the wrong code (WORKFLOW.md §2; on Windows the
# separator is `;`, not `:`). `test_f1s2_workspace_store_dev.py` asserts the
# same invariant as a test; this one refuses to *collect*, so a wrong-tree run
# cannot even produce a list of passing test names to misread.

_PACKAGE_ROOT = Path(rentcomp.__file__).resolve().parents[3]
_TESTS_ROOT = Path(__file__).resolve().parents[3]
if _PACKAGE_ROOT != _TESTS_ROOT:  # pragma: no cover - only on a misconfigured run
    raise RuntimeError(
        f"WRONG SOURCE TREE. These tests live under {_TESTS_ROOT} but `import rentcomp` "
        f"resolved to {_PACKAGE_ROOT}. Nothing below would be about this branch. Set "
        f"PYTHONPATH={_TESTS_ROOT / 'backend' / 'src'} (WORKFLOW.md §2 — on Windows the "
        "PYTHONPATH separator is ';', not ':')."
    )


TODAY = date(2026, 7, 27)
A_MONTH_LATER = TODAY + timedelta(days=30)

SEARCH: dict = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 2,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    load_shaped_pull.cache_clear()
    yield root
    load_shaped_pull.cache_clear()


@pytest.fixture
def fixture_mode(home, tmp_path, monkeypatch) -> Path:
    saved = tmp_path / "live-samples"
    saved.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(saved))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return saved


@pytest.fixture
def live_env(home, monkeypatch) -> Path:
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", "f3s4-dev-fake-key-not-a-real-credential")
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return home


def _record(n: int) -> dict:
    listed = TODAY - timedelta(days=120 + 30 * n)
    return {
        "id": f"f3s4-dev-{n}",
        "formattedAddress": f"{200 + n} N Parts St, Chicago, IL 60609",
        "addressLine1": f"{200 + n} N Parts St",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 1900 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + n,
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


def _answer_each(request: httpx.Request, n: int) -> httpx.Response:
    return httpx.Response(200, json=[_record(n)])


class _Wire:
    """A recording `httpx.MockTransport`. Answers in-process; never a socket."""

    def __init__(self, answer=_answer_each) -> None:
        self._answer = answer
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._answer(request, len(self.requests))


class _NoPurchase:
    """A transport with no answer in it: reaching for a call fails at the reach."""

    def __init__(self) -> None:
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(
            "a RentCast call was issued when the answer was already on disk. Every call is "
            "one of 50 the owner has this month (D24)."
        )


def _seed(directory: Path, *, today: date = TODAY, skip: tuple[int, ...] = ()) -> list:
    from rentcomp.client.planner import fetchable_queries, plan_pull_queries

    queries = fetchable_queries(plan_pull_queries(today, **SEARCH))
    for index, query in enumerate(queries):
        if index in skip:
            continue
        (directory / f"{fixture_signature(query.params)}.json").write_bytes(
            json.dumps([_record(index)]).encode("utf-8")
        )
    return queries


# ===========================================================================
# 1. the third state — satisfied / owed / answered-but-unreadable
# ===========================================================================
#
# `calls_to_complete` is what F3-S2's modal asks the user to consent to and
# what F4-S6 renders verbatim, so it may only ever count calls a resume would
# actually send. `missing` is a different question: what evidence this pull
# cannot produce. F3-S4 is the story where the two stop being the same number.


def test_a_window_is_owed_by_default_so_every_ordinary_status_is_unchanged() -> None:
    """The tri-state must not change what an ordinary window costs."""
    assert QueryStatus(label="2025 Active", sig="y2025-active", satisfied=True).is_owed is False
    assert (
        QueryStatus(label="2025 Active", sig="y2025-active", satisfied=False, error="503").is_owed
        is True
    )


def test_a_window_holding_an_unreadable_answer_is_missing_but_not_owed() -> None:
    """The state D24 exists for, as a property of the manifest.

    A 2xx that this parser cannot use was billed and is filed. It is missing —
    the pull genuinely cannot produce that window's evidence — and it is not
    owed, because another call returns the same bytes.
    """
    manifest = Manifest(
        as_of=TODAY,
        window=("01-01", "12-31"),
        planned=2,
        fetchable=2,
        queries=(
            QueryStatus(label="2025 Active", sig="y2025-active", satisfied=True),
            QueryStatus(
                label="2025 Inactive",
                sig="y2025-inactive",
                satisfied=False,
                owed=False,
                error="the response arrived and could not be read",
            ),
        ),
    )

    assert manifest.missing == ("2025 Inactive",)
    assert manifest.complete is False
    assert manifest.calls_to_complete == 0, (
        "the manifest quotes a call for a window whose response is already filed under this "
        "pull. The number the user consents to and the number a resume spends are one number."
    )


def test_a_manifest_written_before_this_story_still_costs_what_it_always_did(home) -> None:
    """Backward compatibility, at the only layer that can see it.

    Entries on the owner's disk were written without the `owed` key. Reading
    one as "not owed" would strand a genuinely missing window (the user is
    told 0 calls will finish a pull that never finishes); reading it as
    "owed" would be right. No end-to-end test can reach this, because no code
    path writes a legacy manifest any more.
    """
    key = "b" * 64
    (home / "cache" / key).mkdir(parents=True)
    (home / "cache" / key / "manifest.json").write_text(
        json.dumps(
            {
                "as_of": "2026-07-27",
                "window": ["01-01", "12-31"],
                "planned": 2,
                "fetchable": 2,
                "queries": [
                    {"label": "2025 Active", "sig": "y2025-active", "satisfied": True},
                    {
                        "label": "2025 Inactive",
                        "sig": "y2025-inactive",
                        "satisfied": False,
                        "error": "gone",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    manifest = read_manifest(key)

    assert manifest.missing == ("2025 Inactive",)
    assert manifest.calls_to_complete == 1, (
        "a pre-F3-S4 manifest has no `owed` key; its missing window must still read as owed, "
        "or a resume quotes 0 calls for a pull it can never finish"
    )


def test_the_owed_answer_survives_a_write_and_read_of_the_manifest(home) -> None:
    """The third state has to be durable, not just in-memory: the whole point
    is that a user who reopens the search tomorrow is not quoted a call for it.
    """
    key = "c" * 64
    write_manifest(
        key,
        as_of=TODAY,
        window=("01-01", "12-31"),
        planned=1,
        fetchable=1,
        queries=[
            QueryStatus(
                label="2025 Active", sig="y2025-active", satisfied=False, owed=False, error="poison"
            )
        ],
    )

    reread = read_manifest(key)

    assert reread.missing == ("2025 Active",)
    assert reread.calls_to_complete == 0


# ===========================================================================
# 2. what counts as a response — the one line three modules consult
# ===========================================================================


@pytest.mark.parametrize(
    ("name", "body", "expected"),
    [
        ("a normal answer", b'[{"id": "a"}]', [{"id": "a"}]),
        # A real, paid-for "no comps in this window". NOT the same fact as a
        # missing response, and the two must never collapse into each other.
        ("an empty market", b"[]", []),
        ("the documented envelope", b'{"listings": [{"id": "a"}]}', [{"id": "a"}]),
        ("truncated mid-json", b'[{"id": "a"', None),
        ("emptied by a crash", b"", None),
        ("not json at all", b"<html>gateway timeout</html>", None),
        ("a 2xx of the wrong shape", b'{"listings": "not a list"}', None),
        ("json, but not records", b"null", None),
    ],
)
def test_only_a_recognisable_records_body_reads_back_as_evidence(
    tmp_path, name, body, expected
) -> None:
    """`read_raw_records` is where "present" stops meaning "usable".

    `pull_status`, the resume diff and the shaped loader all ask this one
    function, which is what makes "the pull says complete" and "the loader can
    derive it" the same claim rather than two that drift apart.
    """
    path = tmp_path / "y2025-active-off000.json"
    path.write_bytes(body)

    assert read_raw_records(path) == expected, name


def test_a_directory_where_a_response_belongs_is_not_a_response(tmp_path) -> None:
    """A crash shape that raises rather than returning falsy, on both platforms
    (`PermissionError` on Windows, `IsADirectoryError` on POSIX)."""
    path = tmp_path / "y2025-active-off000.json"
    path.mkdir()

    assert read_raw_records(path) is None


def test_a_missing_response_is_not_a_response(tmp_path) -> None:
    assert read_raw_records(tmp_path / "never-written.json") is None


# ===========================================================================
# 3. the writer, against debris
# ===========================================================================


def test_a_response_lands_even_when_debris_stands_in_its_place(home) -> None:
    """Bytes that were paid for must reach disk over an empty directory.

    A directory at a response path is a crash artefact, a half-finished sync or
    a file manager — never anything this writer produced. Left in place it
    makes the rename raise and the paid-for response is lost, which is the one
    outcome D24 exists to prevent.
    """
    key = "d" * 64
    (home / "cache" / key / "raw").mkdir(parents=True)
    (home / "cache" / key / "raw" / "y2025-active-off000.json").mkdir()

    written = write_raw_response(key, "y2025-active-off000", b'[{"id": "a"}]', meta={"sig": "x"})

    assert written is not None
    assert read_raw_records(written) == [{"id": "a"}]


def test_a_non_empty_directory_is_never_deleted_to_make_room(home) -> None:
    """The other half: the writer clears debris, it does not clear data.

    An empty directory cannot be anyone's data; a non-empty one is something
    this code has no business removing, so the write fails loudly instead —
    and leaves no half-written temp file behind.
    """
    key = "e" * 64
    obstruction = home / "cache" / key / "raw" / "y2025-active-off000.json"
    obstruction.mkdir(parents=True)
    (obstruction / "someones-file.txt").write_text("do not delete me", encoding="utf-8")

    with pytest.raises(OSError):
        write_raw_response(key, "y2025-active-off000", b'[{"id": "a"}]', meta={"sig": "x"})

    assert (obstruction / "someones-file.txt").read_text(encoding="utf-8") == "do not delete me"
    assert not list(obstruction.parent.glob(".*.tmp")), (
        "a failed write left its temp file behind, where the next crash-shape scan has to "
        "decide all over again whether it is evidence"
    )


def test_a_pull_survives_one_window_whose_bytes_cannot_be_stored(live_env, no_network) -> None:
    """A storage failure on one window must not unwind the whole pull.

    The three windows already filed were paid for; raising here would return
    nothing and leave the caller with an exception where §5a asks for a named
    gap. Reproduced with a non-empty directory, which the writer refuses by
    design (above).
    """
    ref = pull_ref_for(**SEARCH)
    obstruction = Path(os.environ["RENTCOMP_HOME"]) / "cache" / ref / "raw"
    obstruction.mkdir(parents=True)

    from rentcomp.client.planner import fetchable_queries, plan_pull_queries

    first_sig = "y%d-%s" % (
        (queries := fetchable_queries(plan_pull_queries(TODAY, **SEARCH)))[0].year,
        queries[0].status.lower(),
    )
    blocked = obstruction / f"{_page_sig(first_sig, 0)}.json"
    blocked.mkdir()
    (blocked / "not-ours.txt").write_text("x", encoding="utf-8")

    outcome = run_pull(**SEARCH, today=TODAY, transport=_Wire().transport)

    assert outcome.complete is False
    assert outcome.missing, "the window whose bytes could not be stored was not named"
    readable = [
        path
        for path in raw_response_paths(outcome.pull_ref)
        if read_raw_records(path) is not None
    ]
    assert len(readable) == len(queries) - 1, (
        "a storage failure on one window threw away the responses that did land"
    )
    assert no_network == []


# ===========================================================================
# 4. the reconcile rule — including the direction QA's suite is silent on
# ===========================================================================


def test_bytes_on_disk_never_promote_a_window_the_manifest_recorded_as_failed(
    live_env, no_network
) -> None:
    """The asymmetry, stated on its own.

    Disk demotes a claimed satisfaction it cannot back, and disk promotes a
    window the index is *silent* about. It must not promote a recorded
    failure: a truncated query has a page filed under its signature and is
    still genuinely short of evidence, so "there are bytes here" would close a
    gap that is open and the missing page would never be bought.

    (Page-level resume itself is out of scope by PM ruling — this test pins
    only that the gap stays open and priced, not how it is finished.)
    """
    ref = pull_ref_for(**SEARCH)

    def truncated_then_fine(request: httpx.Request, n: int) -> httpx.Response:
        if n == 1:
            return httpx.Response(
                200, json=[_record(1), _record(2)], headers={"X-Total-Count": "3"}
            )
        if n == 2:
            return httpx.Response(503, json={"error": "gone"})
        return _answer_each(request, n + 10)

    first = run_pull(**SEARCH, today=TODAY, transport=_Wire(truncated_then_fine).transport)
    assert first.complete is False, "the setup did not hold: the truncated window completed"
    assert raw_response_paths(ref), "the setup did not hold: page 0 was not filed"

    reread = pull_status(ref)

    assert reread.complete is False
    assert reread.calls_to_complete == first.calls_to_complete >= 1, (
        "a window with a page on disk and a page missing was read back as satisfied. The "
        "bytes corroborate a claim of satisfaction; they do not manufacture one."
    )
    assert no_network == []


def test_an_unreadable_source_with_nothing_filed_leaves_the_window_owed(
    fixture_mode, no_network
) -> None:
    """The guard on the not-owed answer: **bytes on disk, or it is still owed.**

    Fixture mode reaches the same "this body is unusable" error through a code
    path where the sink never fired, so nothing was written. Reading that as
    "we hold an answer we cannot use" would make the window permanently
    unbuyable — not missing-and-priced, but missing and quoted at zero calls,
    which no amount of retrying can ever resolve. The not-owed answer is only
    ever available *because* the bytes are safe (D24).

    Found by mutation: removing this guard survived both QA's suite and every
    other test in this file.
    """
    queries = _seed(fixture_mode)
    corrupt = fixture_mode / f"{fixture_signature(queries[0].params)}.json"
    corrupt.write_bytes(b'[{"id": "truncated"')

    outcome = run_pull(**SEARCH, today=TODAY)

    assert outcome.complete is False
    assert outcome.calls_to_complete == 1, (
        "a window whose source could not be read, and whose bytes were never filed, was "
        "quoted at 0 calls to complete. Nothing on disk can answer it, so it is owed."
    )
    assert not [
        path
        for path in raw_response_paths(outcome.pull_ref)
        if _query_sig_of(path.stem) == f"y{queries[0].year}-{queries[0].status.lower()}"
    ], "an unusable body with nothing behind it filed something anyway"
    assert no_network == []


def test_an_attempt_that_delivered_nothing_at_all_leaves_the_evidence_date_alone(
    fixture_mode, no_network
) -> None:
    """The ruling's negative case with the arrival signal isolated.

    Every window fails, so no byte is written and no response is filed. If the
    manifest is stamped with the day of the attempt anyway, every censored
    comp's `effective_dom` grows by the gap for a market nobody looked at.
    """
    _seed(fixture_mode)
    first = run_pull(**SEARCH, today=TODAY)
    assert first.complete is True, "the setup did not hold"

    for saved in fixture_mode.glob("*.json"):
        saved.unlink()
    retry = run_pull(**SEARCH, today=A_MONTH_LATER, force_refresh=True)

    assert retry.complete is False, "the setup did not hold: something arrived"
    assert read_manifest(first.pull_ref).as_of == TODAY
    assert no_network == []


def test_the_evidence_date_on_disk_is_carried_forward_and_not_re_derived(home) -> None:
    """`_held_as_of` reads the manifest when there is one.

    Otherwise a run that owes a window starts from its own calendar, and the
    first response to arrive is irrelevant — the date would already have moved.
    """
    key = "7" * 64
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))

    assert _held_as_of(key, read_manifest(key), A_MONTH_LATER) == TODAY


@pytest.mark.parametrize(
    "error_type", (CacheMissError, *CORRUPT_MANIFEST_ERRORS), ids=lambda t: t.__name__
)
def test_no_type_of_unreadable_manifest_reaches_the_resume_diff(monkeypatch, error_type) -> None:
    """The shared tuple, parametrised, so a tenth shape is covered by adding it
    to `storage/cache.py` and nothing else.

    This is the sixth call site in that family on this project, and the
    previous five drifted apart one `except` clause at a time — `AttributeError`
    (a `queries` list of non-records) and `TypeError` (a `window` that is not a
    sequence) walked straight out of `run_pull` and reached every caller as an
    anonymous 500. Driven off `CORRUPT_MANIFEST_ERRORS` itself rather than off
    a list of corrupt manifest bodies: the claim is about the error types the
    shared name promises to cover, and a body that stopped raising one of them
    would silently stop testing it.

    Asserted on `_read_manifest_or_none` rather than on `run_pull`, because
    `pull_status` reads the manifest too and is *entitled* to raise there —
    `api/search.py` refuses a corrupt entry loudly rather than re-pulling
    around it (F2-S1), and this test must not quietly demand the opposite.
    """
    monkeypatch.setattr(
        "rentcomp.client.pull.read_manifest",
        lambda key: (_ for _ in ()).throw(error_type("simulated unreadable manifest")),
    )

    assert _read_manifest_or_none("5" * 64) is None


def test_a_lost_manifest_is_rebuilt_from_the_store_rather_than_re_bought(
    live_env, no_network, home
) -> None:
    """The repair, from the outside: the index is derived data, the bytes are not.

    Asserted through `pull_status` (which knows nothing about the plan) as well
    as through `run_pull`, so the rebuilt manifest is real on disk rather than
    an in-memory reconstruction that vanishes with the process.
    """
    first = run_pull(**SEARCH, today=TODAY, transport=_Wire().transport)
    assert first.complete is True
    (home / "cache" / first.pull_ref / "manifest.json").unlink()
    load_shaped_pull.cache_clear()

    repaired = run_pull(**SEARCH, today=TODAY, transport=_NoPurchase().transport)

    assert repaired.complete is True and repaired.calls_spent == 0
    assert pull_status(first.pull_ref).complete is True, (
        "the rebuilt manifest did not reach disk, so the next process repeats the repair"
    )
    assert no_network == []


def test_a_status_read_never_writes_anything(live_env, no_network, home) -> None:
    """`pull_status` corroborates against the store; it must not *edit* it.

    It is called on every render of the Home screen and on every search that
    hits the cache. A read that repaired the manifest as a side effect would
    make two identical reads distinguishable, and would put a disk write on
    the path whose whole job is to be free.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=_Wire().transport)
    entry = home / "cache" / outcome.pull_ref
    raw_response_paths(outcome.pull_ref)[0].unlink()
    before = {
        str(p.relative_to(entry)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(entry.rglob("*"))
        if p.is_file()
    }

    status = pull_status(outcome.pull_ref)

    assert status.complete is False, "the demotion this test is about did not happen"
    after = {
        str(p.relative_to(entry)): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(entry.rglob("*"))
        if p.is_file()
    }
    assert after == before, "reading a pull's status rewrote the cache entry"
    assert no_network == []


# ===========================================================================
# 5. as_of — the pieces the ruling is built from
# ===========================================================================


def test_the_evidence_date_is_recovered_from_the_sidecars_when_the_manifest_is_gone(
    home,
) -> None:
    """`_held_as_of`, directly, because the pull path can only show the clamp.

    The sidecar beside each response records the day that response arrived, so
    a repair has a source for `as_of` other than its own calendar. Without it
    the repair adds 30 days of vacancy to every censored comp — the same
    defect as the restamp, reached through a different door.
    """
    key = "f" * 64
    write_raw_response(
        key, "y2025-active-off000", b"[]", meta={"sig": "y2025-active", "as_of": "2026-07-27"}
    )
    write_raw_response(
        key, "y2025-inactive-off000", b"[]", meta={"sig": "y2025-inactive", "as_of": "2026-06-01"}
    )

    assert _held_as_of(key, None, A_MONTH_LATER) == TODAY, (
        "the recovered date must be the LATEST day evidence arrived — the entry has been "
        "observed at least that recently"
    )


def test_a_recovered_evidence_date_is_never_later_than_the_run_that_recovered_it(
    home,
) -> None:
    """The clamp, and the reason it exists.

    Sidecars written before this story carry only `fetched_at`, which the
    client stamps from the wall clock rather than from the pull's `today`. A
    pull cannot have observed the market after the date it was given, so the
    fallback is bounded rather than trusted.
    """
    key = "a" * 64
    write_raw_response(
        key, "y2025-active-off000", b"[]", meta={"sig": "y2025-active", "fetched_at": "2099-01-01"}
    )

    assert _held_as_of(key, None, TODAY) == TODAY


def test_an_entry_with_no_sidecars_at_all_dates_to_the_run_that_found_it(home) -> None:
    """Nothing to recover from is not an error: it is a first pull."""
    assert _held_as_of("0" * 64, None, TODAY) == TODAY


def test_a_response_records_the_pulls_own_date_not_the_wall_clock(
    live_env, no_network, home
) -> None:
    """`today` arrives as an argument, and the sidecar has to agree with it.

    The client stamps `fetched_at` from `date.today()`; this pull's "now" is
    the `today` it was handed, and that is what `as_of` is rebuilt from. A
    sidecar that recorded only the wall clock would make a frozen-clock pull
    unrecoverable and would drift from the manifest beside it.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=_Wire().transport)

    sidecars = [read_raw_meta(path) for path in raw_response_paths(outcome.pull_ref)]
    assert sidecars, "no sidecars were filed beside the responses"
    assert {meta.get("as_of") for meta in sidecars} == {TODAY.isoformat()}
    assert read_manifest(outcome.pull_ref).as_of == TODAY
    assert no_network == []


# ===========================================================================
# 6. the memo's manifest half
# ===========================================================================


def test_the_manifest_fingerprint_moves_with_content_and_not_with_the_clock(home) -> None:
    """Content, not `(size, mtime_ns)` — the reason stated as a test.

    `as_of` moving from one ISO date to another leaves the file byte-length
    identical, so a size+mtime fingerprint would rest entirely on the clock's
    resolution. Two writes in the same tick have to be distinguishable.
    """
    key = "9" * 64
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))
    first = manifest_fingerprint(key)

    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))
    assert manifest_fingerprint(key) == first, "an identical rewrite changed the fingerprint"

    write_manifest(key, as_of=A_MONTH_LATER, window=("01-01", "12-31"))
    assert manifest_fingerprint(key) != first, (
        "the evidence date changed and the fingerprint did not, so a warm process keeps "
        "deriving DOM from a date that is no longer on disk"
    )


def test_a_ref_with_no_manifest_fingerprints_as_nothing() -> None:
    assert manifest_fingerprint("8" * 64) == ""
    assert manifest_fingerprint("../escape") == ""


def test_the_memo_key_carries_the_manifest_and_not_only_the_raw_files(home) -> None:
    """The half `_evidence_version` used to miss, asserted at the key itself.

    `_load_cache_backed_pull` hands the manifest's `as_of` and `window`
    straight to `shape_raw_pull`, so they are inputs to the shaped result every
    bit as much as the responses are. A key that covered only `raw/` served
    numbers derived from a date no longer on disk, for as long as the process
    kept running.
    """
    from rentcomp.storage.pulls import _evidence_version

    key = "6" * 64
    write_raw_response(key, "y2025-active-off000", b"[]", meta={"sig": "y2025-active"})
    write_manifest(key, as_of=TODAY, window=("01-01", "12-31"))
    before = _evidence_version(key)

    write_manifest(key, as_of=A_MONTH_LATER, window=("01-01", "12-31"))

    assert _evidence_version(key) != before, (
        "the manifest changed and the memo key did not, so a warm process keeps serving the "
        "old evidence date"
    )


# ===========================================================================
# 7. the naming convention, both ways
# ===========================================================================


@pytest.mark.parametrize("sig", ["y2025-active", "y2025-inactive", "y2026-active"])
@pytest.mark.parametrize("offset", [0, 500, 1000])
def test_a_page_filename_names_the_query_it_belongs_to(sig, offset) -> None:
    """The resume diff maps files back to windows through this pair. A scheme
    whose halves disagreed would silently hold nothing back and re-buy the
    lot."""
    assert _query_sig_of(_page_sig(sig, offset)) == sig


def test_a_filename_from_another_writer_maps_to_itself_and_holds_nothing_back() -> None:
    """`cache/<key>/raw/` also holds responses filed by earlier stories under
    their own signatures. Those must not accidentally match a planned window."""
    assert _query_sig_of("fe9de5158f036802") == "fe9de5158f036802"


# ===========================================================================
# 8. the typed unusable-2xx error
# ===========================================================================


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("not json", b"<html>504 Gateway Time-out</html>"),
        ("a scalar", b"42"),
        ("an envelope whose listings are not a list", b'{"listings": "nope"}'),
    ],
)
def test_a_2xx_this_parser_cannot_read_raises_its_own_type(live_env, no_network, name, body) -> None:
    """The distinction the resume is judged on, at the client boundary.

    Every other `RentCastError` means a call delivered nothing and a retry
    might. This one means the call succeeded, the money is gone and the bytes
    are filed — so it must be separable by type rather than by parsing a
    message.
    """
    client = RentCastClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body)))

    with pytest.raises(ResponseUnusableError):
        client.fetch_listings({"city": "Chicago", "limit": 500})
    assert no_network == []


def test_the_new_type_is_still_an_upstream_error_so_existing_callers_are_unaffected() -> None:
    """Additive, deliberately: `api/search.py` and F0-S4's own tests catch
    `UpstreamError`/`RentCastError`, and widening the taxonomy must not quietly
    change what any of them handle."""
    assert issubclass(ResponseUnusableError, UpstreamError)


def test_a_call_that_never_delivered_is_not_the_unusable_type(live_env, no_network) -> None:
    """The complement, so the type cannot be satisfied by making everything it."""
    client = RentCastClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, json={"error": "gone"}))
    )

    with pytest.raises(UpstreamError) as raised:
        client.fetch_listings({"city": "Chicago", "limit": 500})
    assert not isinstance(raised.value, ResponseUnusableError), (
        "a 503 delivered nothing and a retry might. Reading it as 'we hold an unusable "
        "answer' would leave the window unbought forever."
    )
    assert no_network == []


# ===========================================================================
# 9. the shaped loader, over a store that is not perfect
# ===========================================================================


def test_a_pull_derives_the_evidence_it_has_when_one_response_is_unreadable(
    live_env, no_network
) -> None:
    """The loader must skip what it cannot read, not choke on it and not
    invent records out of it.

    The unusable window is named by `pull_status` (it is `missing`), so the
    user is not shown a whole set — but the three windows that did arrive are
    real, paid-for evidence and §5a says a partial pull is usable.
    """

    def poison_the_third(request: httpx.Request, n: int) -> httpx.Response:
        if n == 3:
            return httpx.Response(200, content=b'{"listings": "this is not a list"}')
        return _answer_each(request, n)

    outcome = run_pull(**SEARCH, today=TODAY, transport=_Wire(poison_the_third).transport)

    assert outcome.complete is False and outcome.calls_to_complete == 0
    shaped = load_shaped_pull(outcome.pull_ref, Config())
    assert len(shaped.comps) == outcome.fetchable - 1, (
        "the unreadable response either crashed the loader or was counted as evidence"
    )
    assert no_network == []


def test_an_empty_window_is_evidence_and_not_a_gap(fixture_mode, no_network) -> None:
    """`b"[]"` is a real, paid-for answer: this market had no comps in that
    window. It must satisfy the window (nothing is owed) even though it
    contributes no comps — the opposite reading buys the same empty answer
    every run, forever.
    """
    queries = _seed(fixture_mode)
    for saved in fixture_mode.glob("*.json"):
        saved.write_bytes(b"[]")

    first = run_pull(**SEARCH, today=TODAY)

    assert first.complete is True and first.calls_to_complete == 0
    assert len(raw_response_paths(first.pull_ref)) == len(queries)
    assert load_ledger().calls_this_month == 0
    assert no_network == []
