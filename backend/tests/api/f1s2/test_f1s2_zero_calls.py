"""F1-S2 AC2 (Layer 2) — "...with zero API calls", pinned STRUCTURALLY.

    **AC (story text, verbatim):** open recent → full state restored
    (including candidate rent and filter settings) with zero API calls; ...

    **Epic F1 Success:** workspace restored in <1s, zero API calls.

HOW THIS IS ASSERTED, AND WHY NOT WITH A COUNTER
-------------------------------------------------
`assert ledger.calls_this_month == 0` is **inert in fixture mode** — the ledger
only moves on the live path, so that assertion passes against a route that
re-pulled every window. This project has caught four tests-that-cannot-fail and
one was exactly that shape (F4-S9). So the money claim is pinned three ways,
each of which works identically in fixture mode and on the live path:

1.  **The mechanism is absent.** `RentCastClient.fetch_listings` — the single
    door `client/pull.py` reaches the source through, in both modes — is
    replaced with a raiser for the duration (`no_fetch_allowed`).
2.  **The evidence is untouched.** Every raw response under
    `cache/<key>/raw/` is byte-identical AND stat-identical afterwards, and no
    second cache entry appeared. A re-pull in fixture mode rewrites the same
    bytes, so bytes alone would not notice it; the mtime does.
3.  **A re-fetch could not have quietly succeeded.** The saved responses a
    re-fetch would need are deleted first, so a route that went back to the
    source cannot look identical to one that did not — it either fails or files
    something different.

ARCHITECTURE.md §5's SEPARATION IS THE SAME TEST FROM THE OTHER SIDE
--------------------------------------------------------------------
"`cache/` is immutable raw API responses; `workspaces/` is mutable curation
state." A save or a load that wrote anything under `cache/` would break the
property that makes refresh and re-derivation safe (F3-S1), so the whole
`cache/` tree — manifests included — is snapshotted around every workspace
operation here, not just the `raw/` directory the story names.
"""

from __future__ import annotations

from f1s2_support import (
    WORKSPACES_PATH,
    as_derive_body,
    cache_snapshot,
    curation,
    fully_curated,
)


# ===========================================================================
# 0 — the tripwire is real (ungated: a guard that cannot fire proves nothing)
# ===========================================================================


def test_the_no_fetch_tripwire_actually_fires(api, fixture_mode, no_fetch_allowed) -> None:
    """Prove the seam this file pins is the seam a fetch goes through.

    Runs today, before F1-S2 exists, because a tripwire aimed at the wrong
    function is the same defect as a counter that cannot move: every "zero API
    calls" assertion above would pass for the wrong reason and nobody would
    know. A cache-missing search MUST trip it.
    """
    from f1s2_support import SEARCH_BODY, SEARCH_PATH

    response = api.post(SEARCH_PATH, json={**SEARCH_BODY, "address": "9 W Tripwire Ave, Chicago, IL"})

    assert no_fetch_allowed, (
        f"a cache-missing POST {SEARCH_PATH} did NOT reach RentCastClient.fetch_listings "
        f"(status {response.status_code}). The tripwire is pointed at the wrong door, so every "
        "zero-call assertion in this file would be vacuous."
    )


def test_the_partial_pull_fixture_really_has_a_gap(partial_pull, api) -> None:
    """The other harness precondition, also ungated: the "incomplete pull"
    fixture must genuinely be missing a window, or the test built on it is
    about nothing."""
    from rentcomp.storage.cache import read_manifest

    manifest = read_manifest(partial_pull.ref)
    assert manifest.missing, (
        f"the partial-pull fixture's manifest reports nothing missing: {manifest}"
    )
    assert manifest.calls_to_complete >= 1


# ===========================================================================
# 1 + 2 + 3 — opening a recent is free
# ===========================================================================


