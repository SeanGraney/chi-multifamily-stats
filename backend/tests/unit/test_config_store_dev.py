"""F0-S5 — developer-side unit tests for the config store.

Complements QA's `test_config_store.py` (which pins the §2.3 contract itself).
These cover what *this implementation* makes newly possible to get wrong:

* the `query_padding_days`-as-a-property mechanism (a `@computed_field` or a
  plain attribute would each break a different part of "fixed, internal"),
* the deliberately-unpinned drift-source case decision (lowercase only),
* `ConfigError` being a distinct type from `ValueError` — the whole point of
  P2 is that a caller can tell "your file is bad" from "your code is bad",
* the storage-root resolver's edge cases (empty override, `~` expansion,
  no directory created as a side effect),
* error messages naming the offending knob, since this is a local tool with
  no Settings UI yet and the file is the only place to fix it,
* list aliasing: `km_horizons_days` is the one mutable-typed knob, so a
  caller's list must not stay wired to the frozen config.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rentcomp.storage import RENTCOMP_HOME_ENV, rentcomp_home
from rentcomp.storage.config import (
    QUERY_PADDING_DAYS,
    Config,
    ConfigError,
    config_path,
    load_config,
    save_config,
)


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv(RENTCOMP_HOME_ENV, str(root))
    return root


# --- query_padding_days: fixed, internal, and not a field -------------------


def test_query_padding_is_not_a_model_field() -> None:
    """Not a field is *why* it is neither serialized nor accepted as a knob —
    both properties follow from this one fact, so pin the fact."""
    assert "query_padding_days" not in Config.model_fields
    assert "query_padding_days" not in Config().model_dump()
    assert Config().query_padding_days == QUERY_PADDING_DAYS == 90


def test_query_padding_cannot_be_assigned_after_construction() -> None:
    """A `@property` with no setter, on a frozen model: assignment fails."""
    cfg = Config()
    with pytest.raises((AttributeError, ValueError)):
        cfg.query_padding_days = 120  # type: ignore[misc]
    assert cfg.query_padding_days == 90


def test_query_padding_as_a_constructor_kwarg_names_itself_in_the_error() -> None:
    """`extra="forbid"` is what rejects it; the message has to make clear
    which keyword was refused, not just that something was."""
    with pytest.raises(ValueError) as excinfo:
        Config(query_padding_days=120)  # type: ignore[call-arg]
    assert "query_padding_days" in str(excinfo.value)


def test_query_padding_is_identical_across_configs() -> None:
    assert Config().query_padding_days == Config(knn_k=15).query_padding_days


# --- drift_source case: lowercase only (the UNPINNED call, made) ------------


@pytest.mark.parametrize("variant", ["Manual", "MANUAL", "Zip", "Cohort", " manual"])
def test_drift_source_is_case_sensitive_and_untrimmed(variant: str) -> None:
    """[DEFAULT] decision: only the canonical lowercase spellings are legal.

    Accepting `"Manual"` would mean the value the user wrote is not the value
    that round-trips, and a normalizing coercion sits badly next to the rest
    of this store's posture (unknown keys and out-of-range values are
    rejected precisely because a near-miss is nearly always a mistake worth
    surfacing). Easy to relax later; impossible to tighten once persisted
    files contain mixed spellings.
    """
    with pytest.raises(ValueError):
        Config(drift_source=variant)  # type: ignore[arg-type]


@pytest.mark.parametrize("source", ["zip", "cohort", "manual"])
def test_every_drift_source_including_v2_ones_persists(home: Path, source: str) -> None:
    """Config stores what §2.3 names; whether the pipeline can *use* `zip` or
    `cohort` yet is F8's problem, not a reason to reject them here."""
    save_config(Config(drift_source=source))  # type: ignore[arg-type]
    assert load_config().drift_source == source


# --- ConfigError is a distinct type ----------------------------------------


