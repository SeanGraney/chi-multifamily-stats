"""F0-S4 — RentCast client: wire syntax, fixture mode, key storage.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol). The live-call guard (D17) is a separate file,
`test_live_call_guard.py`; the assembled fetch behaviour (pagination, error
mapping, the ledger) is `tests/api/test_rentcast_client_surface.py`.

WHY LAYER 1
-----------
AGENT_QA.md's decision procedure, step 1: everything here is "import a
function and call it with plain values" — a param dict, a fixture file, a
range string. No HTTP is involved even in principle: fixture mode is the
default mode and reads from disk.

THE CONTRACT THESE TESTS PIN
----------------------------
The story text names no module, so every path below is **QA-PROPOSED** and
needs PM confirmation (the convention F0-S3/F0-S5/F0-S2 established). The
developer may counter-propose through the PM; QA then edits these tests.

``rentcomp.client.query`` — the wire syntax, inherited from T-S3
  ``rentcast_range(min_value, max_value) -> str``   ``5:10`` / ``2000:*`` / ``*:10`` / ``5:5``
  ``rentcast_multi(values) -> str``                 pipe-joined
  ``build_listing_params(...) -> dict``             one (status x daysOld x offset) query

``rentcomp.client.rentcast`` — the client
  ``RENTCAST_BASE``      ``https://api.rentcast.io/v1``      (spec §3.1)
  ``LISTINGS_PATH``      ``/listings/rental/long-term``      (NORTH_STAR: never /avm)
  ``API_KEY_HEADER``     ``X-Api-Key``                       (spec §3.1)
  ``MAX_LIMIT``          ``500``                             (schema ceiling)
  ``Mode``               ``FIXTURE`` | ``LIVE``
  ``resolve_mode()``     env -> Mode, at call time
  ``RentCastClient(mode=None, *, transport=None, api_key=None, fixtures_dir=None, sink=None)``
  ``RentCastClient.fetch_listings(params, *, max_calls=None) -> ListingsResult``
  ``ListingsResult``     records / total_count / fetched / complete / missing /
                         calls_spent / pages
  ``ListingsPage``       records / total_count / offset / raw (BYTES, unparsed)
  ``fixture_signature(params) -> str``   must agree with the gate's own
  errors: ``RentCastError`` (base) · ``LiveModeDisabledError`` ·
          ``MissingApiKeyError`` · ``AuthError`` (401) · ``RateLimitError`` (429) ·
          ``UpstreamError`` (other non-2xx) · ``FixtureMissingError`` ·
          ``CallBudgetExceededError``

``rentcomp.storage.secrets`` — "API key entered in Settings, stored locally"
  ``secrets_path()`` / ``load_api_key()`` / ``save_api_key(key)``  (D16, mode 0600)

DELIBERATELY NOT PINNED HERE (scope boundaries — flagged in the handoff)
-----------------------------------------------------------------------
* **Parsing records into DTOs.** ``models/dto.py`` is empty and its docstring
  says "populated by F4-S2 against fixtures/live-samples/ ground truth". The
  client hands back raw records; typing them is F4-S2's job, and D24 wants the
  bytes safe on disk *before* anything validates them anyway.
* **The query PLAN** (which windows, how many years, PAD=90) — F4-S1.
* **The cache key and the raw store under ``~/.rentcomp/cache/``** — F3-S1,
  and the resumable manifest is F3-S4. What F0-S4 owes them is the ``sink``
  seam and an honest ``complete``/``missing`` on the result.
* **The Settings UI** — no frontend exists (F0-S1b is still READY).

EXPECTED RED STATE TODAY: three import-gate tests FAIL naming exactly what is
missing (client, query builders, secrets store); the gate-parity tests that
only touch ``scripts/gate.py`` PASS; everything else SKIPS.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_SAMPLES = REPO_ROOT / "fixtures" / "live-samples"
GATE_LEDGER = LIVE_SAMPLES / "ledger.json"

# `scripts/` is not a package; scripts/tests/conftest.py puts it on sys.path
# for its own suite only, so do it here too rather than depend on collection
# order.
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gate  # noqa: E402  (path juggling above)

try:
    from rentcomp.client.rentcast import (
        API_KEY_HEADER,
        LISTINGS_PATH,
        MAX_LIMIT,
        RENTCAST_BASE,
        AuthError,
        CallBudgetExceededError,
        FixtureMissingError,
        ListingsPage,
        ListingsResult,
        LiveModeDisabledError,
        MissingApiKeyError,
        Mode,
        RateLimitError,
        RentCastClient,
        RentCastError,
        UpstreamError,
        fixture_signature,
        resolve_mode,
    )

    _CLIENT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _CLIENT_ERROR = _exc

try:
    from rentcomp.client.query import build_listing_params, rentcast_multi, rentcast_range

    _QUERY_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _QUERY_ERROR = _exc

try:
    from rentcomp.storage.secrets import load_api_key, save_api_key, secrets_path

    _SECRETS_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _SECRETS_ERROR = _exc

needs_client = pytest.mark.skipif(
    _CLIENT_ERROR is not None, reason=f"rentcomp.client.rentcast missing (F0-S4 red): {_CLIENT_ERROR}"
)
needs_query = pytest.mark.skipif(
    _QUERY_ERROR is not None, reason=f"rentcomp.client.query missing (F0-S4 red): {_QUERY_ERROR}"
)
needs_secrets = pytest.mark.skipif(
    _SECRETS_ERROR is not None, reason=f"rentcomp.storage.secrets missing (F0-S4 red): {_SECRETS_ERROR}"
)

FAKE_KEY = "qa-fake-key-not-a-real-credential"


# ---------------------------------------------------------------------------
# fixture-mode environment
# ---------------------------------------------------------------------------


@pytest.fixture
def fixture_mode(tmp_path, monkeypatch) -> Path:
    """Default mode, no key, storage in a temp dir. Reads the REAL committed
    `fixtures/live-samples/` — that is the dataset the AC is about."""
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    return tmp_path


def gate_ledger_entries() -> list[dict]:
    return json.loads(GATE_LEDGER.read_text(encoding="utf-8"))["history"]


def entries_with_records() -> list[dict]:
    """The gate calls that actually returned records (2 of 8) — the ones whose
    byte-identity is worth more than "both files are `[]`"."""
    return [e for e in gate_ledger_entries() if (e.get("total_count") or 0) > 0]


# ---------------------------------------------------------------------------
# existence — legible red
# ---------------------------------------------------------------------------


def test_client_module_imports_its_contract() -> None:
    assert _CLIENT_ERROR is None, (
        "Cannot import the client contract from rentcomp.client.rentcast "
        f"(QA-proposed, see this module's docstring): {_CLIENT_ERROR}"
    )


def test_query_module_imports_its_contract() -> None:
    assert _QUERY_ERROR is None, (
        "Cannot import the wire-syntax builders from rentcomp.client.query: " f"{_QUERY_ERROR}"
    )


def test_secrets_store_imports_its_contract() -> None:
    assert _SECRETS_ERROR is None, (
        "Cannot import the local API-key store from rentcomp.storage.secrets "
        f"(D16 — the story's 'stored locally'): {_SECRETS_ERROR}"
    )


# ---------------------------------------------------------------------------
# the endpoint itself (NORTH_STAR: evidence, never a vendor model)
# ---------------------------------------------------------------------------


@needs_client
def test_the_client_targets_the_listings_endpoint_on_the_documented_base() -> None:
    """Spec §3.1. The `/avm` exclusion is not a preference: pulling RentCast's
    own estimate would replace observed evidence with a restatement of someone
    else's model and would pass every numeric test we have (NORTH_STAR,
    SEMANTIC_CHANGE_PROTOCOL §2)."""
    assert RENTCAST_BASE == "https://api.rentcast.io/v1"
    assert LISTINGS_PATH == "/listings/rental/long-term"
    assert API_KEY_HEADER == "X-Api-Key"
    assert MAX_LIMIT == 500, "the schema caps `limit` at 500; asking for more is what truncates"


def test_the_client_package_never_mentions_the_avm_endpoint() -> None:
    """A one-line guard against the canonical bad swap. `/avm/rent/long-term`
    is a v2 *benchmark* (spec §3.1) and must never be reachable from the comp
    pipeline's client without the semantic-change protocol."""
    client_dir = REPO_ROOT / "backend" / "src" / "rentcomp" / "client"
    for source in sorted(client_dir.rglob("*.py")):
        text = source.read_text(encoding="utf-8")
        offending = [
            line
            for line in text.splitlines()
            if "/avm" in line and not line.lstrip().startswith("#") and "never" not in line.lower()
        ]
        assert not offending, (
            f"{source.name} reaches for /avm: {offending} — SEMANTIC_CHANGE_PROTOCOL §2 requires "
            "owner sign-off, not a code change"
        )


