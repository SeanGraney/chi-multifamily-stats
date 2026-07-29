"""`GET` / `PUT /api/config` — spec §2.3's knobs over the wire.

ARCHITECTURE.md §4 has always listed this route; F0-S5 deliberately built only
the store (`storage/config.py`) because nothing had a Settings screen to drive
it. F2-S1 is the story that finally does, so the route lands here (PM ruling).

THE STORE OWNS THE CONTRACT; THIS MODULE OWNS THE EDGE TRANSLATION
-------------------------------------------------------------------
Nothing here re-states a range, a default or a validation rule — `Config` is
already the single source of all three, and a second copy would drift the day
someone widened a bound in one place. What this module adds is the mapping from
F0-S5's three in-process failure modes onto three different HTTP answers, which
is a genuinely different question and the one place a store contract goes wrong:

| condition | in process | on the wire | why |
|---|---|---|---|
| knob out of contract, unknown key, `query_padding_days` | `ValidationError` (a `ValueError`) | **422** | the user's input was wrong, not their tool |
| `config.json` unreadable / unparseable / invalid | `ConfigError` | **500**, naming the file | the file is wrong and only the user can fix it — and it must never quietly become defaults |
| the save could not be written | bare `OSError` | **500**, naming the file | a full disk is not "your file's contents are bad" |

The third row is the sharp one. `save_config` raises `OSError` *unwrapped*, on
purpose (F0-S5, 2026-07-26: wrapping it in `ConfigError` would make that error
mean two things needing two different user actions). So a handler catching only
`ValueError`/`ConfigError` lets it escape as Starlette's anonymous `text/plain`
500 — a save that silently did not happen. It is translated here explicitly.

WHY `Config` IS THE WIRE SHAPE
-------------------------------
`Config` is the request body *and* the response model, rather than being
mirrored into `responses.py`. The three-layer rule (dto → domain → responses)
is about RentCast *evidence*, where the layers exist to stop wire-shaped data
leaking into the math; a config knob has no wire truth and no domain form, so a
mirror would be pure duplication of nine ranges whose only possible behaviour is
to disagree with the store one day. It codegens to TypeScript through D12 like
any other model, and `query_padding_days` stays out of the generated type for
free — it is a property, not a field, so it is neither dumped nor accepted
(§2.3: "fixed, internal").

**PUT replaces, it does not patch.** A body missing a knob gets that knob's
§2.3 default, which is what PUT means and what a Settings screen (which always
sends the whole form) does anyway. It is also the only reading that cannot
half-apply: either the whole body validates and the whole file is written
(tmp → fsync → rename), or nothing is.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from rentcomp.storage.config import Config, ConfigError, config_path, load_config, save_config

__all__ = ["router"]

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config", response_model=Config, summary="Read the §2.3 knobs")
def get_config() -> Config:
    """The knobs as they are on disk, or §2.3's defaults when there is no file.

    Reading never writes (F0-S5): a fresh install must not have today's
    defaults frozen into a file by the act of opening Settings, or a future
    default change would silently not reach it.
    """
    try:
        return load_config()
    except ConfigError as exc:
        # Never a 200 carrying defaults. A user whose tuned 30-day stitch gap
        # quietly became 42 gets different lease classifications and a
        # different anchor with nothing on screen saying so — F0-S5's loudest
        # invariant, and the same translation `api/derive.py` already makes.
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.put("/config", response_model=Config, summary="Replace the §2.3 knobs")
def put_config(config: Config) -> Config:
    """Persist every knob; answer with what is now stored.

    422 comes from the model, before this function runs: an out-of-range knob,
    an unknown key, a non-canonical `drift_source`, unsorted `km_horizons_days`
    or `query_padding_days` all fail validation, so a refused save cannot have
    touched the file. Never clamped and never defaulted — a clamped knob is a
    lie, since the user sees the value they typed while the math uses another.
    """
    try:
        save_config(config)
    except OSError as exc:
        # The row `save_config` cannot translate for us (see module docstring).
        # Naming the path matters more than usual here: the request looked
        # valid, so without it the user has no way to tell a rejected save from
        # a save that happened.
        raise HTTPException(
            status_code=500,
            detail=(
                f"the config file {config_path()} could not be written: {exc}. "
                "Nothing was saved — the knobs in use are unchanged."
            ),
        ) from exc
    return config
