"""Settings API-key entry — Layer 2 contract tests (PM ruling 2 on F2-S1).

QA-authored, written RED before any implementation exists.

WHY THIS STORY OWNS IT
-----------------------
F0-S4's AC reads "API key entered in Settings, stored locally, sent as
`X-Api-Key`". F0-S4 built and verified the middle and last clauses
(`storage/secrets.py`, `client/rentcast.py`); the *entry* clause had no
surface to verify against — no Settings screen existed — and QA correctly
refused to close it. The PM reassigned that half-AC here (QUEUE row 35,
2026-07-27 log).

Entry needs a route: the SPA cannot write `~/.rentcomp/secrets.json`. Nothing
in ARCHITECTURE.md §4 names one, so this file pins one (QA-PROPOSED, needs PM
confirmation): ``PUT /api/settings/api-key`` with body ``{"api_key": "..."}``,
plus ``GET`` on the same path reporting **presence only**. Both overridable via
``RENTCOMP_TEST_API_KEY_PATH``.

THE ONE INVARIANT THAT IS NOT NEGOTIABLE HERE
----------------------------------------------
**No route ever returns the key.** Not masked, not partially, not "just the
last four" — presence is a boolean. A local-only tool still writes its
responses into browser devtools, HAR files, Playwright traces (this repo keeps
them on failure) and any log a future story adds. `storage/secrets.py` already
holds the line on its side ("nothing here ever returns the key to a log, a
traceback or a repr"); a read-back route would hand it straight out of the
front door instead.

NO REAL CREDENTIAL EVER TOUCHES THIS FILE (CLAUDE.md: secrets never enter the
repo). `FAKE_KEY` below is a self-describing non-credential, it is written only
into a per-test temp `RENTCOMP_HOME`, and it is never printed — the assertions
say *whether* it appears in a response, never echo it.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

#: The key-entry route (QA-proposed, see the module docstring).
KEY_PATH = os.environ.get("RENTCOMP_TEST_API_KEY_PATH", "/api/settings/api-key")

#: Obviously not a credential, and shaped so a grep for it in a diff or a log
#: reads as an alarm rather than as a plausible key.
FAKE_KEY = "rc-fake-test-key-NOT-A-CREDENTIAL-0000"

API_KEY_ENV = "RENTCAST_API_KEY"


def route_present(app: Any, path: str, method: str) -> bool:
    """True when `app` exposes `method path` (see `tests/api/conftest.py`)."""
    try:
        operations = (app.openapi().get("paths") or {}).get(path) or {}
    except Exception:  # pragma: no cover - defensive
        operations = {}
    if method.lower() in operations:
        return True
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) != path:
            continue
        if method in (getattr(route, "methods", None) or {method}):
            return True
    return False


@pytest.fixture
def client(app, rentcomp_home, monkeypatch):
    """A `TestClient` over a temp home with the env override cleared.

    `load_api_key()` reads the environment FIRST (F0-S4's ruling), so a
    developer with a real `RENTCAST_API_KEY` exported would otherwise make
    "the route stored my key" pass without the route storing anything.
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    if not route_present(app, KEY_PATH, "PUT"):
        pytest.skip(f"PUT {KEY_PATH} not implemented yet (F2-S1 red)")
    from fastapi.testclient import TestClient

    return TestClient(app)


# ===========================================================================
# existence — the one loud failure everything else skips behind
# ===========================================================================


def test_the_api_key_entry_route_exists(app) -> None:
    """F0-S4's "API key entered in Settings" half-AC, reassigned here.

    Without a route the only way to configure a key is hand-editing
    `~/.rentcomp/secrets.json` or exporting an env var — which is not "entered
    in Settings", and is why QA refused to close F0-S4 on it.
    """
    assert route_present(app, KEY_PATH, "PUT"), (
        f"PUT {KEY_PATH} is not in the app's OpenAPI document. The SPA cannot write "
        "~/.rentcomp/secrets.json itself, so key entry needs a route; F0-S4 built the storage "
        "half only (storage/secrets.py::save_api_key) and the PM reassigned the entry half to "
        "this story. If the PM rules a different path, set RENTCOMP_TEST_API_KEY_PATH."
    )


# ===========================================================================
# storing a key
# ===========================================================================


def test_putting_a_key_stores_it_where_the_client_will_find_it(client) -> None:
    """The whole point: what Settings saves is what `RentCastClient` reads.

    Asserted through `load_api_key()` rather than by reading the file, because
    that function is the *only* consumer that matters — a route that wrote a
    correct-looking file under a different key name would pass a file check and
    still leave live mode credential-less.
    """
    from rentcomp.storage.secrets import load_api_key

    response = client.put(KEY_PATH, json={"api_key": FAKE_KEY})
    assert response.status_code in {200, 204}, (
        f"PUT {KEY_PATH} returned {response.status_code}: {response.text[:300]}"
    )
    assert load_api_key() == FAKE_KEY, (
        "the stored key is not what storage/secrets.py::load_api_key returns — that function "
        "is what the RentCast client consults, so anything else is a key that was 'saved' and "
        "will never be used"
    )


def test_a_stored_key_survives_and_can_be_replaced(client) -> None:
    """Rotating a key is the second thing anyone does in a Settings screen."""
    from rentcomp.storage.secrets import load_api_key

    client.put(KEY_PATH, json={"api_key": FAKE_KEY})
    client.put(KEY_PATH, json={"api_key": FAKE_KEY + "-rotated"})
    assert load_api_key() == FAKE_KEY + "-rotated"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n"])
def test_a_blank_key_is_refused_with_422(client, blank: str) -> None:
    """`save_api_key` raises `ValueError` on a blank, and the reason is not
    fussiness: a stored blank produces a file that *looks* like a configured
    key while `load_api_key()` reports `None` — the user believes they are
    configured and finds out at the first live pull (F0-S4)."""
    from rentcomp.storage.secrets import load_api_key

    response = client.put(KEY_PATH, json={"api_key": blank})
    assert response.status_code == 422, (
        f"a blank key returned {response.status_code}, expected 422 so Settings can show it "
        f"inline: {response.text[:300]}"
    )
    assert load_api_key() is None, "a refused key must not have been written"


def test_a_key_with_surrounding_whitespace_is_trimmed_not_rejected(client) -> None:
    """PM ruling 3's rule applied where it bites hardest: a key pasted from a
    dashboard usually arrives with a trailing newline, and a stored key with a
    space in it fails authentication with a 401 that costs one of the 50
    monthly calls to discover."""
    from rentcomp.storage.secrets import load_api_key

    response = client.put(KEY_PATH, json={"api_key": f"  {FAKE_KEY}\n"})
    assert response.status_code in {200, 204}, response.text[:300]
    assert load_api_key() == FAKE_KEY


# ===========================================================================
# [SECURITY INVARIANT] the key is never handed back
# ===========================================================================


def test_the_put_response_does_not_echo_the_key(client) -> None:
    """A confirmation response is the easiest place to leak one — and this
    repo retains Playwright traces on failure, which capture response bodies.
    """
    response = client.put(KEY_PATH, json={"api_key": FAKE_KEY})
    assert FAKE_KEY not in response.text, (
        "the save response echoed the API key back. Presence is a boolean; the key itself "
        "never leaves storage/secrets.py (D16, CLAUDE.md)"
    )


def test_reading_the_key_status_reports_presence_only_never_the_key(client, app) -> None:
    """Settings has to render "configured" vs "not configured" — that is a
    boolean, and a boolean is all the wire may carry.

    `GET` on the key path is optional (the developer may fold presence into
    `GET /api/config` or a status route instead); what is NOT optional is that
    whatever reports it never carries the secret. So this asserts the strong
    form: after storing a key, it appears in **no** GET response anywhere in
    the app's route table, nor in the OpenAPI document.
    """
    client.put(KEY_PATH, json={"api_key": FAKE_KEY})

    if route_present(app, KEY_PATH, "GET"):
        status = client.get(KEY_PATH)
        assert status.status_code == 200, status.text[:300]
        assert FAKE_KEY not in status.text, (
            f"GET {KEY_PATH} returned the stored key. It must report presence only — a "
            "local-only tool still writes responses into devtools, HAR files and Playwright "
            "traces"
        )
        assert any(value is True for value in status.json().values()), (
            "the status response must carry a boolean saying a key IS configured, or Settings "
            f"cannot render its own state: {status.text[:200]}"
        )

    paths = (app.openapi().get("paths") or {})
    for path, operations in paths.items():
        if "get" not in operations or "{" in path or not path.startswith("/api"):
            continue
        response = client.get(path)
        assert FAKE_KEY not in response.text, (
            f"GET {path} leaked the stored API key. No route may return it (D16) — presence "
            "is a boolean"
        )


def test_the_openapi_document_never_contains_a_stored_key(client, app) -> None:
    """A schema `example` or a default populated from the live value is the
    other classic leak, and `/openapi.json` is served to the browser and
    consumed by D12's codegen — a key in there would land in a committed
    `schema.d.ts`."""
    client.put(KEY_PATH, json={"api_key": FAKE_KEY})
    import json as _json

    assert FAKE_KEY not in _json.dumps(app.openapi()), (
        "the OpenAPI document contains the stored API key — it is public to the browser and "
        "is what D12's committed TypeScript types are generated from"
    )