# ---------------------------------------------------------------------------
# wire syntax — inherited from T-S3, and it must not drift
# ---------------------------------------------------------------------------

RANGE_CASES = [
    ((5, 10), "5:10"),
    ((2000, None), "2000:*"),
    ((None, 10), "*:10"),
    ((5, 5), "5:5"),
    ((1, 1095), "1:1095"),
    ((1.5, 2), "1.5:2"),
]


@needs_query
@pytest.mark.parametrize(("args", "expected"), RANGE_CASES)
def test_numeric_ranges_are_colon_syntax_on_one_param(args, expected: str) -> None:
    """[INVARIANT] RentCast has no `daysOldMin`/`daysOldMax`; a range is ONE
    param, colon-separated, `*` for an open bound. This is the answer T-S3
    bought with real calls (QUEUE log 2026-07-26) — F0-S4 inherits it, F2-S2
    will too."""
    assert rentcast_range(*args) == expected


@needs_query
def test_a_degenerate_range_is_still_a_range() -> None:
    """The round-1 defect class, at the library level: RentCast reads a BARE
    `daysOld=5` as the range MAXIMUM (0..5), not as "exactly 5". A degenerate
    range must therefore be emitted as `5:5`, never as `5`."""
    assert rentcast_range(5, 5) == "5:5"
    assert ":" in rentcast_range(88, 88)


