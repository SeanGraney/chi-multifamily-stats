"""F0-S1a — developer supporting tests (Layer 2): built-UI serving (D7).

QA's contract covers the no-frontend-build case (F0-S1a merges before
F0-S1b exists). These cover the other half of D7 — when a built UI *is*
present, the app serves it — using a temp directory as the build output so
the tests are valid before F0-S1b lands. TestClient only; no port bound.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from rentcomp.app import UI_DIR_ENV, create_app

INDEX_HTML = "<!doctype html><html><body>rentcomp ui</body></html>"


def _app_with_built_ui(tmp_path, monkeypatch) -> FastAPI:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(INDEX_HTML)
    assets = dist / "assets"
    assets.mkdir()
    (assets / "main.js").write_text("console.log('rentcomp')")
    monkeypatch.setenv(UI_DIR_ENV, str(dist))
    return create_app()


def test_create_app_is_the_canonical_factory():
    """create_app() builds a fresh FastAPI instance per call — app assembly
    lives in rentcomp.app, above the routers ([DEFAULT] choice, see
    rentcomp/app.py docstring)."""
    first, second = create_app(), create_app()
    assert isinstance(first, FastAPI)
    assert first is not second


def test_built_ui_is_served_at_root(tmp_path, monkeypatch):
    client = TestClient(_app_with_built_ui(tmp_path, monkeypatch))

    response = client.get("/")
    assert response.status_code == 200
    assert "rentcomp ui" in response.text

    asset = client.get("/assets/main.js")
    assert asset.status_code == 200


def test_static_mount_does_not_shadow_openapi_schema(tmp_path, monkeypatch):
    """D12: even with the UI mounted at `/`, /openapi.json must keep serving
    the schema — F0-S1b's codegen consumes it."""
    client = TestClient(_app_with_built_ui(tmp_path, monkeypatch))

    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert str(response.json().get("openapi", "")).startswith("3.")


def test_placeholder_root_is_excluded_from_openapi_schema():
    """Without a built UI the `/` placeholder must not pollute the schema —
    codegen (D12) should see only real API routes."""
    client = TestClient(create_app())
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths
