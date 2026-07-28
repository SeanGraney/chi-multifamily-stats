"""F0-S1a scaffold — shared QA fixtures.

Written by QA *before* implementation (WORKFLOW.md §2), so every import of
the product package happens lazily inside fixtures/tests. A missing package
(or missing fastapi, since `pip install -e .` is the developer's step) must
produce a legible per-test failure message, never a collection error.

App-object location: pinned to the canonical **`rentcomp.app:app`**
(module-level object built by `create_app()` in the same module) per the
F0-S1a contract ruling relayed by the PM — this replaces the original
pre-implementation candidate-list resolver. If this import fails, that is a
contract break, not a lookup miss.

F0-S4 addition — the suite-wide network kill switch
---------------------------------------------------
`no_network` below is autouse for the WHOLE backend suite: every outbound
socket connection raises. WORKFLOW.md §6 is a hard money constraint (50
calls/month, 42 left) and D17's guarantee is "the client cannot reach the
network without the flag" — a guarantee that is only worth what the weakest
test honours it. This makes an accidental live call impossible from a test
that forgot to inject a transport, instead of merely unlikely.

It is deliberately a *backstop*, not the AC's proof: the AC is asserted
structurally in `tests/unit/test_live_call_guard.py`. A test that genuinely
needs to exercise the live code path injects an `httpx.MockTransport` (the
pattern `scripts/tests/test_gate.py` established), which never opens a
socket, so the switch is invisible to it.
"""

from __future__ import annotations

import importlib
import ipaddress
import socket

import pytest

_SCAFFOLD_HINT = (
    "F0-S1a scaffold broken: {problem} "
    "(expected after `pip install -e backend/` into the repo-root .venv)"
)


def _resolve_app():
    try:
        module = importlib.import_module("rentcomp.app")
    except ImportError as exc:
        pytest.fail(
            _SCAFFOLD_HINT.format(
                problem=f"cannot import 'rentcomp.app' — canonical app module ({exc})"
            )
        )
    candidate = getattr(module, "app", None)
    if candidate is None:
        pytest.fail(
            _SCAFFOLD_HINT.format(
                problem="'rentcomp.app' has no module-level 'app' object "
                "(canonical contract is rentcomp.app:app)"
            )
        )
    return candidate


@pytest.fixture(scope="session")
def app():
    """The scaffold's FastAPI application object."""
    return _resolve_app()


@pytest.fixture(scope="session")
def client(app):
    """FastAPI TestClient — Layer 2 (D21). No server bound, no network."""
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:
        pytest.fail(
            _SCAFFOLD_HINT.format(problem=f"fastapi not importable from .venv ({exc})")
        )
    return TestClient(app)


class NetworkUseInTestsError(RuntimeError):
    """Raised when a test tries to open a socket (WORKFLOW.md §6: zero live
    API calls, ever, from the suite)."""


#: Set by the kill switch every time something tried to dial out, so a test
#: can assert *that* an attempt was made without ever letting it complete.
network_attempts: list[str] = []


def _is_loopback(address: object) -> bool:
    """True for a host-local address, which by construction cannot reach RentCast.

    The kill switch exists to stop *outbound* calls (WORKFLOW.md §6 — real
    money), and no loopback packet leaves the machine. Letting these through
    is not a hole in the guard; blocking them is a portability bug:

    Windows has no `AF_UNIX` socketpair, so CPython's `socket.socketpair()`
    falls back to a real 127.0.0.1 TCP connect — and asyncio's
    `ProactorEventLoop` builds its self-pipe that way for *every* loop it
    creates. Blocking loopback therefore kills `TestClient` itself before a
    request is ever made, failing the whole API suite on Windows while
    catching nothing. POSIX never hits this path, which is why it went unseen.
    """
    if not isinstance(address, tuple) or not address:
        return False  # AF_UNIX path or similar — not an IP dial-out
    host = address[0]
    if not isinstance(host, str):
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Autouse, every test: outbound sockets raise.

    A RentCast call costs real money against a 50/month cap. This is the
    backstop that makes "zero live API calls" a property of the runner rather
    than of every individual test author's memory. `httpx.MockTransport`
    short-circuits above the socket layer, so mocked-transport tests are
    unaffected.
    """
    del network_attempts[:]

    # Captured before patching so loopback can be passed through to the real
    # implementation rather than reimplemented.
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_create_connection = socket.create_connection

    def _blocked(target: object) -> "NetworkUseInTestsError":
        network_attempts.append(repr(target))
        return NetworkUseInTestsError(
            f"a test tried to open a network connection to {target!r}. The backend suite "
            "runs in fixture mode with zero live API calls (WORKFLOW.md §6 — 50 calls/month, "
            "and every one of them is the owner's). Inject an httpx.MockTransport instead."
        )

    def _connect(self, address, *args, **kwargs):  # noqa: ANN001
        if _is_loopback(address):
            return real_connect(self, address, *args, **kwargs)
        raise _blocked(address)

    def _connect_ex(self, address, *args, **kwargs):  # noqa: ANN001
        if _is_loopback(address):
            return real_connect_ex(self, address, *args, **kwargs)
        raise _blocked(address)

    def _create_connection(address, *args, **kwargs):  # noqa: ANN001
        if _is_loopback(address):
            return real_create_connection(address, *args, **kwargs)
        raise _blocked(address)

    monkeypatch.setattr(socket.socket, "connect", _connect, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _connect_ex, raising=True)
    monkeypatch.setattr(socket, "create_connection", _create_connection, raising=True)
    yield network_attempts