@needs_query
def test_a_fully_unpinned_range_is_a_caller_bug() -> None:
    """`*:*` is not a query, it is a mistake that silently widens the pull."""
    with pytest.raises(ValueError):
        rentcast_range(None, None)


@needs_query
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["Apartment"], "Apartment"),
        (["Multi-Family", "Apartment", "Townhouse"], "Multi-Family|Apartment|Townhouse"),
        (
            ["Multi-Family", "Apartment", "Townhouse", "Single Family", "Condo"],
            "Multi-Family|Apartment|Townhouse|Single Family|Condo",
        ),
    ],
)
def test_multi_values_are_pipe_joined(values: list[str], expected: str) -> None:
    """[INVARIANT] Commas match nothing (QUEUE log 2026-07-26). A comma-joined
    propertyType is not an error — it is an empty pull that looks like a thin
    market."""
    assert rentcast_multi(values) == expected


@needs_query
def test_an_empty_multi_value_is_a_caller_bug() -> None:
    with pytest.raises(ValueError):
        rentcast_multi([])


@needs_query
@pytest.mark.parametrize(("args", "expected"), RANGE_CASES)
def test_the_syntax_cannot_drift_from_the_gates(args, expected: str) -> None:
    """ONE answer, two callers. The gate is what spends money and it is the
    only code whose syntax has been verified against the real API (8 calls,
    all HTTP 200, `X-Total-Count` echoed) — so the product's builders must
    agree with it exactly, character for character.

    Asserted as BEHAVIOURAL equivalence rather than object identity on
    purpose: whether `scripts/gate.py` ends up importing these functions from
    the package, or the package keeps its own copy, is a structural question
    for the PM (QA recommends the import). Drift is the risk either way, and
    drift is what this catches.
    """
    assert gate.rentcast_range(*args) == rentcast_range(*args) == expected


@needs_query
def test_multi_value_syntax_cannot_drift_from_the_gates() -> None:
    values = ["Multi-Family", "Apartment", "Townhouse", "Single Family", "Condo"]
    assert gate.rentcast_multi(values) == rentcast_multi(values)


# ---------------------------------------------------------------------------
# param building — reproducing queries that really happened
# ---------------------------------------------------------------------------


