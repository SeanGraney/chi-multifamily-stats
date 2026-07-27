"""F0-S1a — developer supporting tests (Layer 1): launch wiring for D7.

QA's structural tests prove the console script is declared and registered;
these pin what launching actually *does* — without ever binding a port
(D21: no server in tests). Written by the developer, committed on
story/F0-S1a; QA's pre-written files stay uncommitted here.
"""

from __future__ import annotations


def test_main_serves_canonical_app_on_localhost_8000(monkeypatch):
    """`rentcomp` / `python -m rentcomp` must serve rentcomp.app:app on
    localhost:8000 (D7), loopback only (D1). uvicorn.run is stubbed so no
    socket is ever bound."""
    import rentcomp.__main__ as launch

    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(launch.uvicorn, "run", lambda *a, **kw: calls.append((a, kw)))

    launch.main()

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ("rentcomp.app:app",)
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 8000


def test_console_script_and_dash_m_share_one_entry_point():
    """The [project.scripts] target is rentcomp.__main__:main — the same
    function `python -m rentcomp` executes — so there is exactly one launch
    code path to maintain (D7)."""
    from importlib.metadata import entry_points

    import rentcomp.__main__ as launch

    (ep,) = [e for e in entry_points(group="console_scripts") if e.name == "rentcomp"]
    assert ep.load() is launch.main
