"""F4-S9 AC4 [INVARIANT] — D17 still holds once something composes a whole pull.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol).

THE AC THIS FILE OWNS
---------------------
    "Never goes live without BOTH `RENTCOMP_LIVE=1` and a key present (D17).
     Default is fixture mode. [INVARIANT] — assert this several independent
     ways."

WHY THIS NEEDS ITS OWN FILE WHEN F0-S4 ALREADY HAS ONE
-------------------------------------------------------
`tests/unit/test_live_call_guard.py` proves D17 holds for the client that
issues ONE query. F4-S9 is the first code that runs a whole plan, and it
introduces two new ways to lose the guarantee that the F0-S4 file cannot see:

1. **A second door.** An orchestrator that builds its own `httpx.Client` (for
   a retry, a HEAD probe, a "quick" concurrency experiment) would be inside
   `client/`, so F0-S4's "only `client/` may import httpx" guard would still
   pass — while the mode check in `RentCastClient` was bypassed entirely.
   `test_only_the_rentcast_client_may_build_a_transport` closes that.

2. **A transport that implies consent.** `transport=` exists so tests can
   exercise the live path without spending. If an implementation treats "a
   transport was injected" as "go live", then D17 is enforced against the
   environment but not against the code — and every one of QA's own specs
   becomes the thing that turns live mode on.
   `test_injecting_a_transport_does_not_by_itself_enable_live_mode` closes that.

Asserted three independent ways, matching the rigor F0-S4's QA set:
structurally over the source (total over code paths, including ones no test
walks), at runtime with `httpx` booby-trapped (total over the process), and
against the suite's socket kill switch (total over the runner).

WHY LAYER 1 (AGENT_QA.md decision procedure)
--------------------------------------------
Step 1 matches: import a module, call it with plain values. "No transport was
constructed" and "no source file may name this symbol" are not observable from
a browser, and a Playwright spec asserting them would be an L1 test wearing a
browser costume. One representative D17 assertion is made at Layer 2 in
`tests/api/test_f4s9_search_route.py` (the route, in default env, spends
nothing) — exhaustive below, representative above.

MONEY: not one test here sets `RENTCOMP_LIVE=1`. The whole file is about the
paths where it is NOT set. The single test that needs a key present sets a
fake one, and asserts it never leaves the process.

EXPECTED RED STATE TODAY: the structural guards RUN and PASS (vacuously —
there is no orchestrator module yet, which is the honest answer); the runtime
guards SKIP behind the import. `test_the_pull_orchestrator_module_exists` in
`test_f4s9_pull_orchestrator.py` is the one loud failure for this story.
"""

from __future__ import annotations

import ast
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from rentcomp.client.planner import fetchable_queries, plan_pull_queries
from rentcomp.client.rentcast import RentCastError, fixture_signature
from rentcomp.storage.config import Config
from rentcomp.storage.ledger import Ledger, current_month, load_ledger, save_ledger
from rentcomp.storage.pulls import load_shaped_pull

BACKEND_SRC = Path(__file__).resolve().parents[2] / "src" / "rentcomp"

#: D17's flag and D16's key. Names fixed by the architecture, not by taste.
LIVE_ENV = "RENTCOMP_LIVE"
LIVE_VALUE = "1"
KEY_ENV = "RENTCAST_API_KEY"
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

FAKE_KEY = "qa-f4s9-guard-fake-key-not-a-real-credential"

#: The module this story is expected to add. Named here so the structural
#: guards can say something precise while it does not exist yet.
ORCHESTRATOR_SOURCE = BACKEND_SRC / "client" / "pull.py"

try:
    from rentcomp.client.pull import run_pull

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F4-S9 lands
    _IMPORT_ERROR = _exc

needs_orchestrator = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"rentcomp.client.pull is not importable yet (F4-S9 red): {_IMPORT_ERROR}",
)


# ---------------------------------------------------------------------------
# the search these guards drive
# ---------------------------------------------------------------------------

TODAY = date(2026, 7, 27)

