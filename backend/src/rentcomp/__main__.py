"""One-command launch (ARCHITECTURE.md D7).

`python -m rentcomp` and the `rentcomp` console script both land here and
serve the API plus the built UI on localhost:8000. Local-only tool (D1):
binds the loopback interface, never 0.0.0.0.
"""

from __future__ import annotations

import uvicorn

HOST = "127.0.0.1"
PORT = 8000


def main() -> None:
    uvicorn.run("rentcomp.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