def test_config_error_is_not_a_value_error() -> None:
    """P2: a file problem and a caller bug must be separately catchable.
    `except ValueError` (the caller-bug handler) must not swallow a bad file.
    """
    assert issubclass(ConfigError, Exception)
    assert not issubclass(ConfigError, ValueError)


def test_unknown_key_error_names_the_offending_key(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({"stich_gap_days": 30}), encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "stich_gap_days" in str(excinfo.value)


def test_out_of_range_file_error_names_the_offending_knob(home: Path) -> None:
    (home / "config.json").write_text(json.dumps({"knn_k": 99}), encoding="utf-8")
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "knn_k" in message and "config.json" in message


def test_non_utf8_config_file_raises_config_error(home: Path) -> None:
    """A binary/mis-encoded file is a file problem, not a caller bug —
    `UnicodeDecodeError` is a `ValueError`, so it has to be converted or it
    would land in the wrong handler (and P2's distinction would leak)."""
    (home / "config.json").write_bytes(b'{"knn_k": 7, "note": "\xff\xfe"}')
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "config.json" in str(excinfo.value)


def test_unreadable_config_path_raises_config_error(home: Path) -> None:
    """A `config.json` that is a directory (or otherwise unopenable) is an
    OSError, not a JSON error — it must still surface as ConfigError rather
    than escaping as a raw filesystem exception."""
    (home / "config.json").mkdir()
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "config.json" in str(excinfo.value)


# --- storage root -----------------------------------------------------------


def test_empty_home_override_falls_back_to_dot_rentcomp(tmp_path, monkeypatch) -> None:
    """`RENTCOMP_HOME=` (set but empty) means unset, not the cwd — otherwise
    an accidental empty export would scatter config into the repo."""
    fake_home = tmp_path / "user"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.setenv(RENTCOMP_HOME_ENV, "")
    assert rentcomp_home() == fake_home / ".rentcomp"


def test_home_override_expands_a_tilde(tmp_path, monkeypatch) -> None:
    fake_home = tmp_path / "user"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv(RENTCOMP_HOME_ENV, "~/rentcomp-elsewhere")
    assert rentcomp_home() == fake_home / "rentcomp-elsewhere"


def test_resolving_the_home_creates_nothing(tmp_path, monkeypatch) -> None:
    root = tmp_path / "never-created"
    monkeypatch.setenv(RENTCOMP_HOME_ENV, str(root))
    assert rentcomp_home() == root
    assert config_path() == root / "config.json"
    assert not root.exists()


# --- persistence details ----------------------------------------------------


def test_save_returns_the_written_path(home: Path) -> None:
    assert save_config(Config()) == home / "config.json"


def test_saved_file_holds_exactly_the_knobs(home: Path) -> None:
    """P10: file keys == field names, nothing more (no PAD, no metadata,
    no schema version this story never agreed to)."""
    save_config(Config())
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert set(on_disk) == set(Config.model_fields)


def test_horizons_list_is_copied_not_aliased() -> None:
    """`km_horizons_days` is the only mutable-typed knob; a caller holding
    the list it passed in must not be able to reach into a frozen Config."""
    caller_list = [14, 30]
    cfg = Config(km_horizons_days=caller_list)
    caller_list.append(45)
    assert list(cfg.km_horizons_days) == [14, 30]


def test_horizons_cannot_be_rebound_on_a_frozen_config() -> None:
    cfg = Config()
    with pytest.raises(ValueError):
        cfg.km_horizons_days = [1, 2]  # type: ignore[misc]


def test_load_reflects_a_relocated_home_within_one_process(tmp_path, monkeypatch) -> None:
    """D21 in the small: two homes, two different stored configs, no import
    time caching anywhere in the read path."""
    first, second = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(RENTCOMP_HOME_ENV, str(first))
    save_config(Config(knn_k=3))
    monkeypatch.setenv(RENTCOMP_HOME_ENV, str(second))
    save_config(Config(knn_k=15))
    assert load_config().knn_k == 15
    monkeypatch.setenv(RENTCOMP_HOME_ENV, str(first))
    assert load_config().knn_k == 3