@needs_query
@pytest.mark.parametrize("entry", entries_with_records(), ids=lambda e: e["sig"])
def test_the_builder_reproduces_the_gates_real_wire_params(entry: dict) -> None:
    """The strongest available check that the client speaks the same wire
    language as the calls we have already paid for: given the same search
    inputs, `build_listing_params` must produce the exact param dict the gate
    sent for `fixtures/live-samples/<sig>.json`.

    If this holds, fixture lookup by params can work at all; if it does not,
    every fixture in the repo is unreachable from the product code.
    """
    expected = entry["params"]
    built = build_listing_params(
        address=expected["address"],
        radius=expected["radius"],
        bedrooms=expected["bedrooms"],
        bathrooms=expected.get("bathrooms"),
        property_types=expected["propertyType"].split("|"),
        status=expected["status"],
        days_old=expected["daysOld"],
    )
    assert built == expected, (
        "built params differ from the gate's real ones.\n"
        f"  built:    {sorted(built.items())}\n"
        f"  expected: {sorted(expected.items())}"
    )


@needs_query
def test_an_omitted_bathrooms_filter_is_absent_not_empty() -> None:
    """T-S3's round-2 finding: sending `bathrooms=""` or `*:*` makes the
    server exclude records with missing bathroom data. Omitted must mean
    OMITTED — the round-2 broad pull (539 records) had no bathrooms key at
    all."""
    params = build_listing_params(
        address="3651 S Wood St, Chicago, IL 60609",
        radius=2.0,
        bedrooms="3:4",
        bathrooms=None,
        property_types=["Apartment"],
        status="Active",
        days_old="1:1095",
    )
    assert "bathrooms" not in params


@needs_query
def test_the_builder_asks_for_the_total_count_and_the_full_page() -> None:
    """`includeTotalCount` is how the client learns it was truncated (F4-S7
    §3: Inactive returned 500 of 690). Without it there is no way to tell a
    complete pull from a capped one, and the difference silently skews every
    downstream number."""
    params = build_listing_params(
        address="a",
        radius=1.0,
        bedrooms="3",
        property_types=["Apartment"],
        status="Inactive",
        days_old="1:1095",
    )
    assert params["limit"] == 500
    assert str(params["includeTotalCount"]).lower() in {"true", "True"}


@needs_query
def test_offset_appears_only_on_continuation_pages() -> None:
    """Page 0 must produce the same params (and therefore the same fixture
    signature) as the gate's un-offset call — otherwise nothing in
    `fixtures/live-samples/` is findable."""
    first = build_listing_params(
        address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
        status="Active", days_old="1:1095",
    )
    assert "offset" not in first or first["offset"] == 0

    second = build_listing_params(
        address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
        status="Active", days_old="1:1095", offset=500,
    )
    assert second["offset"] == 500
    assert {k: v for k, v in second.items() if k != "offset"} == {
        k: v for k, v in first.items() if k != "offset"
    }, "paging changed something other than the offset — page 2 would be a different query"


@needs_query
def test_a_bare_days_old_value_is_refused() -> None:
    """The operator-interface guard `scripts/gate.py` already carries, moved
    into the library: a bare `daysOld` is read by the API as the range MAXIMUM,
    so accepting one silently widens the pull to "everything newer than N"."""
    with pytest.raises(ValueError):
        build_listing_params(
            address="a", radius=1.0, bedrooms="3", property_types=["Apartment"],
            status="Active", days_old="5",
        )


# ---------------------------------------------------------------------------
# fixture mode — byte-identity with what the gate paid for (THE AC)
# ---------------------------------------------------------------------------


@needs_client
@pytest.mark.parametrize("entry", gate_ledger_entries(), ids=lambda e: e["sig"])
def test_fixture_lookup_agrees_with_the_gates_signature(entry: dict) -> None:
    """Fixture mode has to find the file the gate wrote. The gate's naming
    scheme is `sha256(json.dumps(params, sort_keys=True))[:16]`; whatever the
    client's scheme is, it must land on the same name for the same params, or
    the committed dataset is unreachable."""
    assert fixture_signature(entry["params"]) == entry["sig"], (
        f"fixture_signature disagrees with the gate for {entry['sig']} — "
        f"{LIVE_SAMPLES / (entry['sig'] + '.json')} would never be found"
    )


