"""RentComp backend package.

Local-only FastAPI service that owns all data and all derivation math
(ARCHITECTURE.md D5), serving the built React UI alongside the API (D7).

Deliberately empty of behavior: importing `rentcomp` must never construct
the web application as a side effect. The assembled app lives in
`rentcomp.app` (see that module's docstring for the layout rationale).
"""