SEARCH = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start_mmdd": "01-01",
    "window_end_mmdd": "12-31",
}


def _record(n: int) -> dict:
    return {
        "id": f"f4s9-guard-{n}",
        "formattedAddress": f"{200 + n} S Guard Rd, Chicago, IL 60609",
        "addressLine1": f"{200 + n} S Guard Rd",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 950,
        "listedDate": "2026-03-01T00:00:00.000Z",
        "status": "Active",
    }


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def default_env(tmp_path, monkeypatch) -> Path:
    """The DEFAULT state of every dev machine, test run, QA run and E2E run
    (WORKFLOW.md §6): no live flag, no key, storage somewhere harmless, and
    fixture mode pointed at a seeded temp dataset.

    Seeding matters: the AC is "fixture mode is the default", and a fixture
    mode that cannot answer proves nothing about whether the alternative was
    reached. Every fetchable query in the plan gets a real saved response, at
    exactly the signature `RentCastClient` looks it up by.
    """
    home = tmp_path / "rentcomp-home"
    home.mkdir()
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()

    for name in (LIVE_ENV, KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(home))
    monkeypatch.setenv(FIXTURES_DIR_ENV, str(fixtures))

    for index, query in enumerate(fetchable_queries(plan_pull_queries(TODAY, **SEARCH))):
        body = json.dumps([_record(index)]).encode("utf-8")
        (fixtures / f"{fixture_signature(query.params)}.json").write_bytes(body)

    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    load_shaped_pull.cache_clear()
    yield home
    load_shaped_pull.cache_clear()


@pytest.fixture
def booby_trapped_httpx(monkeypatch) -> list[str]:
    """Constructing ANY httpx client, or issuing any module-level httpx
    request, becomes an immediate loud failure.

    Stronger than watching for a socket: it trips the moment the code *decides*
    to build a transport, which is the decision D17 governs. (Same technique as
    `test_live_call_guard.py` — kept local rather than shared so neither file
    can silently weaken the other's guard.)
    """
    attempts: list[str] = []

    def _explode(kind: str):
        def _boom(*args, **kwargs):
            attempts.append(kind)
            raise AssertionError(
                f"the pull orchestrator constructed/used {kind} with no {LIVE_ENV}="
                f"{LIVE_VALUE} in the environment. D17: live mode requires BOTH the flag "
                "AND a key; the default is fixture mode, which never touches the network."
            )

        return _boom

    monkeypatch.setattr(httpx.Client, "__init__", _explode("httpx.Client"))
    monkeypatch.setattr(httpx.AsyncClient, "__init__", _explode("httpx.AsyncClient"))
    monkeypatch.setattr(httpx.HTTPTransport, "__init__", _explode("httpx.HTTPTransport"))
    monkeypatch.setattr(httpx, "request", _explode("httpx.request"), raising=False)
    monkeypatch.setattr(httpx, "get", _explode("httpx.get"), raising=False)
    return attempts


