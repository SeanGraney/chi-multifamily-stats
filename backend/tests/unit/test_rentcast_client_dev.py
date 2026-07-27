"""F0-S4 — developer-authored Layer 1 tests (AGENT_DEVELOPER.md step 4).

QA owns the acceptance criteria (`test_rentcast_client.py`,
`test_live_call_guard.py`, `tests/api/test_rentcast_client_surface.py`). This
file covers what the *implementation* made newly possible to get wrong — the
edges of decisions QA's tests do not reach into:

* **The ledger's month rule** (PM ruling, F0-S4). QA pins the headline case
  ("a past month starts from zero"); the rule has four other branches and the
  dangerous one is the migration: an absent month must read as the CURRENT
  month, never as "unknown, therefore zero". Getting that backwards silently
  hands back 8 calls that were already paid for.
* **Flag parsing**, in the affirmative direction QA deliberately left open.
* **The sink's exact contract** — what it does NOT see, which is the half a
  "did the sink fire" test cannot express.
* Pure-function edges in `client/query.py` and `storage/secrets.py`.

Zero network, like everything else in this suite: nothing here sets
`RENTCOMP_LIVE=1` at all (the live path is exercised through
`httpx.MockTransport` in `tests/api/test_rentcast_client_surface_dev.py`), and
`tests/conftest.py`'s autouse kill switch sits under it regardless.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from rentcomp.client.query import (
    MAX_LIMIT,
    build_listing_params,
    rentcast_multi,
    rentcast_range,
)
from rentcomp.client.rentcast import (
    FIXTURES_DIR_ENV,
    LIVE_MODE_ENV,
    FixtureMissingError,
    LiveModeDisabledError,
    Mode,
    RentCastClient,
    RentCastError,
    UpstreamError,
    fixture_signature,
    fixtures_dir,
    resolve_mode,
)
from rentcomp.storage.ledger import (
    MONTHLY_CALL_CAP,
    CallRecord,
    Ledger,
    LedgerError,
    current_month,
    ledger_path,
    load_ledger,
    save_ledger,
)
from rentcomp.storage.secrets import load_api_key, save_api_key, secrets_path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_SAMPLES = REPO_ROOT / "fixtures" / "live-samples"

FAKE_KEY = "dev-fake-key-not-a-real-credential"

#: The gate's real broad Active query — 39 of 39 records, one committed file.
REAL_QUERY = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "propertyType": "Multi-Family|Apartment|Townhouse|Single Family|Condo",
    "status": "Active",
    "daysOld": "1:1095",
    "limit": 500,
    "includeTotalCount": "true",
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """A temp storage root, no flag, no key — the default state of every run."""
    root = tmp_path / "home"
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    monkeypatch.delenv(LIVE_MODE_ENV, raising=False)
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.delenv(FIXTURES_DIR_ENV, raising=False)
    return root


def month_offset(months: int) -> str:
    """`current_month()` shifted by whole months, as `YYYY-MM`."""
    year, month = (int(part) for part in current_month().split("-"))
    index = (year * 12 + (month - 1)) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


# ---------------------------------------------------------------------------
# client/query.py — the syntax that spends money
# ---------------------------------------------------------------------------


def test_a_zero_bound_is_a_bound_not_an_absent_one() -> None:
    """`0` is falsy. A range builder written with truthiness instead of an
    explicit `is None` would turn `daysOld=0:30` into `*:30` — a silently
    unbounded lower end on a query that costs a call."""
    assert rentcast_range(0, 30) == "0:30"
    assert rentcast_range(30, 0) == "30:0"
    assert rentcast_range(0, None) == "0:*"
    assert rentcast_range(None, 0) == "*:0"


def test_a_single_value_multi_is_not_pipe_wrapped() -> None:
    """One property type must go on the wire bare, exactly as the gate sent it
    for its single-type runs — `|Apartment|` would match nothing."""
    assert rentcast_multi(["Apartment"]) == "Apartment"
    assert rentcast_multi(iter(["A", "B"])) == "A|B", "an iterator must work, not just a list"


def test_an_empty_property_type_list_is_refused_by_the_builder() -> None:
    """A pull with no property types is not a narrower pull, it is a broken
    param — and `rentcast_multi`'s guard has to survive being called through
    the builder, which is the only way the product reaches it."""
    with pytest.raises(ValueError):
        build_listing_params(
            address="a", radius=1.0, bedrooms="3", property_types=[],
            status="Active", days_old="1:30",
        )


def test_offset_zero_hashes_to_the_same_query_as_no_offset() -> None:
    """Page 0 and "the whole thing" are the same call. If `offset=0` produced a
    key, every committed fixture would be unreachable from the paging path."""
    plain = build_listing_params(
        address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
        status="Active", days_old="1:30",
    )
    page_zero = build_listing_params(
        address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
        status="Active", days_old="1:30", offset=0,
    )
    assert plain == page_zero
    assert fixture_signature(plain) == fixture_signature(page_zero)
    assert plain["limit"] == MAX_LIMIT


def test_the_builder_keeps_an_explicit_bathrooms_filter_verbatim() -> None:
    """Bare means exact match, colon syntax means a range — only the caller
    knows which it wanted, so the builder must not "helpfully" normalize. The
    gate's 1.5:2 runs are committed fixtures keyed on the exact string."""
    params = build_listing_params(
        address="a", radius=1.0, bedrooms="4", bathrooms="1.5:2",
        property_types=["Apartment"], status="Active", days_old="1:88",
    )
    assert params["bathrooms"] == "1.5:2"


