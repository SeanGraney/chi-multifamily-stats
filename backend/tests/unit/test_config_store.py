"""F0-S5 — Config store. QA-authored, written RED before any implementation
exists (AGENT_QA.md protocol).

WHAT THIS FILE PINS
-------------------
Spec §2.3's knob table — *defaults and ranges both*, because the ranges are
validation contracts, not documentation — plus persistence through
``RENTCOMP_HOME`` and the structural shape that makes the story's load-bearing
clause ("consumed only via the derivation graph so a knob change re-derives
like any other input") achievable once F0-S2 exists.

| Knob                                | Default        | Range                    |
|-------------------------------------|----------------|--------------------------|
| Stitch gap threshold (historical)   | 42 days        | 7-60                     |
| Provisional-lease threshold         | 7 days         | 3-21                     |
| Withdrawal-suspect window           | 6 months       | 2-10 months              |
| kNN neighbor count (k)              | 7              | 3-15                     |
| Bucket half-width                   | +/-4%          | +/-2-10%                 |
| Min cohort size before fallback     | 4              | 2-10                     |
| Drift source                        | manual         | zip / cohort / manual    |
| Query padding                       | 90 days        | FIXED, INTERNAL          |
| KM display horizons                 | 14/30/45/60 d  | editable                 |

IMPORT TARGET (ARCHITECTURE.md section 2 lists ``storage/config.py``):
``rentcomp.storage.config``.

CONTRACT THESE TESTS PIN (the dev implements to this; anything marked
"PROPOSED" is QA's reading and needs PM confirmation before it is treated as
locked -- see the module-level docstring section below and the
``TestProposedContract`` class):

* ``Config`` -- a value object carrying every knob. Field names / types /
  defaults exactly as in ``KNOB_DEFAULTS`` below. Constructible with plain
  keyword arguments and NO filesystem access; that is what lets F0-S2 take it
  as a derivation *input* rather than reading a file mid-pipeline.
* ``Config().query_padding_days == 90`` -- present on the value object (so a
  pipeline stage receives PAD the same way it receives every other setting),
  but NOT a knob: not settable through the normal knob path and not written
  into the persisted file. Spec §2.3 calls it "fixed, internal".
* ``config_path() -> Path`` -- ``<RENTCOMP_HOME>/config.json``.
* ``rentcomp.storage.rentcomp_home() -> Path`` -- ``$RENTCOMP_HOME`` when set,
  else ``~/.rentcomp``. Resolved at CALL time, never cached at import time
  (ARCHITECTURE.md section 9/D21: tests point ``RENTCOMP_HOME`` at a temp dir).
* ``load_config() -> Config`` -- defaults when the file is absent; never
  writes.
* ``save_config(config) -> ...`` -- persists every knob; round-trip is exact.
* ``ConfigError`` -- raised when a file exists but cannot yield a valid
  Config (malformed JSON, non-object JSON, out-of-range value, unknown key).

PROPOSED (needs PM confirmation, all exercised in ``TestProposedContract``):

1. Out-of-range / invalid values raise ``ValueError`` -- never clamp, never
   silently substitute the default. Consistent with the PM-confirmed F0-S3
   ruling that caller bugs raise rather than laundering into a valid-looking
   result. (``pydantic.ValidationError`` subclasses ``ValueError``, so a
   Pydantic implementation satisfies this for free.)
2. A bad config FILE raises ``ConfigError`` naming the path -- it does NOT
   silently fall back to defaults. Silently defaulting would change what
   every downstream number means (a user's tuned stitch gap of 30 silently
   becoming 42 reclassifies leases) with no visible signal.
3. Unknown keys in the file are rejected, not ignored -- an unknown key is
   almost always a typo of a real knob, and ignoring it means the user
   believes they set something they did not.
4. Missing keys in the file ARE filled with defaults (the file is
   hand-editable; a partial file is legitimate).
5. ``load_config()`` has no write side effects (does not create the file or
   the home directory).
6. ``Config`` instances are immutable -- a pipeline stage must not be able to
   mutate the knobs mid-derive, or two derives on identical input could
   differ.
7. ``km_horizons_days`` must be non-empty, positive, and strictly increasing;
   an unsorted list is rejected rather than silently sorted.
8. ``bucket_half_width_pct`` is in PERCENTAGE POINTS (4.0 == +/-4%), not a
   fraction (0.04). §2.3 states the range in percent ("+/-2-10%"), so the
   validation bound and the stored value must share that unit. If the dev
   prefers a fraction, the range check becomes 0.02-0.10 and every downstream
   consumer's unit changes -- that is a contract change, not a rename.

DELIBERATELY NOT TESTED HERE (and why):

* Any HTTP surface. The story text does not specify one, and F2/Settings owns
  the UI. (ARCHITECTURE.md section 4 lists ``GET/PUT /api/config`` -- flagged
  to the PM as an ambiguity rather than pinned here. Inventing the route now
  would lock a contract this story never agreed to.)
* End-to-end "a knob change re-derives" -- F0-S2 does not exist yet. What is
  pinned instead is the shape that makes it possible (value object, no
  filesystem read at point of use) plus the structural guard in
  ``test_derivation_modules_never_read_config_from_disk``.
* Whether the pipeline actually honors any knob -- that belongs to F4-S3
  (stitch gap), F4-S8 (provisional/withdrawal), F11-S1 (k), F10-S1 (bucket
  half-width), F4-S4 (min cohort), F4-S1 (PAD).

EXPECTED RED STATE TODAY: ``test_module_imports_and_exports`` FAILS with the
ModuleNotFoundError detail; ``test_storage_package_exposes_rentcomp_home``
FAILS the same way; ``test_derivation_modules_never_read_config_from_disk``
PASSES vacuously (it guards the future); every other test SKIPS until the
module exists, then runs for real.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# --- import gates (two, so a mislocated home-resolver does not blank the file)

try:
    from rentcomp.storage.config import (
        Config,
        ConfigError,
        config_path,
        load_config,
        save_config,
    )

    _CONFIG_IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover - red state before F0-S5 lands
    _CONFIG_IMPORT_ERROR = _e

try:
    from rentcomp.storage import rentcomp_home

    _HOME_IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # pragma: no cover - red state before F0-S5 lands
    _HOME_IMPORT_ERROR = _e

needs_config = pytest.mark.skipif(
    _CONFIG_IMPORT_ERROR is not None,
    reason=f"rentcomp.storage.config not importable yet (F0-S5 red): {_CONFIG_IMPORT_ERROR}",
)
needs_home = pytest.mark.skipif(
    _HOME_IMPORT_ERROR is not None,
    reason=f"rentcomp.storage.rentcomp_home not importable yet (F0-S5 red): {_HOME_IMPORT_ERROR}",
)

BACKEND_SRC = Path(__file__).resolve().parents[2] / "src" / "rentcomp"

# --- the §2.3 table, as data ------------------------------------------------

#: (field, default) for every *knob*. Query padding is NOT here: it is fixed
#: and internal, and is asserted separately.
KNOB_DEFAULTS: list[tuple[str, object]] = [
    ("stitch_gap_days", 42),
    ("provisional_lease_days", 7),
    ("withdrawal_suspect_months", 6),
    ("knn_k", 7),
    ("bucket_half_width_pct", 4.0),
    ("min_cohort_size", 4),
    ("drift_source", "manual"),
    ("km_horizons_days", [14, 30, 45, 60]),
]

#: (field, low, high) -- inclusive endpoints. §2.3 writes ranges as "7-60";
#: QA reads dashes as inclusive (same reading the PM accepted for T-S3's ±90
#: window boundaries). Flagged in the handoff.
KNOB_RANGES: list[tuple[str, float, float]] = [
    ("stitch_gap_days", 7, 60),
    ("provisional_lease_days", 3, 21),
    ("withdrawal_suspect_months", 2, 10),
    ("knn_k", 3, 15),
    ("bucket_half_width_pct", 2, 10),
    ("min_cohort_size", 2, 10),
]

DRIFT_SOURCES = ["zip", "cohort", "manual"]

QUERY_PADDING_FIELD = "query_padding_days"
FIXED_QUERY_PADDING = 90

#: A fully non-default (but entirely in-range) config, used for round-trips.
NON_DEFAULT = {
    "stitch_gap_days": 30,
    "provisional_lease_days": 10,
    "withdrawal_suspect_months": 3,
    "knn_k": 5,
    "bucket_half_width_pct": 6.5,
    "min_cohort_size": 2,
    "drift_source": "cohort",
    "km_horizons_days": [7, 21, 35],
}


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    """Point RENTCOMP_HOME at a temp dir (ARCHITECTURE.md section 9/D21)."""
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    return root


def _write_config_file(home_dir: Path, payload: object) -> Path:
    path = home_dir / "config.json"
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8"
    )
    return path


# --- existence --------------------------------------------------------------


def test_module_imports_and_exports() -> None:
    """Legible red: the store must exist at the ARCHITECTURE.md section 2 path."""
    assert _CONFIG_IMPORT_ERROR is None, (
        "Cannot import Config/ConfigError/config_path/load_config/save_config "
        f"from rentcomp.storage.config: {_CONFIG_IMPORT_ERROR}"
    )
    assert callable(load_config)
    assert callable(save_config)
    assert callable(config_path)
    assert isinstance(ConfigError, type) and issubclass(ConfigError, Exception)


def test_storage_package_exposes_rentcomp_home() -> None:
    """The storage root resolver -- every later storage story (cache,
    workspaces, decisions, ledger) needs the same ``RENTCOMP_HOME`` override,
    so it belongs to the package, not to config.py."""
    assert _HOME_IMPORT_ERROR is None, (
        "Cannot import rentcomp_home from rentcomp.storage: " f"{_HOME_IMPORT_ERROR}"
    )
    assert callable(rentcomp_home)


# --- §2.3 defaults ----------------------------------------------------------


@needs_config
@pytest.mark.parametrize(("field", "expected"), KNOB_DEFAULTS)
def test_knob_default(field: str, expected: object) -> None:
    """Spec §2.3 defaults, exactly. The whole product's numbers depend on
    these; silent drift of any one of them is a semantic change."""
    cfg = Config()
    assert hasattr(cfg, field), f"Config has no knob {field!r} (spec §2.3)"
    actual = getattr(cfg, field)
    assert actual == expected, f"{field}: default is {actual!r}, spec §2.3 says {expected!r}"


@needs_config
def test_defaults_survive_a_round_trip_to_disk(home: Path) -> None:
    """Persisting and reloading the defaults must not perturb any of them."""
    save_config(Config())
    reloaded = load_config()
    for field, expected in KNOB_DEFAULTS:
        assert getattr(reloaded, field) == expected


@needs_config
def test_km_horizons_is_an_ordered_list() -> None:
    """14/30/45/60 in that order -- horizons are read positionally on the
    curve (F11-S5 renders markers); a set or an unordered container would
    make the display order non-deterministic."""
    horizons = Config().km_horizons_days
    assert isinstance(horizons, (list, tuple)), f"expected an ordered sequence, got {type(horizons)}"
    assert list(horizons) == [14, 30, 45, 60]
    assert list(horizons) == sorted(horizons)


@needs_config
def test_km_horizons_are_editable(home: Path) -> None:
    """§2.3 marks the horizons 'editable' -- a different list must be
    accepted, and must round-trip in order."""
    save_config(Config(km_horizons_days=[10, 20, 30, 40, 50]))
    assert list(load_config().km_horizons_days) == [10, 20, 30, 40, 50]


# --- §2.3 ranges (validation contracts) -------------------------------------


@needs_config
@pytest.mark.parametrize(("field", "low", "high"), KNOB_RANGES)
def test_range_endpoints_are_accepted(field: str, low: float, high: float) -> None:
    """Inclusive endpoints: both ends of every §2.3 range are legal values."""
    for value in (low, high):
        cfg = Config(**{field: value})
        assert getattr(cfg, field) == value


@needs_config
@pytest.mark.parametrize(
    ("field", "value"),
    [(f, v) for f, low, high in KNOB_RANGES for v in (low - 1, high + 1)]
    + [
        ("bucket_half_width_pct", 1.9),
        ("bucket_half_width_pct", 10.1),
        ("stitch_gap_days", 0),
        ("knn_k", -7),
        ("min_cohort_size", 0),
    ],
)
def test_out_of_range_value_is_rejected(field: str, value: float) -> None:
    """Out of range must RAISE, not clamp and not fall back to the default.
    A clamped knob is a lie: the user sees the value they typed while the
    math uses another one."""
    with pytest.raises(ValueError):
        Config(**{field: value})


@needs_config
@pytest.mark.parametrize("source", DRIFT_SOURCES)
def test_drift_source_accepts_the_three_named_values(source: str) -> None:
    assert Config(drift_source=source).drift_source == source


@needs_config
@pytest.mark.parametrize("bad", ["", "auto", "market", "zipcode", "MANUAL_", None, 3])
def test_drift_source_rejects_anything_else(bad: object) -> None:
    """§2.3 names exactly three sources. An unrecognized source would leave
    the drift assumption unlabeled -- NORTH_STAR requires drift always be
    rendered with its source."""
    with pytest.raises(ValueError):
        Config(drift_source=bad)


# --- PAD = 90, fixed and internal -------------------------------------------


@needs_config
def test_query_padding_is_90_on_every_config() -> None:
    """§2.3: 'Query padding | 90 days | fixed, internal'. It still has to be
    reachable from the config value object, because F4-S1's query planner
    must receive it the same way it receives every other setting."""
    assert getattr(Config(), QUERY_PADDING_FIELD) == FIXED_QUERY_PADDING
    assert getattr(Config(**NON_DEFAULT), QUERY_PADDING_FIELD) == FIXED_QUERY_PADDING


@needs_config
def test_query_padding_cannot_be_overridden_through_the_knob_path() -> None:
    """'Fixed' means fixed: passing it as a knob either raises or is inert --
    what must never happen is the effective padding changing."""
    try:
        cfg = Config(**{QUERY_PADDING_FIELD: 120})
    except (ValueError, TypeError):
        return  # rejecting the knob outright is a legitimate way to be fixed
    assert getattr(cfg, QUERY_PADDING_FIELD) == FIXED_QUERY_PADDING, (
        "query padding was overridden through the constructor; §2.3 marks it fixed"
    )


@needs_config
def test_query_padding_is_not_persisted_as_a_knob(home: Path) -> None:
    """It must not appear in config.json -- the file is hand-editable, and
    writing a fixed internal there invites editing it."""
    save_config(Config())
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert QUERY_PADDING_FIELD not in on_disk
    assert not any("pad" in key.lower() for key in on_disk), (
        f"a padding-looking key was persisted: {sorted(on_disk)}"
    )


@needs_config
def test_query_padding_in_the_file_cannot_change_the_effective_value(home: Path) -> None:
    """A hand-edited file claiming a different padding either errors or is
    ignored; the effective value stays 90 either way."""
    _write_config_file(home, {QUERY_PADDING_FIELD: 120})
    try:
        cfg = load_config()
    except ConfigError:
        return
    assert getattr(cfg, QUERY_PADDING_FIELD) == FIXED_QUERY_PADDING


# --- storage location -------------------------------------------------------


@needs_home
def test_rentcomp_home_honors_the_env_override(home: Path) -> None:
    assert Path(rentcomp_home()) == home


@needs_home
def test_rentcomp_home_defaults_to_dot_rentcomp(tmp_path, monkeypatch) -> None:
    """ARCHITECTURE.md section 5: ``~/.rentcomp/``. Asserted against a fake
    HOME so the real one is never touched."""
    monkeypatch.delenv("RENTCOMP_HOME", raising=False)
    fake_home = tmp_path / "fake-user-home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))
    assert Path(rentcomp_home()) == fake_home / ".rentcomp"


@needs_home
def test_rentcomp_home_is_resolved_at_call_time(tmp_path, monkeypatch) -> None:
    """No import-time caching -- otherwise every Layer-2 test that repoints
    RENTCOMP_HOME (D21) would silently read the developer's real config."""
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(first))
    assert Path(rentcomp_home()) == first
    monkeypatch.setenv("RENTCOMP_HOME", str(second))
    assert Path(rentcomp_home()) == second