class RecordingTransport:
    """A transport that records and refuses. Nothing may ever reach it here."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json=[])


def _sources(*packages: str) -> list[Path]:
    files: list[Path] = []
    for package in packages:
        directory = BACKEND_SRC / package
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def _all_sources() -> list[Path]:
    return sorted(BACKEND_SRC.rglob("*.py"))


# ===========================================================================
# 1. structural — over the source, total over code paths no test walks
# ===========================================================================

#: The one module allowed to build a transport. Everything else must go
#: through `RentCastClient`, which is where the mode check lives.
TRANSPORT_OWNER = "rentcast.py"

_TRANSPORT_BUILDERS = {"Client", "AsyncClient", "HTTPTransport"}


def test_only_the_rentcast_client_may_build_a_transport() -> None:
    """AC4, structurally: one door, and it is the door D17 guards.

    F0-S4's guard says only `client/` may import httpx. That is no longer
    sufficient once a SECOND module lives in `client/`: an orchestrator that
    builds its own `httpx.Client` would satisfy that guard while routing
    around `RentCastClient._effective_mode` entirely — no flag check, no key
    check, no ledger increment, and a real call for every request.
    """
    offenders: list[str] = []
    for source in _sources("client"):
        if source.name == TRANSPORT_OWNER:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr in _TRANSPORT_BUILDERS:
                offenders.append(f"{source.name}: httpx.{target.attr}(...)")
            elif isinstance(target, ast.Name) and target.id in _TRANSPORT_BUILDERS:
                offenders.append(f"{source.name}: {target.id}(...)")

    assert not offenders, (
        "a module in client/ other than "
        f"{TRANSPORT_OWNER} builds its own HTTP transport: {'; '.join(sorted(set(offenders)))}. "
        "Every RentCast call goes through RentCastClient, which is the only place the D17 "
        "mode check, the key check and the ledger increment happen."
    )


def test_no_product_code_ever_sets_the_live_flag_for_itself() -> None:
    """AC4: the flag expresses the OWNER's consent to spend, so product code
    setting it would be the code consenting on the owner's behalf.

    Catches `os.environ[LIVE] = "1"` and `os.environ.setdefault(LIVE, "1")` —
    both of which would make every subsequent `resolve_mode()` in the process
    answer LIVE, including ones in code that never asked.
    """
    offenders: list[str] = []
    for source in _all_sources():
        text = source.read_text(encoding="utf-8")
        if LIVE_ENV not in text:
            continue
        tree = ast.parse(text, filename=str(source))
        for node in ast.walk(tree):
            written = False
            if isinstance(node, ast.Assign):
                written = any(
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Attribute)
                    and t.value.attr == "environ"
                    for t in node.targets
                )
            elif isinstance(node, ast.Call):
                func = node.func
                written = (
                    isinstance(func, ast.Attribute)
                    and func.attr in {"setdefault", "putenv", "update"}
                    and LIVE_ENV in ast.dump(node)
                )
            if written and LIVE_ENV in ast.dump(node):
                offenders.append(f"{source.relative_to(BACKEND_SRC)}:{node.lineno}")

    assert not offenders, (
        f"product code writes {LIVE_ENV} into the environment at {sorted(set(offenders))}. "
        "D17's flag is the owner's consent to spend money; code may read it, never set it."
    )


def test_the_orchestrator_does_not_read_the_wall_clock() -> None:
    """CONTRACT GUARD, not AC4 — flagged to the PM as such.

    The F4-S1 planner precedent is explicit: "Time is data here, not ambient
    state... a planner that read `date.today()` would be untestable at exactly
    the boundaries the AC cares about, and would make a cached plan depend on
    when it was replayed." The same applies with more force here, because the
    orchestrator's `today` also becomes the manifest's `as_of` — the pipeline's
    only "now" (owner ruling 1), the thing that decides which comps are
    censored.

    The API edge is where `date.today()` legitimately enters, and `api/` is
    deliberately not covered by this guard.
    """
    if not ORCHESTRATOR_SOURCE.exists():
        pytest.skip(f"{ORCHESTRATOR_SOURCE.name} does not exist yet (F4-S9 red)")

    tree = ast.parse(ORCHESTRATOR_SOURCE.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr in {"today", "now", "utcnow"}:
            offenders.append(f"line {node.lineno}: .{node.func.attr}()")

    assert not offenders, (
        f"{ORCHESTRATOR_SOURCE.name} reads the wall clock ({'; '.join(offenders)}). `today` "
        "arrives as an argument, like F4-S1's planner — it decides both the query windows "
        "and the manifest's as_of, and an ambient clock makes both unreproducible."
    )


# ===========================================================================
# 2. runtime — over the process
# ===========================================================================


@needs_orchestrator
def test_a_whole_pull_in_the_default_environment_builds_no_transport(
    default_env, booby_trapped_httpx, no_network
) -> None:
    """AC4, at runtime: the default is fixture mode, for a WHOLE pull.

    Not "one query did not dial out" — the entire plan runs, every window is
    served from saved responses, and nothing anywhere decides to build a
    transport. This is the state every test, dev server, QA run and Playwright
    run is in (WORKFLOW.md §6).
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=None)

    assert booby_trapped_httpx == [], (
        f"a transport was built in fixture mode: {booby_trapped_httpx}"
    )
    assert no_network == [], f"a socket was attempted in fixture mode: {no_network}"
    assert outcome.calls_spent == 0, (
        f"fixture mode reported {outcome.calls_spent} calls spent. Serving a saved response "
        "costs nothing and must never be counted against the month."
    )
    assert load_ledger().calls_this_month == 0, "fixture mode moved the call ledger"