def test_include_total_count_is_the_string_true_not_a_bool() -> None:
    """A bool re-keys the signature (`true` vs `"true"` in canonical JSON) and
    orphans all 8 committed fixtures. Pinned as a *type* assertion because
    `str(True).lower() == "true"` would let a bool sneak past a value check."""
    params = build_listing_params(
        address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
        status="Active", days_old="1:30",
    )
    assert params["includeTotalCount"] is not True
    assert isinstance(params["includeTotalCount"], str)
    assert isinstance(params["limit"], int) and params["limit"] is not True


def test_the_signature_is_order_independent_and_value_dependent() -> None:
    """Canonical JSON with sorted keys: the same query built in a different key
    order is the same call, and a one-character difference is a different one."""
    a = dict(REAL_QUERY)
    b = {key: REAL_QUERY[key] for key in reversed(list(REAL_QUERY))}
    assert fixture_signature(a) == fixture_signature(b)
    assert fixture_signature({**a, "radius": 2.5}) != fixture_signature(a)
    assert fixture_signature(a) == "fe9de5158f036802", (
        "the signature no longer matches the gate's committed name for this exact query"
    )


# ---------------------------------------------------------------------------
# mode resolution — D17's decision, and its edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", " 1", "1 ", "\t1\n"])
def test_only_the_literal_one_enables_live_even_padded(home, monkeypatch, value: str) -> None:
    """Surrounding whitespace is stripped — `RENTCOMP_LIVE="1 "` out of a shell
    profile is unambiguously someone asking for live mode."""
    monkeypatch.setenv(LIVE_MODE_ENV, value)
    assert resolve_mode() is Mode.LIVE


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "on", "y", "11", "1x", "01", "-1"])
def test_affirmative_looking_spellings_do_not_enable_live(
    home, monkeypatch, value: str
) -> None:
    """QA deliberately left this open ("whether the flag parser accepts extra
    affirmative spellings is the developer's call"). The call: it does not.

    D17 spells the flag `RENTCOMP_LIVE=1`. One spelling is auditable by reading
    a single line; a vocabulary of accepted truthy words is a thing you have to
    re-derive every time you review whether a run could have cost money — and
    `01` / `11` / `1x` show how close a typo can sit to the enabling value.
    """
    monkeypatch.setenv(LIVE_MODE_ENV, value)
    assert resolve_mode() is Mode.FIXTURE, f"{LIVE_MODE_ENV}={value!r} was read as live"