def test_opening_a_recent_never_reaches_the_fetch_seam(
    workspaces, saved_workspace, no_fetch_allowed
) -> None:
    """The whole F1 flow — list, load, derive — with the fetch door bricked up.

    This is the AC in its most direct form: not "no calls were counted" but
    "the code that makes calls was never entered". It is the assertion that
    survives a move to the live path unchanged.
    """
    key, _ = saved_workspace

    recents = workspaces.recents()
    assert recents.status_code == 200, f"listing recents: {recents.text[:300]}"

    loaded = workspaces.load(key)
    assert loaded.status_code == 200, f"loading the recent: {loaded.text[:300]}"

    derived = workspaces.derive(as_derive_body(loaded.json(), key))
    assert derived.status_code == 200, (
        f"deriving the restored workspace: {derived.status_code} {derived.text[:300]}"
    )
    assert no_fetch_allowed == [], f"the flow tried to fetch: {no_fetch_allowed}"


def test_opening_a_recent_leaves_the_paid_for_evidence_byte_identical(
    workspaces, saved_workspace, pull
) -> None:
    """The bytes on disk were bought once and are never bought again (D24).

    The saved responses are deleted first, so this cannot pass by accident: a
    route that re-pulled would have nothing to serve and would either fail or
    file a different (empty) result. Byte AND stat comparison, because a
    fixture-mode re-pull writes the same content back.
    """
    key, _ = saved_workspace
    bytes_before = pull.raw_snapshot()
    stats_before = pull.raw_stat_snapshot()
    entries_before = sorted(p.name for p in (pull.home / "cache").iterdir())
    assert bytes_before, "no raw responses on disk — this test would prove nothing"

    pull.drop_fixtures()

    assert workspaces.recents().status_code == 200
    loaded = workspaces.load(key)
    assert loaded.status_code == 200, (
        f"the workspace could not be reopened once the saved responses were gone "
        f"({loaded.status_code}) — a load that depends on the source is not free: "
        f"{loaded.text[:300]}"
    )
    assert workspaces.derive(as_derive_body(loaded.json(), key)).status_code == 200

    assert pull.raw_snapshot() == bytes_before, (
        "the raw responses changed while a workspace was being opened. They are immutable "
        "evidence that was already paid for (ARCHITECTURE.md §5/§5a)."
    )
    assert pull.raw_stat_snapshot() == stats_before, (
        "the raw response files were rewritten (same bytes, new mtime) — that is a re-fetch "
        "that happened to land on identical fixture content, and on the live path it is the "
        "whole pull bought a second time"
    )
    assert sorted(p.name for p in (pull.home / "cache").iterdir()) == entries_before, (
        "a second cache entry appeared while opening a recent search"
    )


def test_opening_a_recent_over_an_INCOMPLETE_pull_still_costs_nothing(
    workspaces, partial_pull, no_fetch_allowed, api
) -> None:
    """A gap in the evidence is not permission to go and fill it.

    §5a says completing a partial pull costs exactly the remainder — real
    money — so it is the user's decision in F3-S2's modal, never a side effect
    of clicking a recent row. A load that "helpfully" finished the pull would
    be the most expensive possible reading of "restore the workspace".
    """
    state = curation(partial_pull.ref, drift_pct=3.0, candidate_rent=2100.0)
    assert workspaces.save(partial_pull.ref, state).status_code < 300

    bytes_before = partial_pull.raw_snapshot()
    loaded = workspaces.load(partial_pull.ref)

    assert loaded.status_code == 200, (
        f"a workspace over an incomplete pull could not be opened ({loaded.status_code}): "
        f"{loaded.text[:300]}. A partial pull is usable, with the gap named loudly (§5a)."
    )
    assert no_fetch_allowed == [], f"opening it tried to fetch the missing window: {no_fetch_allowed}"
    assert partial_pull.raw_snapshot() == bytes_before


def test_listing_recents_never_reaches_the_fetch_seam(
    workspaces, saved_workspace, another_pull, no_fetch_allowed
) -> None:
    """Home renders on launch. If building the recents index could fetch, the
    tool would spend money for merely being opened."""
    from f1s2_support import OTHER_SEARCH_BODY

    second = another_pull(OTHER_SEARCH_BODY)
    assert workspaces.save(second.ref, curation(second.ref)).status_code < 300

    response = workspaces.recents()

    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"
    assert no_fetch_allowed == []


# ===========================================================================
# ARCHITECTURE.md §5 — curation state and raw evidence never touch
# ===========================================================================