@needs_orchestrator
def test_fixture_mode_still_mints_a_ref_the_loader_can_resolve(
    default_env, booby_trapped_httpx, no_network
) -> None:
    """AC4 x AC3: fixture mode is not a degraded mode.

    The dev server, every Playwright spec and every developer on a fresh clone
    run here. If a fixture-mode pull produced no usable `pull_ref`, the whole
    product would only work by spending money — which is exactly backwards.

    This also pins a real seam consequence: F0-S4's `sink` fires on the LIVE
    path only (`_fetch_from_fixtures` never emits), so the orchestrator has to
    persist fixture-mode pages itself rather than assuming the sink covers it.
    """
    outcome = run_pull(**SEARCH, today=TODAY, transport=None)

    pull = load_shaped_pull(outcome.pull_ref, Config())
    assert len(pull.comps) == 6, (
        f"a fixture-mode pull of 6 windows resolved to {len(pull.comps)} comps. The "
        "orchestrator must write fixture-mode responses to the cache too — F0-S4's sink "
        "only fires on the live path."
    )
    assert pull.as_of == TODAY
    assert booby_trapped_httpx == [] and no_network == []


@needs_orchestrator
def test_a_key_alone_does_not_enable_live_mode(
    default_env, booby_trapped_httpx, monkeypatch, no_network
) -> None:
    """D17 requires BOTH. The owner's key is present in `.env` on the machine
    this is developed on, so "key present" is the NORMAL state — if a key were
    sufficient, every dev run and every `pytest` would be a live run."""
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)

    outcome = run_pull(**SEARCH, today=TODAY, transport=None)

    assert outcome.calls_spent == 0
    assert load_ledger().calls_this_month == 0
    assert booby_trapped_httpx == [] and no_network == []


@needs_orchestrator
@pytest.mark.parametrize("value", ["", " ", "0", "no", "off", "false", "2"])
def test_only_the_flags_enabling_value_enables_live(
    default_env, booby_trapped_httpx, monkeypatch, value: str, no_network
) -> None:
    """Fail closed, at the orchestrator level too.

    `RENTCOMP_LIVE=0` read as "live" would be a money-costing misreading, and
    the orchestrator is the layer a user's launch script sets env vars for.
    """
    monkeypatch.setenv(LIVE_ENV, value)
    monkeypatch.setenv(KEY_ENV, FAKE_KEY)

    run_pull(**SEARCH, today=TODAY, transport=None)

    assert booby_trapped_httpx == [], (
        f"{LIVE_ENV}={value!r} was read as live mode by the orchestrator"
    )
    assert load_ledger().calls_this_month == 0


@needs_orchestrator
def test_injecting_a_transport_does_not_by_itself_enable_live_mode(
    default_env, no_network
) -> None:
    """AC4's new hazard, and the reason this file exists.

    `transport=` is the seam QA's own specs use to exercise the live path
    without spending. If an implementation reads "a transport was passed" as
    "the caller wants live", then D17 is enforced against the environment but
    not against the code — and the safety mechanism becomes the trigger.

    With no flag set, an injected transport must receive nothing at all.
    """
    recorder = RecordingTransport()

    run_pull(**SEARCH, today=TODAY, transport=recorder.transport)

    assert recorder.requests == [], (
        f"{len(recorder.requests)} requests reached an injected transport with no "
        f"{LIVE_ENV}={LIVE_VALUE} set. Injecting a transport is how a test avoids "
        "spending money; it must never be how live mode gets turned on."
    )
    assert load_ledger().calls_this_month == 0


