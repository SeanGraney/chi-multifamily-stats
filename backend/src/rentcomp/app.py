"""Application assembly — the canonical home of the FastAPI app object.

Canonical location (F0-S1a [DEFAULT] choice): ``rentcomp.app:app``, built by
the ``create_app()`` factory in this module. Rationale: ARCHITECTURE.md §2
gives ``rentcomp/api/`` to *routers*; assembling the application (wiring
routers together and mounting the built UI per D7) is a concern one level
above the routers, and putting it in ``rentcomp/__init__.py`` would make a
web app an import side effect of the package itself.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from rentcomp.api.config import router as config_router
from rentcomp.api.derive import router as derive_router
from rentcomp.api.plan import router as plan_router
from rentcomp.api.search import router as search_router
from rentcomp.api.settings import router as settings_router

#: Env var that overrides where the built frontend is looked up. Used by
#: tests (point it at a temp dir) and available to E2E harnesses; normal
#: launches never need it.
UI_DIR_ENV = "RENTCOMP_UI_DIR"


def _ui_dist_dir() -> Path:
    """Directory holding the built frontend (F0-S1b's `frontend/dist`).

    This tool is always run from an editable install in its own repo (D1,
    D7), so repo-relative resolution from the package location is the
    intended path; the env override exists for tests and harnesses.
    """
    override = os.environ.get(UI_DIR_ENV)
    if override:
        return Path(override)
    # __file__ = <repo>/backend/src/rentcomp/app.py → parents[3] = <repo>
    return Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app() -> FastAPI:
    """Assemble the RentComp application.

    API routers from ``rentcomp.api`` are included here as later stories
    add them (F0-S2 `/api/derive`, F4-S9 `/api/search`, ...). The built UI,
    when present, is mounted at ``/`` *after* the API routes so it can never
    shadow ``/openapi.json`` (which F0-S1b's type codegen consumes, D12) or
    any ``/api/*`` route.
    """
    app = FastAPI(title="RentComp")

    # Routers first, mount last — a Starlette mount at "/" swallows everything
    # registered after it, so an API route added below the mount 404s the
    # moment a UI build exists on disk (ADR-001 §4).
    app.include_router(derive_router)
    app.include_router(search_router)
    # F2-S3's non-spending preview. A separate module from `search_router` on
    # purpose (`api/plan.py`): the guarantee that a live-updating form cannot
    # buy anything is carried by that module's import list, and merging the two
    # would put `run_pull` back within reach of the preview handler.
    app.include_router(plan_router)
    app.include_router(config_router)
    app.include_router(settings_router)

    dist = _ui_dist_dir()
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    else:
        # F0-S1a merges before the frontend exists; the backend must be
        # fully runnable on its own. Placeholder is excluded from the
        # OpenAPI schema so codegen sees only real API routes.
        @app.get("/", include_in_schema=False)
        def ui_not_built() -> dict[str, str]:
            return {
                "service": "rentcomp",
                "ui": "not built — run the frontend build (F0-S1b), then restart",
                "openapi": "/openapi.json",
            }

    return app


#: The application object servers and tests resolve (`rentcomp.app:app`).
app = create_app()
