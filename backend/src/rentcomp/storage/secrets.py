"""The local API-key store (D16) — F0-S4's "entered in Settings, stored locally".

`<RENTCOMP_HOME>/secrets.json`, mode 0600, **or** the `RENTCAST_API_KEY`
environment variable. Never the repo (CLAUDE.md: "Secrets never enter the
repo"), and never a log line.

Two rulings this module implements, both PM-confirmed for F0-S4:

* **The env var wins over the stored file.** D16 names both sources without
  ordering. The env var is the explicit, per-process override — it is what
  `.env` and `scripts/gate.py` already use — so a developer can point one run
  at a different key without editing stored state, and cannot be surprised by
  a stale file quietly overriding what they just exported.
* **A whitespace-only value reads as absent.** `RENTCAST_API_KEY=` in a shell
  profile is the classic way to *think* you have a key. Under D17 the honest
  answer is `MissingApiKeyError` before any call, not a 401 that cost one of
  the month's 50.

Nothing here ever returns the key to a log, a traceback or a `repr`: callers
get the string and are expected to put it in exactly one place, the
`X-Api-Key` header.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from rentcomp.storage import rentcomp_home

__all__ = [
    "API_KEY_ENV",
    "SECRETS_FILENAME",
    "load_api_key",
    "save_api_key",
    "secrets_path",
]

#: D16's environment override. The same name `scripts/gate.py` and `.env` use.
API_KEY_ENV = "RENTCAST_API_KEY"

SECRETS_FILENAME = "secrets.json"

#: The one key in the file. A JSON object rather than a bare string so a later
#: story can add a second credential without a migration.
_KEY_FIELD = "rentcast_api_key"


def secrets_path() -> Path:
    """``<RENTCOMP_HOME>/secrets.json`` — resolved at call time.

    Same rule as `rentcomp_home()` and `config_path()`: never snapshotted at
    import, because tests and E2E point the root at a temp directory *after*
    the package is imported, and a snapshot would write a credential into the
    developer's real `~/.rentcomp`.
    """
    return rentcomp_home() / SECRETS_FILENAME


def load_api_key() -> str | None:
    """The API key, or `None` when there isn't one.

    `None` — not `""`, and never an exception — because "no key yet" is the
    normal state of a fresh clone and of every fixture-mode run (which is all
    of them: WORKFLOW.md §6). Fixture mode must work with no credential at all,
    or `pytest` would fail for anyone without one and tempt them to set one.

    Order: environment first, then the stored file. Whitespace-only from either
    source counts as absent.
    """
    from_env = _clean(os.environ.get(API_KEY_ENV))
    if from_env is not None:
        return from_env

    try:
        raw = secrets_path().read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return None
    except (OSError, UnicodeDecodeError):
        # An unreadable secrets file is "no key", not a crash: the caller's
        # next step under D17 is a loud MissingApiKeyError anyway, and raising
        # here would take down a fixture-mode run that never needed a key.
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _clean(payload.get(_KEY_FIELD))


def save_api_key(key: str) -> Path:
    """Persist `key` to `<RENTCOMP_HOME>/secrets.json` at mode 0600; return the path.

    Written tmp -> fsync -> rename (ARCHITECTURE.md §5a) with the temp file
    created 0600 from the start, so the credential is never briefly world-
    readable between `open()` and `chmod()`.
    """
    cleaned = _clean(key)
    if cleaned is None:
        # Storing a blank would produce a file that *looks* like a configured
        # key while `load_api_key()` reports None — the exact confusion the
        # whitespace rule above exists to prevent.
        raise ValueError("refusing to store a blank API key")

    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({_KEY_FIELD: cleaned}, indent=2) + "\n"

    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)  # defeats a permissive umask on a pre-existing tmp
        os.replace(tmp, path)  # atomic on POSIX; mode travels with the inode
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path


def _clean(value: object) -> str | None:
    """A usable key, or `None`. Non-strings and whitespace-only read as absent."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