@needs_client
@pytest.mark.parametrize("entry", gate_ledger_entries(), ids=lambda e: e["sig"])
def test_fixture_responses_are_byte_identical_to_the_saved_originals(
    fixture_mode: Path, entry: dict
) -> None:
    """THE AC, literally: "fixture responses are byte-identical to the gate's
    saved raw responses".

    Bytes, not just equal JSON — D24 requires the raw body to reach disk
    unparsed and unmodified, and F3-S1 stores exactly these bytes. A client
    that round-trips through `json.dumps` would re-key, re-space and re-order
    nothing visible today and something invisible later.
    """
    saved = (LIVE_SAMPLES / f"{entry['sig']}.json").read_bytes()
    result = RentCastClient().fetch_listings(dict(entry["params"]))

    assert isinstance(result, ListingsResult)
    assert len(result.pages) == 1, "one saved response is one page"
    page = result.pages[0]
    assert isinstance(page, ListingsPage)
    assert page.raw == saved, (
        f"fixture-mode bytes differ from {entry['sig']}.json "
        f"({len(page.raw)} vs {len(saved)} bytes)"
    )
    assert list(result.records) == json.loads(saved), "parsed records differ from the saved file"


@needs_client
def test_fixture_mode_reports_the_saved_total_count(fixture_mode: Path) -> None:
    """F4-S7 §3, reproduced offline: the broad Inactive pull returned 500
    records with `X-Total-Count: 690`. The committed fixture IS that truncated
    sample, and fixture mode must say so — a fixture that reports itself
    complete would let every downstream test develop against a silently
    500/690 dataset believing it whole.
    """
    inactive = next(e for e in gate_ledger_entries() if e["total_count"] == 690)
    result = RentCastClient().fetch_listings(dict(inactive["params"]))

    assert result.total_count == 690
    assert result.fetched == 500
    assert result.complete is False, "a 500-of-690 pull is not complete"
    assert result.missing == 190, f"missing should be 690 - 500 = 190, got {result.missing!r}"


@needs_client
def test_a_complete_fixture_reports_itself_complete(fixture_mode: Path) -> None:
    """The other half — `X-Total-Count: 39`, 39 records. If everything were
    reported incomplete the flag would carry no information."""
    active = next(e for e in gate_ledger_entries() if e["total_count"] == 39)
    result = RentCastClient().fetch_listings(dict(active["params"]))
    assert result.total_count == 39 and result.fetched == 39
    assert result.complete is True and not result.missing


@needs_client
def test_an_empty_fixture_is_a_real_answer_not_a_miss(fixture_mode: Path) -> None:
    """Six of the eight gate responses are `[]` — a genuinely empty market
    (that is what NO-GO round 1 meant). Zero records must come back as an
    empty, COMPLETE result, never as a fixture-missing error: the two mean
    opposite things to F4's empty state."""
    empty = next(e for e in gate_ledger_entries() if e["total_count"] == 0)
    result = RentCastClient().fetch_listings(dict(empty["params"]))
    assert list(result.records) == []
    assert result.complete is True


@needs_client
def test_an_unknown_query_fails_loudly_in_fixture_mode(fixture_mode: Path) -> None:
    """"No fixture for these params" must never render as "no comps in this
    market". Same distinction as above, in the other direction — and the error
    has to name the signature so a developer can go make the fixture."""
    with pytest.raises(FixtureMissingError) as excinfo:
        RentCastClient().fetch_listings(
            {
                "address": "somewhere nobody pulled, IL",
                "radius": 1.0,
                "bedrooms": "3",
                "propertyType": "Apartment",
                "status": "Active",
                "daysOld": "1:30",
                "limit": 500,
                "includeTotalCount": "true",
            }
        )
    assert str(excinfo.value).strip(), "FixtureMissingError carries no message"