def test_the_mode_is_not_snapshotted_when_the_client_is_constructed(
    home, monkeypatch
) -> None:
    """A client built while the flag happened to be set must not stay live
    after it is unset. Constructing is free; every money decision is made in
    `fetch_listings`."""
    monkeypatch.setenv(LIVE_MODE_ENV, "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    client = RentCastClient()
    monkeypatch.delenv(LIVE_MODE_ENV)

    result = client.fetch_listings(dict(REAL_QUERY))
    assert result.calls_spent == 0, "a client constructed under the flag stayed live"


def test_explicit_fixture_mode_is_honoured_even_with_the_flag_and_a_key(
    home, monkeypatch
) -> None:
    """The override has to work in the *safe* direction unconditionally —
    that is what lets a future caller (a Playwright harness, a dry run) opt out
    of spending on a machine that is otherwise configured to spend."""
    monkeypatch.setenv(LIVE_MODE_ENV, "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)

    result = RentCastClient(mode=Mode.FIXTURE).fetch_listings(dict(REAL_QUERY))
    assert result.calls_spent == 0
    assert result.fetched == 39
    assert not ledger_path().exists(), "explicit fixture mode wrote a ledger entry"


def test_a_string_mode_is_accepted_and_still_guarded(home) -> None:
    """`RentCastClient("live")` must be refused the same way `Mode.LIVE` is —
    otherwise the string spelling is a hole in D17."""
    assert RentCastClient("fixture")._effective_mode() is Mode.FIXTURE
    with pytest.raises(LiveModeDisabledError):
        RentCastClient("live").fetch_listings(dict(REAL_QUERY))


def test_an_unknown_mode_name_is_rejected_at_construction(home) -> None:
    """A typo'd mode must not fall through to "whatever the environment says"."""
    with pytest.raises(ValueError):
        RentCastClient("liv")


# ---------------------------------------------------------------------------
# fixture mode
# ---------------------------------------------------------------------------


def test_the_fixture_miss_error_names_the_signature_and_the_directory(home) -> None:
    """The developer reading this error has to be able to go make the fixture.
    "Not found" without the name it looked for is a scavenger hunt."""
    params = {**REAL_QUERY, "daysOld": "1:2"}
    sig = fixture_signature(params)
    with pytest.raises(FixtureMissingError) as excinfo:
        RentCastClient().fetch_listings(params)
    message = str(excinfo.value)
    assert sig in message
    assert str(fixtures_dir()) in message


def test_the_constructor_argument_beats_the_environment_override(
    home, monkeypatch, tmp_path
) -> None:
    """Two ways to relocate the dataset, and the explicit one wins — the
    argument is what a test or an E2E harness passes deliberately, the env var
    is ambient."""
    from_env = tmp_path / "from-env"
    from_arg = tmp_path / "from-arg"
    from_env.mkdir()
    from_arg.mkdir()
    sig = fixture_signature(REAL_QUERY)
    (from_env / f"{sig}.json").write_bytes(b'[{"id": "env"}]')
    (from_arg / f"{sig}.json").write_bytes(b'[{"id": "arg"}]')
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(from_env))

    result = RentCastClient(fixtures_dir=from_arg).fetch_listings(dict(REAL_QUERY))
    assert [record["id"] for record in result.records] == ["arg"]


def test_a_corrupt_fixture_is_an_error_not_an_empty_market(home, tmp_path) -> None:
    """The same distinction `FixtureMissingError` protects, one step further
    in: a fixture that will not parse must never degrade to `[]`, which reads
    downstream as "no comps in radius"."""
    root = tmp_path / "broken"
    root.mkdir()
    (root / f"{fixture_signature(REAL_QUERY)}.json").write_bytes(b"{not json")

    with pytest.raises(RentCastError) as excinfo:
        RentCastClient(fixtures_dir=root).fetch_listings(dict(REAL_QUERY))
    assert isinstance(excinfo.value, UpstreamError)


def test_a_fixture_without_a_sidecar_entry_reports_itself_whole(home, tmp_path) -> None:
    """Synthetic fixtures (F3+, and the Playwright dataset) have no gate ledger
    beside them. With nothing claiming a larger total, what is on disk IS the
    answer — reporting them permanently incomplete would make every synthetic
    test develop against a fake gap."""
    root = tmp_path / "synthetic"
    root.mkdir()
    (root / f"{fixture_signature(REAL_QUERY)}.json").write_bytes(b'[{"id": "a"}, {"id": "b"}]')

    result = RentCastClient(fixtures_dir=root).fetch_listings(dict(REAL_QUERY))
    assert result.total_count == 2 and result.fetched == 2
    assert result.complete is True and result.missing == 0


def test_the_truncated_fixture_keeps_its_bytes_and_its_gap_together(home) -> None:
    """The committed Inactive pull is the one place both halves are true at
    once: 500 real records on disk, 690 reported by the header. The page's raw
    bytes must be the file, and the result must still say 190 are missing."""
    params = {**REAL_QUERY, "status": "Inactive"}
    saved = (LIVE_SAMPLES / f"{fixture_signature(params)}.json").read_bytes()

    result = RentCastClient().fetch_listings(params)
    assert result.pages[0].raw == saved
    assert result.pages[0].total_count == 690
    assert result.fetched == 500 and result.missing == 190 and result.complete is False


# ---------------------------------------------------------------------------
# storage/secrets.py (D16)
# ---------------------------------------------------------------------------


def test_saving_twice_leaves_one_key_and_still_0600(home) -> None:
    """A re-entered key must replace, not append — and the second write must
    not pick up the umask on the way through the temp file."""
    save_api_key("first-key")
    save_api_key(FAKE_KEY)
    assert load_api_key() == FAKE_KEY
    assert stat.S_IMODE(secrets_path().stat().st_mode) == 0o600
    assert json.loads(secrets_path().read_text(encoding="utf-8")) == {
        "rentcast_api_key": FAKE_KEY
    }


def test_saving_a_blank_key_is_refused(home) -> None:
    """A stored blank would produce a file that looks configured while
    `load_api_key()` reports `None` — the exact confusion the whitespace rule
    exists to prevent, made persistent."""
    for blank in ("", "   ", "\n"):
        with pytest.raises(ValueError):
            save_api_key(blank)
    assert not secrets_path().exists()


def test_a_stored_blank_or_malformed_secrets_file_reads_as_no_key(home) -> None:
    """Hand-edited files exist. Every one of these means "no key", and the
    honest next step is D17's loud `MissingApiKeyError` before a call — not a
    401 that cost one of the month's 50."""
    home.mkdir(parents=True, exist_ok=True)
    for body in ('{"rentcast_api_key": "   "}', '{"rentcast_api_key": null}', "[]", "{}", "nope"):
        secrets_path().write_text(body, encoding="utf-8")
        assert load_api_key() is None, f"{body!r} was read as a key"


def test_reading_a_key_never_creates_the_storage_root(home) -> None:
    """F0-S5's convention: reading is not a mutation. A fresh clone running
    `pytest` must not sprout a `~/.rentcomp`."""
    assert load_api_key() is None
    assert not home.exists()


def test_saving_a_key_creates_the_root_it_needs(home) -> None:
    """The write half of the same convention — saving *is* a mutation, and a
    first run has no directory yet."""
    path = save_api_key(FAKE_KEY)
    assert path == secrets_path() and path.exists()
    assert not list(home.glob(".*.tmp")), "the atomic-write temp file was left behind"


# ---------------------------------------------------------------------------
# storage/ledger.py — the month rule (PM ruling, F0-S4)
# ---------------------------------------------------------------------------


def write_ledger(home: Path, payload: dict) -> Path:
    home.mkdir(parents=True, exist_ok=True)
    path = home / "ledger.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_the_gates_committed_ledger_migrates_without_handing_calls_back(home) -> None:
    """THE migration case, against the real file. `fixtures/live-samples/
    ledger.json` records 8 calls and has no month stamp. Read today it must
    still say 8 — a rule that treated "no month" as "unknown, therefore zero"
    would silently hand back 8 calls the owner has already paid for, and the
    first symptom would be an unexplained overage.
    """
    committed = json.loads((LIVE_SAMPLES / "ledger.json").read_text(encoding="utf-8"))
    assert "month" not in committed and committed["calls_this_month"] == 8, (
        "the committed gate ledger changed shape — re-check the migration rule"
    )
    write_ledger(home, committed)

    ledger = load_ledger()
    assert ledger.calls_this_month == 8
    assert ledger.month == current_month(), "the migrated ledger is stamped with today's month"
    assert ledger.remaining == MONTHLY_CALL_CAP - 8


@pytest.mark.parametrize("month", ["", "banana", "2026-13", "2026-1", "202607", None, 7])
def test_a_malformed_month_also_reads_as_this_month(home, month) -> None:
    """Same direction as absent, for the same reason. A month field we cannot
    parse tells us nothing about whether the allowance rolled over, and the
    only safe reading of "nothing" is "do not hand back budget"."""
    write_ledger(home, {"month": month, "calls_this_month": 8, "history": []})
    assert load_ledger().calls_this_month == 8


def test_a_stamp_for_this_month_is_kept_as_is(home) -> None:
    write_ledger(home, {"month": current_month(), "calls_this_month": 12, "history": []})
    assert load_ledger().calls_this_month == 12


@pytest.mark.parametrize("shift", [-1, -2, -13])
def test_a_stamp_for_a_past_month_rolls_the_allowance_over(home, shift: int) -> None:
    """The reason the field exists: without it the counter only ever went up,
    and the tool would have refused to fetch anything, permanently, at 50
    lifetime calls."""
    write_ledger(
        home,
        {"month": month_offset(shift), "calls_this_month": MONTHLY_CALL_CAP, "history": [{}]},
    )
    ledger = load_ledger()
    assert ledger.calls_this_month == 0
    assert ledger.month == current_month()
    assert ledger.history == (), "last month's history was carried into the new month's count"
    assert ledger.remaining == MONTHLY_CALL_CAP


def test_a_stamp_for_a_future_month_does_not_hand_calls_back(home) -> None:
    """A clock skew or a hand-edit must not become free calls. The count is
    kept and the ledger restamps itself to today, so the next month rolls over
    normally instead of being stuck in the future forever."""
    write_ledger(home, {"month": month_offset(+1), "calls_this_month": 30, "history": []})
    ledger = load_ledger()
    assert ledger.calls_this_month == 30
    assert ledger.month == current_month()


def test_loading_a_ledger_never_writes_anything(home) -> None:
    """Fixture mode must be able to run on a machine with no `~/.rentcomp`, and
    a *read* that created a ledger would make "the suite spends nothing"
    depend on nobody ever looking."""
    assert load_ledger().calls_this_month == 0
    assert not home.exists()

    write_ledger(home, {"month": month_offset(-1), "calls_this_month": 9, "history": []})
    before = ledger_path().read_bytes()
    load_ledger()
    assert ledger_path().read_bytes() == before, "a rolled-over month was persisted by a read"


@pytest.mark.parametrize(
    "payload",
    ['{"calls_this_month": "eight"}', '{"calls_this_month": -1}', '{"calls_this_month": 1.5}',
     "not json at all", "[]"],
)
def test_an_unreadable_ledger_is_loud_not_a_silent_zero(home, payload: str) -> None:
    """A corrupt ledger that quietly reads as zero hands the month's whole
    remaining budget to whatever corrupted it. `LedgerError` names the path so
    the user can go look — the same contract `ConfigError` has (F0-S5)."""
    home.mkdir(parents=True, exist_ok=True)
    ledger_path().write_text(payload, encoding="utf-8")
    with pytest.raises(LedgerError) as excinfo:
        load_ledger()
    assert str(ledger_path()) in str(excinfo.value)


def test_a_ledger_round_trips_through_disk(home) -> None:
    ledger = Ledger(
        month=current_month(),
        calls_this_month=2,
        history=(
            CallRecord(sig="aaa", params={"status": "Active"}, status=200, total_count=39),
            CallRecord(sig="bbb", params={"status": "Inactive"}),
        ),
    )
    save_ledger(ledger)
    reloaded = load_ledger()
    assert reloaded.calls_this_month == 2
    assert [record.sig for record in reloaded.history] == ["aaa", "bbb"]
    assert reloaded.history[0].total_count == 39
    assert reloaded.history[1].status is None, "a call with no known outcome must stay unknown"
    assert json.loads(ledger_path().read_text(encoding="utf-8"))["month"] == current_month()


def test_recording_a_call_never_lowers_the_count(home) -> None:
    """`with_outcome` fills in what came back; it must not touch the count.
    RentCast charged for the call whatever the far end said."""
    ledger = Ledger(month=current_month(), calls_this_month=0)
    sent = ledger.with_call(sig="s", params={"a": 1})
    assert sent.calls_this_month == 1
    answered = sent.with_outcome(status=429, total_count=None)
    assert answered.calls_this_month == 1
    assert answered.history[-1].status == 429
    assert ledger.calls_this_month == 0, "the original ledger was mutated in place"


def test_remaining_never_goes_negative(home) -> None:
    """A hand-edited (or historically over-spent) ledger must read as "zero
    left", not as a negative number some caller compares with `> 0`."""
    assert Ledger(month=current_month(), calls_this_month=999).remaining == 0
