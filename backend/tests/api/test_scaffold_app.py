"""F0-S1a scaffold — Layer 2 API-contract tests (QA, written pre-implementation).

FastAPI TestClient only (D21): no server bound to :8000, zero network. The
app object is resolved by tests/conftest.py.

Contract pinned here, and nothing more:
- The OpenAPI schema route serves a valid schema — this is exactly what
  F0-S1b's `openapi-typescript` codegen consumes (D12).
- The app is importable and functional with NO built frontend present
  (F0-S1a merges before F0-S1b exists, so this is a hard sequencing
  requirement implied by D7's serve-together decision, not a nice-to-have).
- Unknown routes fail cleanly (404), not with a server error.

Deliberately NOT asserted: any /api/* endpoint (F0-S2+ own those; the docs
name none for this story), a health route (docs don't name one), or what `/`
serves when a built UI IS present (that's F0-S1b's static-consumption story
and later E2E).
"""

from __future__ import annotations


def test_app_is_a_fastapi_application(app):
    from fastapi import FastAPI

    assert isinstance(app, FastAPI), (
        f"resolved app object is {type(app)!r}, expected a fastapi.FastAPI instance"
    )


def test_openapi_schema_route_serves_valid_schema(client):
    """GET /openapi.json → 200 with a well-formed OpenAPI 3 document.

    Also proves the static-UI mount does not shadow the schema route even
    when the built-frontend directory is absent.
    """
    response = client.get("/openapi.json")
    assert response.status_code == 200, (
        f"/openapi.json returned {response.status_code} — F0-S1b's codegen (D12) "
        "depends on this route"
    )
    schema = response.json()
    assert str(schema.get("openapi", "")).startswith("3."), (
        f"expected an OpenAPI 3.x document, got version {schema.get('openapi')!r}"
    )
    assert "info" in schema
    assert isinstance(schema.get("paths"), dict)


def test_root_survives_missing_frontend_build(client):
    """With no built UI on disk, GET / must respond cleanly (404 or a
    placeholder are both acceptable) — never a 5xx, and the app must not
    have crashed at import/startup. The client fixture resolving at all
    covers startup; this covers request handling."""
    response = client.get("/")
    assert response.status_code < 500, (
        f"GET / returned {response.status_code} with no frontend build present — "
        "the backend must be fully runnable before F0-S1b exists"
    )


def test_unknown_api_route_is_a_clean_404(client):
    response = client.get("/api/definitely-not-a-route-f0s1a")
    assert response.status_code == 404, (
        f"unknown /api path returned {response.status_code}, expected a clean 404"
    )
