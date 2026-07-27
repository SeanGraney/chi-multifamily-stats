"""Config store — F0-S5. Spec §2.3's knobs, with defaults, ranges, and
persistence to ``<RENTCOMP_HOME>/config.json``.

The load-bearing clause of the story is *"consumed only via the derivation
graph so a knob change re-derives like any other input"*: a `Config` is a
plain, immutable **value object**. Constructing one touches no filesystem, so
the pipeline can receive it as an argument the same way it receives comps,
weights, and drift. Nothing under `pipeline/`, `stats/`, `client/`, or
`models/` may call `load_config()` — reading the file mid-derive makes a knob
change invisible to F0-S2's reactivity invariant. Loading happens at the
edge (`api/`, `app.py`, `__main__.py`), where a request is assembled.

The §2.3 table, verbatim, as implemented here:

| Knob                          | Default | Range (inclusive)        |
|-------------------------------|---------|--------------------------|
| `stitch_gap_days`             | 42      | 7–60                     |
| `provisional_lease_days`      | 7       | 3–21                     |
| `withdrawal_suspect_months`   | 6       | 2–10                     |
| `knn_k`                       | 7       | 3–15                     |
| `bucket_half_width_pct`       | 4.0     | 2–10 (percentage points) |
| `min_cohort_size`             | 4       | 2–10                     |
| `drift_source`                | manual  | zip \\| cohort \\| manual  |
| `km_horizons_days`            | 14/30/45/60 | editable             |
| `query_padding_days`          | 90      | FIXED, internal          |

Contract (PM-confirmed rulings, F0-S5):

* **Invalid value in process ⇒ `ValueError`.** Never clamped, never replaced
  by the default — a clamped knob is a lie: the user sees the value they
  typed while the math uses another one. (`pydantic.ValidationError`
  subclasses `ValueError`.)
* **Invalid file ⇒ `ConfigError` naming the path** — never a silent
  fall-back to defaults. A user's tuned 30-day stitch gap quietly becoming 42
  reclassifies leases and moves every downstream number with zero signal.
  `ConfigError` is deliberately *not* a `ValueError`, so a caller can tell
  "your file is bad" from "your code is bad".
* **`load_config()` never writes** (no file, no directory, no debris).
* **Missing keys are filled with defaults; unknown keys are rejected.** The
  file is hand-editable, so changing one knob must not require restating
  eight; but an unrecognized key is nearly always a typo of a real knob, and
  ignoring it leaves the user believing they set something they did not.
* **`Config` is immutable** — a stage mutating knobs mid-derive would break
  F0-S2's idempotence.
* **`km_horizons_days`** is non-empty, positive, and strictly increasing;
  an unsorted list is rejected, not silently sorted.
* **`bucket_half_width_pct` is percentage points** (4.0 == ±4%), not a
  fraction — §2.3 states the range in percent, so bound and value share a
  unit.
* **Range endpoints are inclusive**; file keys are exactly the field names;
  every knob is written on save.
* All three drift sources are accepted here, including the v2 `zip`/`cohort`
  sources. Whether the *pipeline* supports them is F8's problem — config's
  job is to store what §2.3 names. Only the canonical lowercase spellings
  are legal (see `drift_source` below).

`query_padding_days` (§2.3: "fixed, internal") is readable **on the value
object** — F4-S1's query planner receives PAD like every other setting — but
it is not a knob: not a field, not settable, not serialized, not overridable
from the file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from rentcomp.storage import rentcomp_home

__all__ = [
    "QUERY_PADDING_DAYS",
    "Config",
    "ConfigError",
    "config_path",
    "load_config",
    "save_config",
]

#: Spec §2.3 "Query padding | 90 days | fixed, internal", used on both sides
#: of F4-S1's daysOld window. A constant, not a knob: §3.2 pads because
#: RentCast resets `listedDate` on re-list, so a stitched listing's true start
#: can fall inside the window even when its latest re-list does not. Shrinking
#: it would silently drop those records before the stitcher ever sees them.
QUERY_PADDING_DAYS = 90

CONFIG_FILENAME = "config.json"


class ConfigError(Exception):
    """The config file exists but cannot yield a valid `Config`.

    Distinct from `ValueError` on purpose: a `ValueError` out of `Config(...)`
    means the *caller* passed something out of contract, while a
    `ConfigError` means the *file on disk* is unusable and the user has to go
    edit it. Every message names the offending path.
    """


class Config(BaseModel):
    """The §2.3 knobs as an immutable value object.

    Frozen (no rebinding of any knob) and `extra="forbid"` (an unrecognized
    keyword is an error, which is also what rejects `query_padding_days` and
    typo'd knob names coming out of the file).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stitch_gap_days: int = Field(default=42, ge=7, le=60)
    provisional_lease_days: int = Field(default=7, ge=3, le=21)
    withdrawal_suspect_months: int = Field(default=6, ge=2, le=10)
    knn_k: int = Field(default=7, ge=3, le=15)
    #: Percentage POINTS: 4.0 means ±4% (§2.3 states the range as "±2–10%").
    bucket_half_width_pct: float = Field(default=4.0, ge=2.0, le=10.0)
    min_cohort_size: int = Field(default=4, ge=2, le=10)
    #: Canonical lowercase only — the stored value is the value the user
    #: typed, so no case-folding coercion here (see module docstring). A
    #: `Literal` (not a free string + pattern) so the JSON schema carries the
    #: three legal values through to the generated TS types (D12).
    drift_source: Literal["zip", "cohort", "manual"] = "manual"
    km_horizons_days: list[int] = Field(default_factory=lambda: [14, 30, 45, 60])

    @field_validator("km_horizons_days")
    @classmethod
    def _validate_horizons(cls, horizons: list[int]) -> list[int]:
        if not horizons:
            raise ValueError("km_horizons_days must not be empty")
        if any(day <= 0 for day in horizons):
            raise ValueError(f"km_horizons_days must all be positive (got {horizons!r})")
        if any(b <= a for a, b in zip(horizons, horizons[1:], strict=False)):
            # Rejected rather than sorted/de-duplicated: F11-S5 reads the
            # horizons positionally, and silently reordering what the user
            # wrote hides the mistake instead of surfacing it.
            raise ValueError(
                f"km_horizons_days must be strictly increasing (got {horizons!r})"
            )
        return horizons

    @property
    def query_padding_days(self) -> int:
        """§2.3's fixed 90-day query padding — readable, never settable.

        A property rather than a field: fields are what `model_dump()`
        serializes and what `extra="forbid"` accepts, and PAD must be neither
        persisted nor overridable.
        """
        return QUERY_PADDING_DAYS


def config_path() -> Path:
    """``<RENTCOMP_HOME>/config.json`` — resolved at call time (D21)."""
    return rentcomp_home() / CONFIG_FILENAME


def load_config() -> Config:
    """Read the config file, or return §2.3 defaults when it does not exist.

    Never writes anything. Raises `ConfigError` (naming the path) when a file
    is present but unreadable, not JSON, not a JSON object, or carries a
    value or key outside the §2.3 contract — never a silent fall-back to
    defaults.
    """
    path = config_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        # First run: no file (and possibly no home directory) is the normal
        # state, not an error.
        return Config()
    except OSError as exc:  # unreadable, a directory, bad permissions, ...
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"config file {path} is not valid JSON: {exc}. "
            "Fix or delete the file to fall back to defaults."
        ) from exc

    if not isinstance(payload, dict):
        raise ConfigError(
            f"config file {path} must contain a JSON object of knobs, "
            f"got {type(payload).__name__}"
        )

    try:
        return Config.model_validate(payload)
    except ValidationError as exc:
        raise ConfigError(f"config file {path} is not a valid configuration: {exc}") from exc


def save_config(config: Config) -> Path:
    """Persist every knob to ``<RENTCOMP_HOME>/config.json``; return the path.

    Written tmp → fsync → rename (ARCHITECTURE.md §5a), so a crash mid-write
    can never leave a half-written config that parses as a different set of
    knobs. `query_padding_days` is not written: it is fixed and internal, and
    putting it in a hand-editable file invites editing it.
    """
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Field-declaration order (== §2.3's table order), not sorted: the file is
    # meant to be read and hand-edited alongside the spec.
    body = json.dumps(config.model_dump(mode="json"), indent=2) + "\n"

    tmp = path.with_name(f".{path.name}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)  # atomic on POSIX
    except OSError:
        tmp.unlink(missing_ok=True)
        raise
    return path