@needs_client
def test_fixture_mode_spends_nothing_and_writes_nothing(fixture_mode: Path) -> None:
    """[INVARIANT] "fixture (default: ... zero network)". Zero network implies
    zero spend: no ledger entry, no call counted, and nothing written into
    RENTCOMP_HOME by a read."""
    entry = entries_with_records()[0]
    result = RentCastClient().fetch_listings(dict(entry["params"]))
    assert result.calls_spent == 0
    assert list(fixture_mode.iterdir()) == [], (
        f"fixture mode wrote into RENTCOMP_HOME: {[p.name for p in fixture_mode.iterdir()]}"
    )


@needs_client
def test_fixture_mode_never_modifies_the_committed_samples(fixture_mode: Path) -> None:
    """The committed dataset is evidence (T-S3) as well as test data. A client
    that rewrote it — even formatting — would destroy the only proof of what
    the API actually returned."""
    before = {p.name: p.read_bytes() for p in sorted(LIVE_SAMPLES.glob("*.json"))}
    for entry in gate_ledger_entries():
        RentCastClient().fetch_listings(dict(entry["params"]))
    after = {p.name: p.read_bytes() for p in sorted(LIVE_SAMPLES.glob("*.json"))}
    assert after == before, "fixtures/live-samples/ was modified by a fixture-mode read"


# ---------------------------------------------------------------------------
# the error taxonomy (behaviour lives in tests/api/, shape lives here)
# ---------------------------------------------------------------------------


@needs_client
def test_the_error_types_are_distinct_and_share_one_base() -> None:
    """Spec §7 wants 401 and rate limit surfaced *plainly* and distinctly. One
    base so a caller can say "anything the client can go wrong with"; distinct
    subclasses so the UI can say the two different true things ("your key is
    wrong" vs "you are out of calls this month" — opposite user actions)."""
    everything = [
        LiveModeDisabledError,
        MissingApiKeyError,
        AuthError,
        RateLimitError,
        UpstreamError,
        FixtureMissingError,
        CallBudgetExceededError,
    ]
    for error in everything:
        assert issubclass(error, RentCastError), f"{error.__name__} is not a RentCastError"
    assert len(set(everything)) == len(everything), "two names, one class — they must be distinct"
    assert AuthError is not RateLimitError
    assert not issubclass(AuthError, RateLimitError) and not issubclass(RateLimitError, AuthError), (
        "401 and 429 must not be each other's subclass — `except AuthError` would then swallow "
        "a rate limit and the user would go re-enter a perfectly good key"
    )
    assert issubclass(RentCastError, Exception)


# ---------------------------------------------------------------------------
# "API key entered in Settings, stored locally" (D16)
# ---------------------------------------------------------------------------


@needs_secrets
def test_the_key_is_stored_under_the_storage_root(tmp_path, monkeypatch) -> None:
    """ARCHITECTURE.md §5: `~/.rentcomp/secrets.json`. Resolved through
    `rentcomp_home()` so tests and E2E never touch the real one."""
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    assert Path(secrets_path()) == tmp_path / "secrets.json"


@needs_secrets
def test_the_key_round_trips_and_the_file_is_0600(tmp_path, monkeypatch) -> None:
    """D16: mode 0600. This is a single-user local tool, but the file sits in
    the user's home next to everything else and a world-readable credential is
    a defect regardless of threat model."""
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    save_api_key(FAKE_KEY)
    assert load_api_key() == FAKE_KEY

    mode = stat.S_IMODE(Path(secrets_path()).stat().st_mode)
    assert mode == 0o600, f"secrets.json is mode {mode:o}, expected 600"


