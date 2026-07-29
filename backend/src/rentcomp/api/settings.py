"""`PUT` / `GET /api/settings/api-key` — F0-S4's "entered in Settings" half.

F0-S4's AC reads *"API key entered in Settings, stored locally, sent as
`X-Api-Key`"*. It built and verified the last two clauses (`storage/secrets.py`,
`client/rentcast.py`) and left the first with nothing to verify against, because
no Settings surface existed. QA refused to close it on that; the PM reassigned
the entry half here.

Entry needs a route at all because the SPA cannot write
`<RENTCOMP_HOME>/secrets.json` — the alternative is hand-editing a JSON file or
exporting an env var, neither of which is "entered in Settings".

[SECURITY INVARIANT] NO ROUTE EVER RETURNS THE KEY
---------------------------------------------------
Not masked, not truncated, not "just the last four" — **presence is a boolean**,
and that boolean is the entire read surface. This is not caution for its own
sake: a local-only tool still writes its responses into browser devtools, HAR
exports, and this repo's retained Playwright failure traces, and `/openapi.json`
is served to the browser *and* consumed by D12's codegen, so a key that reached
a schema example would land in a committed `schema.d.ts`. `storage/secrets.py`
holds the line on its side ("nothing here ever returns the key to a log, a
traceback or a repr"); a read-back route would hand it out of the front door
instead. Hence `ApiKeyStatus` carries one `bool` and has nowhere to put a
string.

The key is also never echoed by the *write*: `PUT` answers with the same status
object, so the confirmation a Settings screen renders is "configured", not the
value it just sent.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from rentcomp.storage.secrets import load_api_key, save_api_key, secrets_path

__all__ = ["router"]

router = APIRouter(prefix="/api/settings", tags=["settings"])


class ApiKeyRequest(BaseModel):
    """The one field Settings sends. Never logged, never echoed back.

    Typed as a plain `str` with no length or pattern constraint: the only
    authority on what a usable key is, is `storage/secrets.py::save_api_key`,
    and a second opinion here would either reject a valid key or accept one
    that function then refuses.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    api_key: str


class ApiKeyStatus(BaseModel):
    """Whether a key is configured. A boolean, by design — see the module
    docstring's security invariant.

    True when `load_api_key()` finds one, which is the only question worth
    answering: that function is what the RentCast client consults, so this
    reports whether a live pull would have a credential rather than whether
    some file exists. It is therefore also true when the key comes from
    `RENTCAST_API_KEY` rather than from a save made here.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    configured: bool


@router.put("/api-key", response_model=ApiKeyStatus, summary="Store the RentCast API key")
def put_api_key(request: ApiKeyRequest) -> ApiKeyStatus:
    """Store `api_key` where `RentCastClient` will look for it.

    Surrounding whitespace is trimmed by `save_api_key`, and that is the case
    that bites hardest: a key pasted from a dashboard usually arrives with a
    trailing newline, and a stored key with a space in it fails authentication
    with a 401 that costs one of the month's 50 calls to discover.

    A blank (or whitespace-only) value is a 422, not a stored empty string — a
    stored blank produces a file that *looks* configured while `load_api_key()`
    reports `None`, so the user would find out at the first live pull.
    """
    try:
        save_api_key(request.api_key)
    except ValueError as exc:
        # The message is `save_api_key`'s own and mentions no value.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        # Same shape as the config route's: `save_api_key` re-raises `OSError`
        # bare, so an untranslated one would surface as an anonymous 500 and a
        # save that silently did not happen. The path is named; the key is not.
        raise HTTPException(
            status_code=500,
            detail=(
                f"the API key could not be written to {secrets_path()}: {exc}. "
                "Nothing was saved."
            ),
        ) from exc
    return ApiKeyStatus(configured=True)


@router.get("/api-key", response_model=ApiKeyStatus, summary="Is an API key configured?")
def get_api_key_status() -> ApiKeyStatus:
    """Presence only — never the key (module docstring).

    Settings has to render "configured" vs "not configured", and that is a
    boolean; anything richer would be a leak wearing a helpful face.
    """
    return ApiKeyStatus(configured=load_api_key() is not None)