def test_saving_a_workspace_never_writes_under_cache(
    workspaces, pull, comp_keys, rentcomp_home
) -> None:
    """`cache/` is immutable; `workspaces/` is the mutable half.

    The whole tree is compared, manifests included — a save that restamped a
    manifest would change what `as_of` the pipeline shapes under, which is what
    decides whether a comp is censored.
    """
    before = cache_snapshot(rentcomp_home)
    assert before, "nothing under cache/ — this test would prove nothing"

    assert workspaces.save(pull.ref, fully_curated(pull.ref, comp_keys)).status_code < 300

    assert cache_snapshot(rentcomp_home) == before, (
        "saving curation state modified the raw-response store. ARCHITECTURE.md §5: "
        "`cache/` is immutable raw API responses, `workspaces/` is mutable curation state — "
        "the separation is what makes refresh safe (F3-S1)."
    )


def test_loading_a_workspace_never_writes_under_cache(
    workspaces, saved_workspace, rentcomp_home
) -> None:
    """Reading is never a mutation (F0-S5 P3), and here it is never a purchase
    either."""
    key, _ = saved_workspace
    before = cache_snapshot(rentcomp_home)

    assert workspaces.recents().status_code == 200
    assert workspaces.load(key).status_code == 200

    assert cache_snapshot(rentcomp_home) == before, (
        "reading a workspace modified something under cache/"
    )


def test_curation_state_is_filed_outside_the_cache_entry(
    workspaces, saved_workspace, rentcomp_home, workspace_files
) -> None:
    """The state lands in its own store, keyed by the cache key.

    ARCHITECTURE.md §5 names `workspaces/<cache-key>.json` and the story says
    the same; §5 is binding at the architecture level (the story text says so
    itself), so the *location* is pinned here — but nothing about the file's
    internal schema is, which is the part the [DEFAULT] tag covers.
    """
    key, _ = saved_workspace
    files = workspace_files()

    assert files, f"nothing was written outside cache/ under {rentcomp_home}"
    named = [p for p in files if key in p.name]
    assert named, (
        f"no file under {rentcomp_home} outside cache/ is named for the workspace key {key}. "
        f"ARCHITECTURE.md §5: curation state lives at `workspaces/<cache-key>.json`. Found: "
        f"{[str(p.relative_to(rentcomp_home)) for p in files]}"
    )
    assert any(p.parent.name == "workspaces" for p in named), (
        f"the workspace file is not under `workspaces/`: "
        f"{[str(p.relative_to(rentcomp_home)) for p in named]}"
    )


def test_the_workspace_store_survives_the_evidence_being_deleted(
    workspaces, saved_workspace, pull
) -> None:
    """Curation is not stored inside the pull it curates.

    The complement of the separation: deleting the cache entry must not take
    the user's curation with it. The workspace then names a pull that is gone,
    which is F1's "missing cache entry" edge — an error row offering refresh,
    not silently vanished work.
    """
    import shutil

    key, state = saved_workspace
    shutil.rmtree(pull.entry)

    response = workspaces.load(key)
    assert response.status_code != 500 or response.headers.get("content-type", "").startswith(
        "application/json"
    ), f"loading a workspace whose evidence was deleted crashed: {response.text[:300]}"

    listed = workspaces.recents()
    assert listed.status_code == 200, (
        f"listing recents after a cache entry was deleted returned {listed.status_code}: "
        f"{listed.text[:300]}. F1's edge is an error row, never a broken Home screen."
    )
    assert key in listed.text, (
        f"the workspace {key} disappeared from recents when its cache entry was deleted. The "
        "curation state is still on disk and the row must still appear — offering refresh, "
        "which is the only thing that can bring the evidence back."
    )


def test_a_workspace_never_appears_inside_the_raw_directory(
    workspaces, saved_workspace, pull
) -> None:
    """`cache/<key>/raw/` holds paid-for API responses and nothing else.

    A curation file filed in there would be indistinguishable from evidence to
    `storage/cache.py::raw_response_paths`, which globs `*.json` — the shaping
    chain would then try to read the user's weights as RentCast records.
    """
    key, _ = saved_workspace
    raw_names = sorted(p.name for p in (pull.entry / "raw").glob("*"))

    assert not any(key in name and "off" not in name for name in raw_names), (
        f"a workspace-looking file is filed under cache/{key}/raw/: {raw_names}"
    )