@needs_orchestrator
def test_a_spent_budget_does_not_block_a_fixture_mode_pull(
    default_env, booby_trapped_httpx, no_network
) -> None:
    """AC5's boundary against AC4 — where the budget check must NOT be.

    A fixture-mode pull spends nothing, so the monthly cap has no claim on it.
    If the affordability check ran regardless of mode, the entire build would
    stop working at the end of any month the owner used their calls in: every
    Playwright spec, every dev server, every `pytest` run goes through this
    path (WORKFLOW.md §6), and none of them cost a penny.

    Pinned explicitly because the natural place to put the check — at the top
    of `run_pull`, before the mode is resolved — is the wrong one, and nothing
    would surface that until the ledger crossed the cap.
    """
    save_ledger(Ledger(month=current_month(), calls_this_month=999, history=()))
    assert load_ledger().remaining == 0

    outcome = run_pull(**SEARCH, today=TODAY, transport=None)

    assert outcome.calls_spent == 0
    assert outcome.complete is True, (
        "a fixture-mode pull was refused (or truncated) because the month's LIVE call "
        "budget is spent. Serving a saved response costs nothing; the cap governs the "
        "wire, not the disk."
    )
    assert booby_trapped_httpx == [] and no_network == []


@needs_orchestrator
def test_live_mode_without_a_key_fails_loudly_and_spends_nothing(
    default_env, booby_trapped_httpx, monkeypatch, no_network
) -> None:
    """AC4's second half. "Loudly" = a named exception, not a warning, not an
    empty result, and not an anonymous 401 from the far end — which would have
    cost a call to discover.

    The extra assertion beyond F0-S4's: a keyless live pull must not leave a
    cache entry behind that a later loader mistakes for a real, if thin, pull.
    A ref that resolves to zero comps reads downstream as "no comps in radius"
    and sends the user off widening a radius to fix a missing credential.
    """
    monkeypatch.setenv(LIVE_ENV, LIVE_VALUE)
    monkeypatch.delenv(KEY_ENV, raising=False)

    with pytest.raises(Exception) as excinfo:
        run_pull(**SEARCH, today=TODAY, transport=None)

    error = excinfo.value
    assert isinstance(error, RentCastError), (
        f"expected a typed RentCastError naming the missing key, got "
        f"{type(error).__name__}: {error}"
    )
    assert str(error).strip(), "the refusal carries no message — the user must be told what to fix"
    assert booby_trapped_httpx == [], (
        "a transport was built with the flag set but no key. The key check must precede the "
        "transport, or a keyless live run burns a call to learn it has no key."
    )
    assert load_ledger().calls_this_month == 0, "a refused pull moved the ledger"

    cache_root = default_env / "cache"
    entries = [p for p in cache_root.iterdir()] if cache_root.is_dir() else []
    assert entries == [], (
        f"a keyless live pull left cache entries behind: {[p.name for p in entries]}. "
        "An empty pull that resolves is indistinguishable downstream from a market with "
        "no comps in it."
    )


# ===========================================================================
# 3. backstop — over the runner
# ===========================================================================


def test_the_suite_is_not_running_in_live_mode(monkeypatch) -> None:
    """WORKFLOW.md §6: "All tests, all dev servers, all QA runs: fixture mode."

    Re-asserted for this story because F4-S9 is the first code that would
    actually spend the whole month's budget in one call if the flag leaked in
    from a shell — six calls per pull, unattended.
    """
    import os

    assert os.environ.get(LIVE_ENV) != LIVE_VALUE, (
        f"{LIVE_ENV}={LIVE_VALUE} is set in the environment running the test suite. Unset "
        "it — no test may run in live mode (WORKFLOW.md §6, 42 of 50 calls remaining)."
    )