@needs_config
def test_config_path_is_config_json_under_the_home(home: Path) -> None:
    assert Path(config_path()) == home / "config.json"


# --- persistence ------------------------------------------------------------


@needs_config
def test_missing_config_file_yields_defaults(home: Path) -> None:
    """First run: no file, no explosion, canonical §2.3 values."""
    assert not (home / "config.json").exists()
    cfg = load_config()
    for field, expected in KNOB_DEFAULTS:
        assert getattr(cfg, field) == expected


@needs_config
def test_missing_home_directory_yields_defaults(tmp_path, monkeypatch) -> None:
    """RENTCOMP_HOME pointing at a directory that does not exist yet is the
    genuine first-run case; reading must still succeed."""
    monkeypatch.setenv("RENTCOMP_HOME", str(tmp_path / "not-created-yet"))
    assert load_config().knn_k == 7


@needs_config
def test_round_trip_preserves_every_knob(home: Path) -> None:
    save_config(Config(**NON_DEFAULT))
    reloaded = load_config()
    for field, expected in NON_DEFAULT.items():
        actual = getattr(reloaded, field)
        actual = list(actual) if isinstance(actual, (list, tuple)) else actual
        assert actual == expected, f"{field} did not survive the round trip"


@needs_config
def test_save_creates_the_home_directory_if_absent(tmp_path, monkeypatch) -> None:
    root = tmp_path / "made-on-demand"
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    save_config(Config(knn_k=9))
    assert (root / "config.json").is_file()
    assert load_config().knn_k == 9


