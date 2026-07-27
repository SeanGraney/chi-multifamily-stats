"""F0-S4 [INVARIANT] — D17's live-call guard, asserted STRUCTURALLY.

QA-authored, written RED before any implementation exists (AGENT_QA.md
protocol).

THE AC THIS FILE OWNS
---------------------
    "with no env flag set, the client cannot reach the network (asserted by
     test); live mode without a key fails loudly"

"Cannot" is a strong word and it is the right one: 42 of the 50 monthly
RentCast calls remain and they are the owner's, not the build's. A test that
merely walks one happy path and observes no request proves nothing about the
path it did not walk — so this file asserts the guarantee three ways, each
total in a different dimension:

1. **Over the source (AST).** Only `client/` may reach for a network library
   at all, and no request may be issued at import time. Total over code paths,
   including ones no test exercises.
2. **Over the process (runtime).** With `httpx`'s client constructor booby-
   trapped, every public entry point of the client is driven under every
   env combination that is not "flag AND key". Nothing constructs a transport;
   nothing opens a socket.
3. **Over the runner (backstop).** `tests/conftest.py`'s autouse `no_network`
   fixture makes an outbound socket raise for the entire backend suite. The
   meta-test here keeps that backstop honest — a kill switch nobody tests is a
   kill switch that quietly stops working.

WHY LAYER 1 (AGENT_QA.md decision procedure)
--------------------------------------------
Step 1 matches: these import a module and call it with plain values. Nothing
here needs HTTP, and none of it is observable from a browser — the same
reasoning that puts D19a's target-leakage guard and F0-S5's config-read guard
at this layer (`tests/unit/test_derivation_graph.py`,
`tests/unit/test_config_store.py`).

SCOPE NOTE — ADR-001 ERRATUM E1. The no-I/O AST guard in
`test_derivation_graph.py` deliberately EXCLUDES `client/`, because fixture
mode reads fixture files from disk by design. This file is the narrower
replacement for `client/`: it bans *network* reach outside `client/` and
un-flagged network reach inside it, never file I/O.

MODULE PATHS PINNED HERE ARE QA-PROPOSED (the story text names no module).
See `test_rentcast_client.py`'s docstring for the full proposed contract;
the PM confirms before they count as locked.

EXPECTED RED STATE TODAY: `test_client_module_exists` FAILS with the
ImportError detail; the AST guards and the kill-switch meta-tests PASS
(vacuously, or against the runner); every runtime guard SKIPS until the
client exists, then runs for real.
"""

from __future__ import annotations

import ast
import os
import socket
from pathlib import Path

import httpx
import pytest

BACKEND_SRC = Path(__file__).resolve().parents[2] / "src" / "rentcomp"

#: D17. The flag's name and its enabling value are both fixed.
LIVE_ENV = "RENTCOMP_LIVE"
LIVE_VALUE = "1"
#: D16. The key's env var.
KEY_ENV = "RENTCAST_API_KEY"

#: Never a real key. If this string ever reaches a socket, the socket is
#: already blocked; if it ever reaches a log, `test_the_api_key_is_never_
#: printed_or_persisted` fails.
FAKE_KEY = "qa-fake-key-not-a-real-credential"

try:
    from rentcomp.client.rentcast import (
        LiveModeDisabledError,
        MissingApiKeyError,
        Mode,
        RentCastClient,
        RentCastError,
        resolve_mode,
    )

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - red state before F0-S4 lands
    _IMPORT_ERROR = _exc

needs_client = pytest.mark.skipif(
    _IMPORT_ERROR is not None,
    reason=f"the RentCast client is not importable yet (F0-S4 red): {_IMPORT_ERROR}",
)


