"""F1-S2 (Layer 1, developer-authored) — `storage/pulls.py::pull_exists`.

The recents index calls this once per row on every render of Home, to answer
F1's *other* edge: "corrupt/**missing** cache entry → row shows error state,
offers refresh". Two properties make it fit for that job, and neither is
observable from the route once it holds:

1.  **It is total.** It returns `False` for every broken entry; it never
    raises. Its caller renders the app's front door, so an exception escaping
    here is not a bad row — it is no Home screen at all, which is precisely the
    crash the AC forbids. THIS FILE IS THE REGRESSION PIN FOR A REAL DEFECT:
    the first implementation caught `(CacheMissError, OSError, ValueError,
    KeyError)`, and a manifest with `"as_of": 17` escaped it as
    `TypeError: fromisoformat: argument must be str` — the identical shape that
    got past F4-S9's handler and produced two anonymous 500s there.
2.  **It costs nothing.** A `stat` and a manifest read, never `load_shaped_pull`
    and never the network. Deriving to answer a boolean would blow the epic's
    "<1s, zero API calls" budget on launch, on every row.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

pytest.importorskip("pydantic")

from rentcomp.storage.cache import write_manifest, write_raw_response  # noqa: E402
from rentcomp.storage.pulls import WS1_REAL_PULL_REF, pull_exists  # noqa: E402

KEY = "a" * 64
WINDOW = ("01-01", "12-31")


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "rentcomp-home"
    root.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    return root


def _complete_entry(key: str = KEY) -> None:
    """A cache entry the way `client/pull.py` files one: bytes, then manifest."""
    write_raw_response(key, "sig0-off000", json.dumps([]).encode("utf-8"), meta={"sig": "sig0"})
    write_manifest(key, as_of=date(2026, 7, 1), window=WINDOW, planned=1, fetchable=1)


def _manifest_path(home, key: str = KEY):
    return home / "cache" / key / "manifest.json"


# ===========================================================================
# the two honest answers
# ===========================================================================


def test_a_complete_cache_entry_exists(home) -> None:
    _complete_entry()

    assert pull_exists(KEY) is True


def test_nothing_on_disk_does_not_exist(home) -> None:
    assert pull_exists(KEY) is False


def test_a_manifest_with_no_bytes_behind_it_does_not_exist(home) -> None:
    """"Not there yet" and "not there" are the same fact to a recents row.

    The rule `_load_cache_backed_pull` already applies: an entry that has a
    manifest and no raw responses cannot be derived from, so a row for it would
    open into an empty Results view — which reads as "no comps in this market"
    rather than "the data is gone".
    """
    write_manifest(KEY, as_of=date(2026, 7, 1), window=WINDOW)

    assert pull_exists(KEY) is False


def test_raw_bytes_with_no_manifest_do_not_exist(home) -> None:
    """The manifest is where `as_of` lives, and `as_of` is the pipeline's only
    "now" — evidence that cannot be dated cannot be shaped."""
    write_raw_response(KEY, "sig0-off000", b"[]", meta={"sig": "sig0"})

    assert pull_exists(KEY) is False


# ===========================================================================
# totality — the regression pin
# ===========================================================================


CORRUPT_MANIFESTS = {
    # The shape that escaped the first implementation: right field names,
    # wrong value types, so the failure happens inside coercion rather than
    # inside `json.loads`.
    "wrong types": json.dumps(
        {"as_of": 17, "window": None, "planned": "lots", "fetchable": "some", "queries": 5}
    ),
    "as_of is a number": json.dumps(
        {"as_of": 20260701, "window": ["01-01", "12-31"], "planned": 1, "fetchable": 1,
         "queries": []}
    ),
    "queries is not a list of objects": json.dumps(
        {"as_of": "2026-07-01", "window": ["01-01", "12-31"], "planned": 1, "fetchable": 1,
         "queries": ["nope"]}
    ),
    "unparseable": "{ not json",
    "empty": "",
    "null": "null",
    "an array": "[]",
    "none of the fields": json.dumps({"note": "hand-edited"}),
}


@pytest.mark.parametrize("shape", sorted(CORRUPT_MANIFESTS))
def test_no_shape_of_corrupt_manifest_makes_pull_exists_raise(home, shape) -> None:
    """`False`, never an exception — for every way the manifest can break.

    `False` is the honest answer as well as the safe one: an entry that cannot
    be read cannot be derived from either, so the row is un-openable and gets
    the same error-row-offering-refresh treatment as one that is gone. Nothing
    here re-fetches to work around it (D24) — refresh stays the user's call.
    """
    _complete_entry()
    _manifest_path(home).write_text(CORRUPT_MANIFESTS[shape], encoding="utf-8")

    assert pull_exists(KEY) is False


def test_a_manifest_that_still_parses_counts_as_present(home) -> None:
    """The deliberate boundary of this function, recorded so it is a decision
    rather than an accident.

    `{"window": "all year"}` is nonsense, but `read_manifest` accepts it and
    `as_of` — the only manifest field the shaping chain actually reads — is
    intact, so the pull *is* derivable and the row *is* openable. `pull_exists`
    is a presence check, not a second manifest validator: widening it to reject
    every field it disagrees with would put a copy of the manifest's rules in
    the recents index, which is where they would silently drift.
    """
    _complete_entry()
    _manifest_path(home).write_text(
        json.dumps(
            {"as_of": "2026-07-01", "window": "all year", "planned": 1, "fetchable": 1,
             "queries": []}
        ),
        encoding="utf-8",
    )

    assert pull_exists(KEY) is True


def test_a_directory_where_the_manifest_belongs_does_not_raise(home) -> None:
    _complete_entry()
    path = _manifest_path(home)
    path.unlink()
    path.mkdir()

    assert pull_exists(KEY) is False


def test_reading_a_corrupt_entry_does_not_repair_or_delete_it(home) -> None:
    """A presence check is a read. The evidence the refresh offer is about must
    still be there afterwards — including the broken manifest, which the user
    may want to inspect or hand-repair."""
    _complete_entry()
    _manifest_path(home).write_text("{ not json", encoding="utf-8")
    before = {
        p: p.read_bytes() for p in (home / "cache").rglob("*") if p.is_file()
    }

    assert pull_exists(KEY) is False

    assert {p: p.read_bytes() for p in (home / "cache").rglob("*") if p.is_file()} == before


@pytest.mark.parametrize(
    "ref",
    ["", "..", "../../secrets", "a/b", "a\\b", "C:\\Windows", ".hidden", "a\x00b", "a" * 300],
    ids=repr,
)
def test_an_unsafe_ref_is_false_rather_than_an_exception(home, ref) -> None:
    """A workspace's `pull_ref` is client-supplied and reaches this function
    straight off a stored file, so it is not necessarily a key this code
    wrote."""
    assert pull_exists(ref) is False


def test_a_ref_that_is_neither_a_cache_key_nor_ws1_falls_through_to_the_fixture_pulls(
    home, tmp_path, monkeypatch
) -> None:
    """The third kind of ref: WS-1's synthetic pulls, which the E2E harness and
    the derive contract tests both address by name."""
    pulls = tmp_path / "fixture-pulls"
    pulls.mkdir()
    monkeypatch.setenv("RENTCOMP_FIXTURE_PULLS_DIR", str(pulls))

    assert pull_exists("synthetic-basic") is False
    (pulls / "synthetic-basic.json").write_text("[]", encoding="utf-8")
    assert pull_exists("synthetic-basic") is True


def test_the_ws1_ref_is_answered_from_the_committed_live_samples(home) -> None:
    """`ws1-real` resolves to the gate's committed raw fixtures, which are in
    the repo — so this is `True` without any cache entry at all."""
    assert pull_exists(WS1_REAL_PULL_REF) is True


# ===========================================================================
# it costs nothing
# ===========================================================================


def test_pull_exists_never_shapes_the_evidence(home, monkeypatch) -> None:
    """Structural, not a stopwatch: the expensive path is bricked up.

    `load_shaped_pull` parses and stitches every raw record. Calling it once per
    row on Home's mount is what the epic's "<1s" budget cannot afford, and it is
    an easy accident — the function that answers "is it there?" and the one that
    answers "what is in it?" live in the same module.
    """
    from rentcomp.storage import pulls as pulls_module

    def _boom(*args, **kwargs):
        raise AssertionError("pull_exists shaped the pull to answer a boolean")

    monkeypatch.setattr(pulls_module, "shape_raw_pull", _boom, raising=True)
    _complete_entry()

    assert pull_exists(KEY) is True


def test_pull_exists_reads_at_most_the_manifest_and_the_raw_listing(home) -> None:
    """The raw responses are never opened — only listed.

    A pull is ~40KB per window; opening them to answer a boolean would make
    Home's mount scale with the size of every search the user has ever run.
    """
    _complete_entry()
    raw = home / "cache" / KEY / "raw"
    (raw / "sig0-off000.json").write_bytes(b"\xff\xfe not even utf-8")

    assert pull_exists(KEY) is True