@needs_config
def test_saved_file_is_a_json_object_keyed_by_knob_name(home: Path) -> None:
    """The file is a hand-editable record of the knobs (§2.3 calls them
    Settings), so every knob is present under its own name."""
    save_config(Config())
    on_disk = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert isinstance(on_disk, dict)
    missing = [field for field, _ in KNOB_DEFAULTS if field not in on_disk]
    assert not missing, f"knobs absent from config.json: {missing}"


@needs_config
def test_save_leaves_no_stray_temp_files(home: Path) -> None:
    """ARCHITECTURE.md section 5a's write discipline (tmp -> rename) must not
    leave debris behind, and must not leave a half-written config."""
    save_config(Config())
    leftovers = [p.name for p in home.iterdir() if p.name != "config.json"]
    assert not leftovers, f"unexpected files left in RENTCOMP_HOME: {leftovers}"


@needs_config
def test_saving_twice_replaces_rather_than_appends(home: Path) -> None:
    save_config(Config(knn_k=5))
    save_config(Config(knn_k=11))
    assert load_config().knn_k == 11
    json.loads((home / "config.json").read_text(encoding="utf-8"))  # still valid JSON


@needs_config
@settings(max_examples=50, deadline=None)
@given(
    stitch_gap_days=st.integers(min_value=7, max_value=60),
    provisional_lease_days=st.integers(min_value=3, max_value=21),
    withdrawal_suspect_months=st.integers(min_value=2, max_value=10),
    knn_k=st.integers(min_value=3, max_value=15),
    bucket_half_width_pct=st.floats(min_value=2.0, max_value=10.0, allow_nan=False),
    min_cohort_size=st.integers(min_value=2, max_value=10),
    drift_source=st.sampled_from(DRIFT_SOURCES),
)
def test_any_in_range_config_round_trips(tmp_path_factory, **knobs) -> None:
    """Property: every combination §2.3 permits is accepted AND survives
    persistence unchanged (a float knob truncated to int on the way to disk
    is the failure this catches).

    Env handling is manual rather than via ``monkeypatch`` because hypothesis
    rejects function-scoped fixtures inside ``@given`` (they are set up once
    for all examples, not per example); ``tmp_path_factory`` is session-scoped
    and therefore fine.
    """
    root = tmp_path_factory.mktemp("home")
    previous = os.environ.get("RENTCOMP_HOME")
    os.environ["RENTCOMP_HOME"] = str(root)
    try:
        save_config(Config(**knobs))
        reloaded = load_config()
        for field, value in knobs.items():
            assert getattr(reloaded, field) == value
    finally:
        if previous is None:
            os.environ.pop("RENTCOMP_HOME", None)
        else:
            os.environ["RENTCOMP_HOME"] = previous


