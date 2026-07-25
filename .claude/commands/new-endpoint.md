---
description: Scaffold a new FastAPI router endpoint following this project's layering rules
argument-hint: <method> <path> e.g. "POST /api/decisions"
---

Scaffold a new endpoint: $ARGUMENTS

Follow the project's layering (see CLAUDE.md / D5, D12, model layers):

1. Add the route in `backend/src/rentcomp/api/`, using existing routers as the pattern for error handling and response models.
2. Request/response shapes are Pydantic models in `backend/src/rentcomp/models/responses.py` (or `dto.py` if this wraps a RentCast call) — never inline `dict`/`Any`.
3. Any computation the endpoint needs belongs in `backend/src/rentcomp/pipeline/` or `stats/`, not in the route function itself. Routes call into those modules; they don't compute.
4. Add a pytest API-contract test in `backend/tests/api/` using `TestClient` before considering this done — that's the layer most acceptance criteria live at.
5. If the response shape changed, regenerate the TS types (`openapi-typescript`) — don't hand-edit the generated file.

Ask before touching anything under `frontend/` unless the endpoint's caller is part of this request.
