"""F3-S4 fixtures — an isolated storage root, and the two modes.

Scoped to this directory (the `backend/tests/api/f1s2/` precedent) rather than
added to `backend/tests/unit/`'s namespace: nothing here should be able to
change how another story's tests run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from f3s4_support import FAKE_KEY

from rentcomp.storage.ledger import Ledger, current_month, save_ledger
from rentcomp.storage.pulls import load_shaped_pull


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """An isolated `RENTCOMP_HOME`, with the record-shaping memo cleared either
    side — these tests rewrite the store in place (ADR-001 §3).

    The memo is deliberately NOT cleared *during* a test unless the test says
    so: several of the staleness bugs this story is about only exist while it
    is warm, and a stray `cache_clear()` would make those tests pass against
    the defect.
    """
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    load_shaped_pull.cache_clear()
    yield root
    load_shaped_pull.cache_clear()


@pytest.fixture
def fixture_mode(home, tmp_path, monkeypatch) -> Path:
    """Fixture mode (the default, and the mode the whole product runs in for
    the build): saved responses in a temp directory, `RENTCOMP_LIVE` explicitly
    unset. Returns the fixtures directory."""
    fixtures = tmp_path / "live-samples"
    fixtures.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_FIXTURES_DIR", str(fixtures))
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return fixtures


@pytest.fixture
def live_env(home, monkeypatch) -> Path:
    """The live path, with no way out to the network.

    `RENTCOMP_LIVE=1` + a fake key is the only combination in which calls are
    countable at all — a fixture-mode pull never touches the ledger, so
    "exactly one call" is not even expressible there. Every test using this
    fixture also injects a `MockTransport` and asserts `no_network` saw
    nothing.
    """
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    save_ledger(Ledger(month=current_month(), calls_this_month=0, history=()))
    return home


@pytest.fixture
def no_sockets(no_network):
    """The money assertion, as a fixture: nothing in this directory may reach
    for a real socket, whatever else it asserts."""
    yield no_network
    assert no_network == [], (
        f"a test reached for a real socket instead of its injected MockTransport: "
        f"{no_network}. WORKFLOW.md §6 — zero live API calls, ever, from the suite."
    )
