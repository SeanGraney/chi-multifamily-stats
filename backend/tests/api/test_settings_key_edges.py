"""Settings API-key route — the two edges QA's contract file does not reach.

Developer-authored, deliberately small: `test_f2s1_settings_key_route.py` (QA)
already covers storing, rotating, trimming, the blank refusal and the whole
security sweep, and duplicating any of that here would be maintenance without
insight. What is left is the two states that file has no way to set up, both of
which are about the route being honest when something is *absent* or *broken*.
"""

from __future__ import annotations

import pytest

KEY_PATH = "/api/settings/api-key"
API_KEY_ENV = "RENTCAST_API_KEY"

FAKE_KEY = "rc-fake-test-key-NOT-A-CREDENTIAL-0001"


@pytest.fixture
def client(app, rentcomp_home, monkeypatch):
    """A `TestClient` over a temp home with the env override cleared.

    `load_api_key()` reads the environment first (F0-S4), so a developer with a
    real key exported would otherwise make "not configured" fail for a reason
    that has nothing to do with the route.
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_a_fresh_install_reports_not_configured(client) -> None:
    """The state every clone starts in, and the one Settings has to render a
    prompt for. `False`, not a 404 and not an error: having no key yet is the
    normal condition of fixture mode, which is every run in this project
    (WORKFLOW.md §6)."""
    response = client.get(KEY_PATH)
    assert response.status_code == 200, response.text[:300]
    assert response.json() == {"configured": False}


def test_the_status_follows_the_environment_override_not_just_the_file(
    client, monkeypatch
) -> None:
    """`load_api_key()` reads `RENTCAST_API_KEY` before the stored file (F0-S4),
    so the honest answer to "am I configured" is that function's, not
    `secrets.json.exists()`.

    The distinction is not academic: a developer running with an exported key
    and no saved file would otherwise be told to go enter one, and a user whose
    env var shadows a stale stored key would be told the wrong key is in use.
    """
    monkeypatch.setenv(API_KEY_ENV, FAKE_KEY)
    assert client.get(KEY_PATH).json() == {"configured": True}


def test_an_unwritable_secrets_path_is_a_named_5xx_not_an_anonymous_500(
    app, rentcomp_home, monkeypatch
) -> None:
    """`save_api_key` re-raises `OSError` bare, exactly as `save_config` does,
    so the same translation is needed here for the same reason: an untranslated
    one is Starlette's `text/plain` fallback, and the user is told "Internal
    Server Error" about a save that did not happen.

    Provoked through the filesystem (a directory where `secrets.json` belongs
    makes the final `os.replace` fail on every platform) rather than by
    patching, so the test does not care how the handler imports the store.

    The assertions never echo the value being sent — only whether it appears.
    """
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    stub = rentcomp_home / "secrets.json"
    stub.mkdir()
    (stub / "occupied.txt").write_text("not a secrets file", encoding="utf-8")

    from fastapi.testclient import TestClient

    tolerant = TestClient(app, raise_server_exceptions=False)
    response = tolerant.put(KEY_PATH, json={"api_key": FAKE_KEY})

    assert 500 <= response.status_code < 600, (
        f"expected a 5xx for an unwritable secrets path, got {response.status_code}"
    )
    assert "json" in response.headers.get("content-type", ""), (
        "the OSError escaped the handler — Starlette's unhandled-exception fallback is "
        "text/plain and says nothing the user can act on"
    )
    assert FAKE_KEY not in response.text, (
        "the failure response echoed the key. An error path is the easiest place to leak one, "
        "and it is the path most likely to end up in a bug report (D16)"
    )