# --- invalid file contents --------------------------------------------------


@needs_config
@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("truncated json", '{"knn_k": 7'),
        ("not json at all", "knn_k = 7\n"),
        ("empty file", ""),
        ("json array", "[1, 2, 3]"),
        ("json scalar", "42"),
        ("json null", "null"),
    ],
)
def test_unreadable_config_file_raises_config_error(home: Path, label: str, payload: str) -> None:
    """A config file that exists but cannot produce a valid Config is a loud
    failure, not a silent fall-back to defaults: defaults that quietly
    replace a user's tuned knobs change what every downstream number means
    with no signal to the user."""
    _write_config_file(home, payload)
    with pytest.raises(ConfigError):
        load_config()


@needs_config
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stitch_gap_days", 5),
        ("stitch_gap_days", 400),
        ("knn_k", 1),
        ("min_cohort_size", 99),
        ("bucket_half_width_pct", 25),
        ("drift_source", "auto"),
        ("km_horizons_days", []),
        ("km_horizons_days", "14,30"),
    ],
)
def test_out_of_contract_value_on_disk_raises_config_error(
    home: Path, field: str, value: object
) -> None:
    """The same §2.3 ranges bind hand-edited files, and a violation there is
    a file problem (ConfigError), not a caller bug."""
    _write_config_file(home, {field: value})
    with pytest.raises(ConfigError):
        load_config()