def test_client_module_exists() -> None:
    """Legible red: one failure that names exactly what is missing."""
    assert _IMPORT_ERROR is None, (
        "Cannot import the RentCast client (QA-proposed path "
        f"rentcomp.client.rentcast — see the handoff): {_IMPORT_ERROR}"
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def package_sources(*packages: str) -> list[Path]:
    """Every .py file under the named subpackages of `rentcomp`."""
    files: list[Path] = []
    for package in packages:
        directory = BACKEND_SRC / package
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def all_sources() -> list[Path]:
    return sorted(BACKEND_SRC.rglob("*.py"))


def imported_modules(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.fixture
def clean_env(monkeypatch):
    """No live flag, no key, storage pointed somewhere harmless.

    This is the DEFAULT state of every dev machine, every test run and every
    QA run (WORKFLOW.md §6) — so it is the state the AC is about.
    """

    def _clean(tmp: Path, **overrides: str):
        for name in (LIVE_ENV, KEY_ENV, "RENTCOMP_MODE"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("RENTCOMP_HOME", str(tmp))
        for name, value in overrides.items():
            monkeypatch.setenv(name, value)

    return _clean


@pytest.fixture
def booby_trapped_httpx(monkeypatch):
    """Make constructing ANY httpx client, or issuing any module-level httpx
    request, an immediate loud failure.

    Stronger than watching for a socket: it trips at the moment the code
    *decides* to build a transport, which is the decision D17 governs. Returns
    the (empty) list of attempts so a test can also assert the negative.
    """
    attempts: list[str] = []

    def _explode(kind: str):
        def _boom(*args, **kwargs):
            attempts.append(kind)
            raise AssertionError(
                f"the RentCast client constructed/used {kind} with no {LIVE_ENV}={LIVE_VALUE} "
                "in the environment. D17: live mode requires BOTH the flag AND a key; the "
                "default is fixture mode, which never touches the network."
            )

        return _boom

    monkeypatch.setattr(httpx.Client, "__init__", _explode("httpx.Client"))
    monkeypatch.setattr(httpx.AsyncClient, "__init__", _explode("httpx.AsyncClient"))
    monkeypatch.setattr(httpx, "request", _explode("httpx.request"), raising=False)
    monkeypatch.setattr(httpx, "get", _explode("httpx.get"), raising=False)
    monkeypatch.setattr(httpx.HTTPTransport, "__init__", _explode("httpx.HTTPTransport"))
    return attempts


#: A params dict the gate really sent (fixtures/live-samples/ledger.json,
#: sig fe9de5158f036802). Used so the guards drive the client with a query
#: that a *correct* fixture mode can actually answer.
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


# ---------------------------------------------------------------------------
# 1. structural — over the source
# ---------------------------------------------------------------------------

#: `http` (as in `from http import HTTPStatus`) is deliberately absent — it is
#: a constants module, not a way out to the network.
NETWORK_MODULES = {"httpx", "requests", "urllib", "urllib3", "socket", "aiohttp"}

#: Only these may name a network library. `client/` is the sanctioned edge;
#: nothing else in the package has any business dialing out.
NETWORK_ALLOWED_PACKAGES = {"client"}


def test_only_the_client_package_can_reach_the_network() -> None:
    """One door, and it is the door D17 guards.

    ADR-001 §4.2's I/O guard covers `pipeline/` and `stats/` and (ERRATUM E1)
    deliberately exempts `client/` because fixture mode reads files. This is
    the complementary guard: file I/O in `client/` is fine, *network* reach
    anywhere else is not — otherwise a later story could add an httpx call in
    `pipeline/` or `api/` that bypasses the mode check entirely.
    """
    offenders: list[str] = []
    for source in all_sources():
        package = source.relative_to(BACKEND_SRC).parts[0] if len(
            source.relative_to(BACKEND_SRC).parts
        ) > 1 else ""
        if package in NETWORK_ALLOWED_PACKAGES:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for module in imported_modules(tree) & NETWORK_MODULES:
            offenders.append(f"{source.relative_to(BACKEND_SRC)}: imports {module}")
    assert not offenders, (
        "a module outside rentcomp/client/ can reach the network: "
        + "; ".join(sorted(set(offenders)))
        + ". Every RentCast call goes through the one mode-guarded client (D17)."
    )


REQUEST_METHODS = {"request", "send", "stream"}


def test_no_request_is_issued_at_import_time() -> None:
    """Importing the package must be free.

    `rentcomp.app` imports the package on every `rentcomp` launch, every
    TestClient session and every `import rentcomp` in a REPL. A request at
    module scope would spend a call before anyone chose anything — the exact
    inverse of the cost invariant ("no API call without explicit user
    consent").
    """
    offenders: list[str] = []
    for source in package_sources("client"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = getattr(target, "attr", None) or getattr(target, "id", None)
            if name in REQUEST_METHODS | {"Client", "AsyncClient"} or (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "httpx"
            ):
                if _is_module_scope(tree, node):
                    offenders.append(f"{source.name}: {name}() at module scope")
    assert not offenders, (
        "the client issues a network call (or builds a transport) at import time: "
        + "; ".join(sorted(set(offenders)))
    )


def _is_module_scope(tree: ast.AST, target: ast.AST) -> bool:
    """True when `target` is not nested inside any function or class body."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for descendant in ast.walk(node):
                if descendant is target:
                    return False
    return True


def test_the_live_flag_is_checked_in_the_client() -> None:
    """The flag must actually appear in the code that can dial out.

    Deliberately weak on its own — a string match proves nothing about
    ordering — which is why it exists only to catch the degenerate case
    (`client/` grew a network path and never mentions the flag at all). The
    real proof is the runtime section below.
    """
    sources = package_sources("client")
    if not sources or not any(
        "httpx" in imported_modules(ast.parse(s.read_text(encoding="utf-8"))) for s in sources
    ):
        pytest.skip("no network-capable module in client/ yet — nothing to guard")
    blob = "\n".join(s.read_text(encoding="utf-8") for s in sources)
    assert LIVE_ENV in blob, (
        f"client/ can reach httpx but never mentions {LIVE_ENV} — D17's flag is not part of "
        "the decision to send"
    )


# ---------------------------------------------------------------------------
# 2. runtime — over the process
# ---------------------------------------------------------------------------


@needs_client
def test_the_default_mode_is_fixture(tmp_path, clean_env) -> None:
    """[INVARIANT] "fixture (default) ... live (explicit env flag + key
    required; never default)"."""
    clean_env(tmp_path)
    assert resolve_mode() is Mode.FIXTURE
    assert Mode.FIXTURE.value == "fixture" and Mode.LIVE.value == "live"


@needs_client
def test_a_key_alone_does_not_enable_live_mode(tmp_path, clean_env) -> None:
    """D17 requires BOTH. The owner's key is present in `.env` on the machine
    this is developed on, so "key present" is the NORMAL state — if a key were
    sufficient, every dev run would be a live run."""
    clean_env(tmp_path, **{KEY_ENV: FAKE_KEY})
    assert resolve_mode() is Mode.FIXTURE


@needs_client
@pytest.mark.parametrize("value", ["", " ", "0", "no", "off", "false", "False", "FALSE", "2"])
def test_only_the_flags_enabling_value_enables_live(tmp_path, clean_env, value: str) -> None:
    """Fail closed. `RENTCOMP_LIVE=0` meaning "live" would be a $-costing
    misreading; `RENTCOMP_LIVE=1` is the literal spelling D17 gives.

    (`"true"`/`"yes"` are deliberately NOT asserted either way — whether the
    flag parser accepts extra affirmative spellings is the developer's call;
    what is locked is that nothing non-affirmative turns it on.)
    """
    clean_env(tmp_path, **{LIVE_ENV: value, KEY_ENV: FAKE_KEY})
    assert resolve_mode() is Mode.FIXTURE, (
        f"{LIVE_ENV}={value!r} was read as live mode"
    )


@needs_client
@pytest.mark.parametrize(
    "env",
    [
        {},
        {KEY_ENV: FAKE_KEY},
        {LIVE_ENV: "0", KEY_ENV: FAKE_KEY},
        {LIVE_ENV: "", KEY_ENV: FAKE_KEY},
    ],
    ids=["nothing-set", "key-only", "flag-off", "flag-empty"],
)
def test_no_transport_is_ever_built_without_the_flag(
    tmp_path, clean_env, booby_trapped_httpx, env: dict
) -> None:
    """THE AC, at runtime: with no flag set, the client cannot reach the
    network — whatever it is asked to do.

    A fixture-mode fetch either answers from `fixtures/live-samples/` or fails
    with a fixture-missing error; what it may never do is build a transport.
    """
    clean_env(tmp_path, **env)
    client = RentCastClient()
    try:
        client.fetch_listings(dict(REAL_QUERY))
    except RentCastError:
        pass  # a *typed* refusal/miss is fine; a network attempt is not
    try:
        client.fetch_listings({**REAL_QUERY, "daysOld": "1:2"})
    except RentCastError:
        pass
    assert booby_trapped_httpx == [], f"a transport was built in fixture mode: {booby_trapped_httpx}"


@needs_client
def test_asking_for_live_mode_explicitly_without_the_flag_is_refused(
    tmp_path, clean_env, booby_trapped_httpx
) -> None:
    """Constructing `RentCastClient(mode=Mode.LIVE)` in code must not be a way
    around the env flag — otherwise D17 is enforced only against the
    environment, not against the next story's developer."""
    clean_env(tmp_path, **{KEY_ENV: FAKE_KEY})
    with pytest.raises(RentCastError) as excinfo:
        RentCastClient(mode=Mode.LIVE).fetch_listings(dict(REAL_QUERY))
    assert isinstance(excinfo.value, LiveModeDisabledError), (
        f"expected LiveModeDisabledError, got {type(excinfo.value).__name__} — an in-code "
        f"request for live mode without {LIVE_ENV}={LIVE_VALUE} must be refused by name"
    )
    assert booby_trapped_httpx == []


@needs_client
def test_live_mode_without_a_key_fails_loudly(tmp_path, clean_env, booby_trapped_httpx) -> None:
    """THE AC's second half. "Loudly" = a named exception, not a warning, not
    an empty result, and not an anonymous 401 from the far end (which would
    have cost a call to discover)."""
    clean_env(tmp_path, **{LIVE_ENV: LIVE_VALUE})
    with pytest.raises(RentCastError) as excinfo:
        RentCastClient().fetch_listings(dict(REAL_QUERY))
    assert isinstance(excinfo.value, MissingApiKeyError), (
        f"expected MissingApiKeyError, got {type(excinfo.value).__name__}"
    )
    message = str(excinfo.value)
    assert message.strip(), "MissingApiKeyError carries no message — the user must be told what to fix"
    assert booby_trapped_httpx == [], (
        "a transport was built with the flag set but no key — the key check must precede the "
        "transport, or a keyless live run burns a call to learn it has no key"
    )


@needs_client
def test_fixture_mode_needs_no_key_at_all(tmp_path, clean_env) -> None:
    """The whole build runs keyless in CI and on a fresh clone. Fixture mode
    demanding a key would make `pytest` fail for anyone without a credential —
    and would tempt someone to set one."""
    clean_env(tmp_path)
    result = RentCastClient().fetch_listings(dict(REAL_QUERY))
    assert result is not None


# ---------------------------------------------------------------------------
# 3. backstop — over the runner
# ---------------------------------------------------------------------------


def test_the_suites_network_kill_switch_is_active() -> None:
    """Meta-test for `tests/conftest.py::no_network`. If someone removes the
    autouse fixture, this fails rather than the protection silently lapsing."""
    with pytest.raises(Exception) as excinfo:
        socket.create_connection(("api.rentcast.io", 443), timeout=0.001)
    assert "fixture mode" in str(excinfo.value) or "network" in str(excinfo.value).lower(), (
        f"sockets are not blocked by the suite's kill switch (got {excinfo.value!r})"
    )

    with pytest.raises(Exception):
        s = socket.socket()
        s.connect(("api.rentcast.io", 443))


def test_the_live_flag_is_not_set_in_this_process() -> None:
    """WORKFLOW.md §6: "All tests, all dev servers, all QA runs: fixture
    mode." A suite that runs with `RENTCOMP_LIVE=1` inherited from the shell
    is one forgotten mock away from spending the owner's calls."""
    assert os.environ.get(LIVE_ENV) != LIVE_VALUE, (
        f"{LIVE_ENV}={LIVE_VALUE} is set in the environment running the test suite. "
        "Unset it — no test may run in live mode (WORKFLOW.md §6)."
    )


@needs_client
def test_the_api_key_is_never_printed_or_persisted(tmp_path, clean_env, capsys) -> None:
    """CLAUDE.md: "Don't read, print, or log it." A key in a traceback or a
    ledger entry ends up in a PM report, a commit, or this transcript."""
    clean_env(tmp_path, **{KEY_ENV: FAKE_KEY})
    client = RentCastClient()
    try:
        client.fetch_listings(dict(REAL_QUERY))
        client.fetch_listings({**REAL_QUERY, "daysOld": "nope"})
    except RentCastError as exc:
        assert FAKE_KEY not in str(exc) and FAKE_KEY not in repr(exc), (
            "the API key appears in an exception message"
        )

    captured = capsys.readouterr()
    assert FAKE_KEY not in captured.out and FAKE_KEY not in captured.err, (
        "the API key was written to stdout/stderr"
    )
    for path in tmp_path.rglob("*"):
        # secrets.json is the ONE file allowed to hold a key (D16) — and only
        # when the user explicitly saves one, which this test never does.
        if path.is_file() and path.name != "secrets.json":
            assert FAKE_KEY not in path.read_text(encoding="utf-8", errors="ignore"), (
                f"the API key was written to {path} — the ledger, the cache and every log line "
                "must be key-free (D16 / CLAUDE.md)"
            )
