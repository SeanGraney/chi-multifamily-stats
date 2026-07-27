"""Filesystem storage under ~/.rentcomp/ (ARCHITECTURE.md §2, §5):
cache.py, workspace.py, config.py, decisions.py. Populated by F3+ stories;
cache durability rules are D24 — read §5a before touching cache code.

This package also owns the **storage root resolver** (`rentcomp_home`,
established by F0-S5). It lives here rather than in `config.py` because every
storage concern inherits the same root and the same override: config.json,
secrets.json, ledger.json, cache/, workspaces/, decisions/.

`RENTCOMP_HOME` is the one override, and it is resolved **at call time, never
cached at import time** — D21/§8's Layer-2 tests and Playwright's
`globalSetup` both point it at a temp directory *after* the package has been
imported, and an import-time snapshot would silently read (and write) the
developer's real `~/.rentcomp` instead.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["RENTCOMP_HOME_ENV", "rentcomp_home"]

#: Environment variable that relocates the storage root (tests, E2E fixtures).
RENTCOMP_HOME_ENV = "RENTCOMP_HOME"


def rentcomp_home() -> Path:
    """The storage root: ``$RENTCOMP_HOME`` if set, else ``~/.rentcomp``.

    Resolved on every call. The path is *not* created here — reading must
    never be a mutation (F0-S5 P3); writers call `mkdir(parents=True)`
    themselves. An empty ``RENTCOMP_HOME`` is treated as unset rather than as
    the current directory, which is what an unset-looking value means in
    practice (``env RENTCOMP_HOME= rentcomp``).
    """
    override = os.environ.get(RENTCOMP_HOME_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".rentcomp"