@needs_config
def test_config_error_names_the_offending_file(home: Path) -> None:
    """The user has to be able to find and fix the file -- this is a local
    tool with no Settings UI yet."""
    _write_config_file(home, '{"knn_k": ')
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    message = str(excinfo.value)
    assert "config.json" in message, f"ConfigError message does not name the file: {message!r}"


# --- value-object shape (what makes 'consumed via the derivation graph' possible)


@needs_config
def test_constructing_a_config_touches_no_filesystem(tmp_path, monkeypatch) -> None:
    """A Config is a plain value -- constructing one must not read or create
    anything. This is what lets F0-S2 accept it as a derivation input."""
    root = tmp_path / "never-created"
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    cfg = Config(**NON_DEFAULT)
    assert cfg.knn_k == 5
    assert not root.exists()


@needs_config
def test_two_configs_with_the_same_knobs_are_equal() -> None:
    """Idempotence of /api/derive (F0-S2 AC) is easier to reason about when
    config is comparable by value rather than by identity."""
    assert Config(**NON_DEFAULT) == Config(**NON_DEFAULT)
    assert Config() != Config(knn_k=11)


def test_derivation_modules_never_read_config_from_disk() -> None:
    """STRUCTURAL GUARD for the story's load-bearing clause: a knob must
    reach the math as a passed-in value, never as an ad-hoc read deep inside
    a pipeline function -- an ad-hoc read is invisible to the reactivity
    invariant ('a knob change re-derives like any other input').

    Passes vacuously today (these packages are near-empty); it exists to trip
    the first time F4/F10/F11 code reaches for the file instead of the
    argument. Loading config in ``api/``, ``app.py`` or ``__main__.py`` -- the
    edge where a request is assembled -- is allowed and not checked here.
    """
    forbidden_names = {"load_config", "save_config", "rentcomp_home", "config_path"}
    forbidden_strings = {"config.json", "RENTCOMP_HOME"}
    offenders: list[str] = []

    for package in ("pipeline", "stats", "client", "models"):
        directory = BACKEND_SRC / package
        if not directory.is_dir():
            continue
        for source in sorted(directory.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Name) and node.id in forbidden_names:
                    offenders.append(f"{source.name}: references {node.id}")
                elif isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                    offenders.append(f"{source.name}: references .{node.attr}")
                elif isinstance(node, ast.Constant) and node.value in forbidden_strings:
                    offenders.append(f"{source.name}: string {node.value!r}")

    assert not offenders, (
        "pipeline/stats/client/models must receive config as a value, not read it: "
        + "; ".join(sorted(set(offenders)))
    )