@needs_secrets
def test_no_key_stored_and_none_in_the_env_reads_as_none(tmp_path, monkeypatch) -> None:
    """First run. `None`, not `""` and not an exception — "no key yet" is a
    normal state that fixture mode does not care about at all."""
    monkeypatch.delenv("RENTCAST_API_KEY", raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    assert load_api_key() is None


@needs_secrets
def test_the_env_var_is_honoured(tmp_path, monkeypatch) -> None:
    """D16 allows either source. The gate and the owner's `.env` both use the
    env var, so it must work with no secrets file present at all."""
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    assert load_api_key() == FAKE_KEY


@needs_secrets
def test_the_secrets_file_never_lands_in_the_repo(tmp_path, monkeypatch) -> None:
    """"Secrets never enter the repo" (CLAUDE.md). Saving a key must write
    under the storage root and nowhere else — a relative-path default would
    put a credential next to the source."""
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
    save_api_key(FAKE_KEY)
    assert Path(secrets_path()).is_relative_to(tmp_path)
    strays = [p for p in REPO_ROOT.rglob("secrets.json") if ".venv" not in str(p)]
    assert not strays, f"a secrets.json exists inside the repo: {strays}"


class TestProposedContract:
    """QA-proposed behaviour the story text does NOT state. The PM confirms
    (or overrides) before these count as locked."""

    @needs_secrets
    def test_the_env_var_wins_over_the_stored_file(self, tmp_path, monkeypatch) -> None:
        """D16 says "secrets.json **or** `RENTCAST_API_KEY`" without ordering.
        PROPOSED: the env var wins — it is the explicit, per-process override
        (and the one the gate already uses), so a developer can point a run at
        a different key without editing stored state."""
        monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
        save_api_key("stored-key-value")
        monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
        assert load_api_key() == FAKE_KEY

    @needs_secrets
    @pytest.mark.parametrize("blank", ["", "   ", "\n"])
    def test_a_blank_key_is_not_a_key(self, tmp_path, monkeypatch, blank: str) -> None:
        """PROPOSED: whitespace-only reads as absent. `RENTCAST_API_KEY=` in a
        shell profile is the classic way to *think* you have a key; under D17
        the honest answer is `MissingApiKeyError` before any call, not a 401
        that cost a call."""
        monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
        monkeypatch.setenv("RENTCAST_API_KEY", blank)
        assert load_api_key() is None

    @needs_client
    def test_resolve_mode_reads_the_environment_at_call_time(
        self, tmp_path, monkeypatch
    ) -> None:
        """PROPOSED (consistent with `rentcomp_home()`, F0-S5): no import-time
        snapshot. A cached mode would make every test that manipulates the flag
        depend on import order, which is exactly the property you do not want
        guarding a spend."""
        monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
        monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
        assert resolve_mode() is Mode.FIXTURE
        monkeypatch.setenv("RENTCOMP_LIVE", "1")
        monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
        assert resolve_mode() is Mode.LIVE
        monkeypatch.delenv("RENTCOMP_LIVE")
        assert resolve_mode() is Mode.FIXTURE

    @needs_client
    def test_the_fixtures_directory_is_overridable(self, tmp_path, monkeypatch) -> None:
        """PROPOSED: `RENTCOMP_FIXTURES_DIR` (same shape as F0-S2's
        `RENTCOMP_FIXTURE_PULLS_DIR`), so a synthetic-fixture story or a
        Playwright `globalSetup` can point the client at a temp dataset without
        touching the committed one."""
        monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path))
        fixtures = tmp_path / "fixtures"
        fixtures.mkdir()
        entry = entries_with_records()[0]
        body = b'[{"id": "synthetic-1"}]'
        (fixtures / f"{fixture_signature(entry['params'])}.json").write_bytes(body)
        monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))

        result = RentCastClient().fetch_listings(dict(entry["params"]))
        assert result.pages[0].raw == body
        assert [r["id"] for r in result.records] == ["synthetic-1"]

    @needs_client
    def test_an_unset_home_is_never_written_to_by_a_fixture_read(self, tmp_path, monkeypatch) -> None:
        """PROPOSED: reading is not a mutation (the F0-S5 P5 convention) —
        fixture mode must not create `~/.rentcomp/` as a side effect."""
        root = tmp_path / "never-created"
        monkeypatch.setenv("RENTCOMP_HOME", str(root))
        monkeypatch.delenv("RENTCOMP_LIVE", raising=False)
        RentCastClient().fetch_listings(dict(entries_with_records()[0]["params"]))
        assert not root.exists()


def test_no_test_in_this_file_can_be_run_in_live_mode() -> None:
    """Belt and braces on top of `conftest.py`'s socket kill switch: if the
    flag is inherited from the shell, stop rather than proceed."""
    assert os.environ.get("RENTCOMP_LIVE") != "1"