class TestProposedContract:
    """QA-proposed behavior that the story text does NOT state. The PM/owner
    confirms (or overrides) these before they count as locked. Each is
    argued in the module docstring."""

    @needs_config
    def test_load_does_not_write_anything(self, home: Path) -> None:
        """Reading is not a mutation: no file, no directory, no debris."""
        load_config()
        assert list(home.iterdir()) == []

    @needs_config
    def test_partial_file_fills_the_rest_with_defaults(self, home: Path) -> None:
        """Hand-editing one knob must not require restating the other eight."""
        _write_config_file(home, {"knn_k": 11})
        cfg = load_config()
        assert cfg.knn_k == 11
        assert cfg.stitch_gap_days == 42
        assert list(cfg.km_horizons_days) == [14, 30, 45, 60]

    @needs_config
    def test_unknown_key_in_the_file_is_rejected(self, home: Path) -> None:
        """A typo'd knob name silently ignored means the user believes they
        changed something they did not."""
        _write_config_file(home, {"stich_gap_days": 30})
        with pytest.raises(ConfigError):
            load_config()

    @needs_config
    def test_config_is_immutable(self) -> None:
        """A pipeline stage must not be able to mutate knobs mid-derive."""
        cfg = Config()
        with pytest.raises(Exception):
            cfg.knn_k = 11  # type: ignore[misc]
        assert cfg.knn_k == 7

    @needs_config
    @pytest.mark.parametrize(
        "horizons",
        [[30, 14, 45], [14, 14, 30], [0, 30], [-14, 30], [14.5, 30]],
    )
    def test_km_horizons_must_be_positive_and_strictly_increasing(self, horizons: list) -> None:
        """Unsorted or duplicated horizons are rejected, not silently sorted
        or de-duplicated (same 'caller bugs raise' convention as F0-S3)."""
        with pytest.raises(ValueError):
            Config(km_horizons_days=horizons)

    @needs_config
    def test_bucket_half_width_is_percentage_points_not_a_fraction(self) -> None:
        """4.0 means +/-4%. If this ever becomes 0.04, every §2.3 range bound
        and every downstream comparison changes unit with it."""
        assert Config().bucket_half_width_pct == pytest.approx(4.0)
        with pytest.raises(ValueError):
            Config(bucket_half_width_pct=0.04)
