"""Row 37c — the recents index never DERIVES, pinned structurally (Layer 2).

    **Epic F1 Success (verbatim):** workspace restored in <1s, zero API calls.

`test_f1s2_zero_calls.py` already pins the second half of that sentence: no
workspace route reaches `RentCastClient.fetch_listings`. This file pins the
FIRST half, which nothing pinned before — and which is not the same claim.

WHY "ZERO API CALLS" DOES NOT IMPLY "<1s"
-----------------------------------------
Deriving costs no network at all. A `GET /api/workspaces` that shaped and
derived every stored workspace to put an `anchor` on every row would keep every
existing guard in this directory green — no fetch, no byte under `cache/`
touched, no ledger movement — while Home got slower in proportion to how many
searches the owner has ever saved. The cost is real (record shaping is I/O +
parsing over every raw response in the entry) and it is paid on *launch*, which
is the one render the user cannot avoid.

That is not a hypothetical. **F1-S1's recents table has an `anchor` column**
(epic F1 flow step 1: "address, specs, radius, anchor, age"), and deriving one
per row is the single most natural way to implement it. `RecentWorkspace`'s
docstring defers the question — "F1-S1 decides what to do about that column" —
so the column is still live and the tempting implementation is still available.

WHAT THIS FILE PINS, AND WHAT IT DELIBERATELY DOES NOT
------------------------------------------------------
It pins the **mechanism**: no workspace route may reach the record-shaping /
derivation machinery. It does **not** pin the response's field list. If F1-S1
comes back needing an anchor on each row and the owner rules that it should be
*snapshotted at save time* and read back off the workspace file, every test
here still passes — a stored number is not a derivation. Pinning "no `anchor`
key" instead would forbid the good answer along with the expensive one, and
would also collide with a design decision that is the PM's and the owner's,
not QA's.

WHY NOT A WALL-CLOCK ASSERTION
-------------------------------
`assert elapsed < 1.0` is the obvious reading of the criterion and the wrong
test. It passes on an idle machine against an implementation that derives four
workspaces, fails on a busy one against an implementation that derives none,
and its verdict depends on how many workspaces happen to be on disk. F1-S2's
QA declined to write one for exactly this reason and that judgement stands. A
structural assertion that the expensive call is never made is strictly stronger
than a threshold: it cannot go flaky, and it fails at the moment the cost is
*introduced* rather than at the moment the cost happens to exceed a number.

HOW THE TRIPWIRE IS ARMED — BOTH SIDES OF EVERY DOOR
-----------------------------------------------------
Naively monkeypatching `rentcomp.storage.pulls.load_shaped_pull` is a tripwire
that a real implementation walks straight past. `rentcomp/api/derive.py` line
29 is `from rentcomp.storage.pulls import ... load_shaped_pull` — a module-level
`from`-import binds the function object into the *consumer's* namespace at
import time, and the app is fully imported before any test runs. Patching the
defining module then changes nothing the route can see. A developer adding a
per-row anchor to `api/workspaces.py` would import it exactly that way.

So each door is armed twice:

* **Defining side**, `raising=True` — including `storage.pulls._shaped_pull`,
  the private, `lru_cache`d function every `load_shaped_pull(...)` call reaches
  through a late-bound module-global lookup. That is the seam that survives any
  import style at the caller. `raising=True` is the point: if a refactor renames
  one of these, arming fails loudly here instead of leaving this file asserting
  over a dead monkeypatch.
* **Consumer side**, `raising=False` — the same names installed into
  `rentcomp.api.workspaces` and `rentcomp.storage.workspace`, so a future
  `from ... import load_shaped_pull` at the top of either resolves to the raiser
  at call time.

`DerivationAttempted` derives from `BaseException` on purpose: `pull_exists`
and the workspace store both catch broad tuples of ordinary exceptions, and a
tripwire that a legitimate `except (OSError, ValueError, ...)` can swallow would
turn "this route derives" into "this row is marked broken" — a quieter failure
than no test at all. Every assertion is made against the recorded attempt list
regardless, so a swallowed raise is still caught.

ANTI-VACUITY (the first two tests, and they are not decoration)
---------------------------------------------------------------
`test_the_derivation_tripwire_actually_fires` proves the seam is the one a
derivation really goes through, by driving `POST /api/derive` — a route that
`from`-imported `load_shaped_pull` — into it, and asserting the attempt was
recorded *at `_shaped_pull`*. That label is the whole proof: it is what
distinguishes a tripwire that works from one that only appears to.
"""

from __future__ import annotations

import importlib
import shutil
from typing import Any, Callable

import pytest
from f1s2_support import (
    DERIVE_PATH,
    OTHER_SEARCH_BODY,
    curation,
    curation_of,
    fully_curated,
    row_error,
    rows_by_key,
)


class DerivationAttempted(BaseException):
    """Raised by an armed door. `BaseException` so `except Exception` can't eat it."""


#: The expensive doors, on their DEFINING module. Armed with `raising=True`:
#: a rename must fail arming rather than silently disarm this file.
EXPENSIVE_DOORS: tuple[tuple[str, str], ...] = (
    # The seam that survives any import style at the caller: `load_shaped_pull`
    # reaches `_shaped_pull` by module-global lookup at call time.
    ("rentcomp.storage.pulls", "_shaped_pull"),
    ("rentcomp.storage.pulls", "load_shaped_pull"),
    # Record shaping, reachable independently of the loader. F4-S4 split
    # `shape_raw_pull` into a thin wrapper over `shape_raw_pull_with_summary`,
    # so both are doors and the wrapper is not the bottom of the stack.
    ("rentcomp.storage.pulls", "shape_raw_pull"),
    ("rentcomp.pipeline.shape", "shape_raw_pull"),
    ("rentcomp.pipeline.shape", "shape_raw_pull_with_summary"),
    # The derivation itself — an anchor per row needs this as well as a loader.
    ("rentcomp.pipeline.derive", "derive"),
)

#: Modules that must never grow a call to any of the above. Armed with
#: `raising=False`, which is what covers a `from ... import` added tomorrow.
CONSUMER_MODULES: tuple[str, ...] = (
    "rentcomp.api.workspaces",
    "rentcomp.storage.workspace",
)

EXPENSIVE_NAMES: tuple[str, ...] = tuple(sorted({name for _, name in EXPENSIVE_DOORS}))


@pytest.fixture
def no_derivation_allowed(monkeypatch) -> list[str]:
    """Brick up every way a workspace route could derive. Returns the attempts.

    Returning the list rather than relying on the raise is deliberate: a caller
    that wrapped the door in `try/except` would otherwise convert "this route
    derives" into "this row is broken", and the response alone cannot tell the
    two apart.
    """
    attempts: list[str] = []

    def _armed(label: str, original: Any) -> Callable[..., Any]:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            attempts.append(label)
            raise DerivationAttempted(
                f"a workspace route called {label}. Record shaping and derivation are the "
                "expensive half of the recents index; epic F1's budget is 'restored in <1s' "
                "and this cost is paid on Home's mount, once per stored workspace. "
                "`pull_exists` answers 'is there evidence at this ref' with a stat and a "
                "manifest read for exactly this reason."
            )

        # Keep `cache_clear`/`cache_info` reachable so `clear_pipeline_caches`
        # (backend/tests/api/conftest.py) still finds the memo controls while a
        # door is armed — its teardown runs before monkeypatch unwinds.
        for attribute in ("cache_clear", "cache_info"):
            carried = getattr(original, attribute, None)
            if carried is not None:
                setattr(_boom, attribute, carried)
        return _boom

    for module_name, attribute in EXPENSIVE_DOORS:
        module = importlib.import_module(module_name)
        original = getattr(module, attribute, None)
        assert original is not None, (
            f"{module_name}.{attribute} does not exist, so this file is arming a door that is "
            "no longer there. Every assertion in it would pass for the wrong reason. If the "
            "function was renamed, rename it in EXPENSIVE_DOORS; if it was removed, the pin "
            "needs re-aiming at whatever replaced it."
        )
        monkeypatch.setattr(
            module, attribute, _armed(f"{module_name}.{attribute}", original), raising=True
        )

    for module_name in CONSUMER_MODULES:
        module = importlib.import_module(module_name)
        for attribute in EXPENSIVE_NAMES:
            original = getattr(module, attribute, None)
            monkeypatch.setattr(
                module,
                attribute,
                _armed(f"{module_name}.{attribute}", original),
                raising=False,
            )

    return attempts


def _call(send: Callable[[], Any], attempts: list[str], what: str) -> Any:
    """Run a request, turning a propagating `DerivationAttempted` into a verdict.

    Starlette's error middleware catches `Exception`, not `BaseException`, so an
    armed door escapes the `TestClient` rather than becoming a 500. Rewriting it
    into an `AssertionError` here keeps the failure legible.
    """
    try:
        return send()
    except BaseException as exc:  # noqa: BLE001 - re-raised either way
        if not attempts:
            raise
        raise AssertionError(f"{what} reached {attempts[-1]}: {exc}") from exc


# ===========================================================================
# 0 — anti-vacuity: the tripwire is aimed at a door a derivation really uses
# ===========================================================================


def test_the_derivation_tripwire_actually_fires(api, pull, no_derivation_allowed) -> None:
    """A route that *does* derive must trip this, or nothing below means anything.

    `POST /api/derive` is the proof precisely because `rentcomp/api/derive.py`
    binds `load_shaped_pull` with a module-level `from`-import: the only way it
    can trip is through `storage.pulls._shaped_pull`, the late-bound seam. If
    this file were armed only at `storage.pulls.load_shaped_pull` — the obvious
    place — the derive route would sail past it and every "never derives"
    assertion below would be vacuous against exactly the implementation it
    exists to catch.
    """
    try:
        api.post(DERIVE_PATH, json=curation(pull.ref))
    except BaseException:  # noqa: BLE001 - the tripwire, or a genuine failure
        pass

    assert no_derivation_allowed, (
        f"POST {DERIVE_PATH} did not reach any armed door. A request that derives the whole "
        "state MUST pass through one of them, so the tripwire is pointed at the wrong "
        f"function and every assertion in this file is vacuous. Armed: {EXPENSIVE_DOORS}"
    )
    assert "rentcomp.storage.pulls._shaped_pull" in no_derivation_allowed, (
        "the derive route tripped, but not at `storage.pulls._shaped_pull`: "
        f"{no_derivation_allowed}. That specific label is the proof that this tripwire "
        "survives a consumer doing `from rentcomp.storage.pulls import load_shaped_pull` "
        "(which api/derive.py does, and which a per-row anchor in api/workspaces.py would "
        "do too). Without it, patching the defining module changes nothing the caller sees."
    )


def test_the_workspace_module_namespace_is_actually_armed(no_derivation_allowed) -> None:
    """The consumer-side half: the names are live in the module that must not use them.

    Arming the fixture at all asserts every door in `EXPENSIVE_DOORS` still
    exists (`raising=True`). This adds the other half — that each expensive name
    is now bound *inside* `rentcomp.api.workspaces`, which is where a
    `from`-import added by a future developer would land, and therefore where
    the call would resolve from at request time.
    """
    module = importlib.import_module("rentcomp.api.workspaces")

    for name in EXPENSIVE_NAMES:
        door = getattr(module, name, None)
        assert callable(door), (
            f"`rentcomp.api.workspaces.{name}` is not armed, so a route in that module could "
            "call it without this file noticing"
        )
        with pytest.raises(DerivationAttempted):
            door()

    assert len(no_derivation_allowed) == len(EXPENSIVE_NAMES)


# ===========================================================================
# 1 — the recents index
# ===========================================================================


def test_listing_recents_never_derives_anything(
    workspaces, saved_workspace, another_pull, no_derivation_allowed
) -> None:
    """`GET /api/workspaces` with the whole derivation machinery bricked up.

    Two stored workspaces, not one: the failure mode this pins is per-row work,
    which one row cannot distinguish from a fixed cost. `pull_exists` answers
    the only question a row needs — "is there evidence at this ref" — with a
    stat and a manifest read, and that is the property under test.
    """
    key, _ = saved_workspace
    second = another_pull(OTHER_SEARCH_BODY)
    assert workspaces.save(second.ref, curation(second.ref)).status_code < 300

    response = _call(workspaces.recents, no_derivation_allowed, "listing recents")

    assert no_derivation_allowed == [], (
        f"building the recents index derived: {no_derivation_allowed}. Home renders this on "
        "mount, once per stored workspace, against epic F1's '<1s' budget — and it costs no "
        "API call, so every zero-call guard in this directory stays green while it happens."
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"

    rows = rows_by_key(response)
    assert {key, second.ref} <= set(rows), (
        f"the index lost a row while the derivation doors were shut: {sorted(rows)}"
    )
    for row_id in (key, second.ref):
        assert row_error(rows[row_id]) is None, (
            f"row {row_id} came back marked broken ({row_error(rows[row_id])!r}). Its evidence "
            "is on disk and readable; a row that only looks healthy when a derivation succeeds "
            "is deriving."
        )


def test_a_row_whose_evidence_is_gone_is_answered_without_deriving(
    workspaces, saved_workspace, pull, no_derivation_allowed
) -> None:
    """The "missing evidence" verdict is a presence check, not a failed derivation.

    The tempting implementation of `_missing_evidence` is
    `try: load_shaped_pull(ref) except PullNotFoundError: return "..."`. It
    produces exactly the right error row for the broken case — and shapes every
    raw response on disk for every *healthy* case, which is the expensive path
    nobody would notice, because the only visible symptom is Home being slow.
    """
    key, _ = saved_workspace
    shutil.rmtree(pull.entry)

    response = _call(workspaces.recents, no_derivation_allowed, "listing recents")

    assert no_derivation_allowed == [], (
        f"deciding a row's evidence was missing went through {no_derivation_allowed}. "
        "Whether a ref resolves is a `stat` plus a manifest read; shaping the pull to find "
        "out costs the healthy rows the same work."
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"

    rows = rows_by_key(response)
    assert key in rows, f"the row vanished when its evidence did: {sorted(rows)}"
    assert row_error(rows[key]) is not None, (
        "the row claims to be healthy after its cache entry was deleted — F1's edge is an "
        "error row offering refresh, and it must be reached without deriving"
    )


# ===========================================================================
# 2 — opening one recent, and saving one
# ===========================================================================


def test_opening_one_recent_never_derives_anything(
    workspaces, saved_workspace, no_derivation_allowed
) -> None:
    """`GET /api/workspaces/{key}` restores curation; the numbers come later.

    D5: the client has one source for a derived value, `POST /api/derive`. A
    load that pre-derived would be a second one — and would put the expensive
    work on the click as well as on the mount, which is the other half of the
    "<1s" the epic asks for.
    """
    key, state = saved_workspace

    response = _call(lambda: workspaces.load(key), no_derivation_allowed, "loading a recent")

    assert no_derivation_allowed == [], (
        f"opening a recent derived before returning the curation state: {no_derivation_allowed}"
    )
    assert response.status_code == 200, f"{response.status_code}: {response.text[:300]}"

    restored = curation_of(response.json())
    assert restored.get("candidate_rent") == state["candidate_rent"], (
        f"the curation state did not survive: {restored}"
    )
    assert restored.get("drift_pct") == state["drift_pct"]


def test_saving_a_workspace_never_derives_anything(
    workspaces, pull, comp_keys, no_derivation_allowed
) -> None:
    """`PUT /api/workspaces/{key}` — the route F14-S2 fires on every mutation.

    "Saving is not deriving" is in the route's docstring, which no run can
    fail. It matters more here than on either GET: F14-S2's autosave is
    debounced but still runs on drags and toggles, so a derive on the save path
    is paid over and over during ordinary curation, not once on mount.
    """
    state = fully_curated(pull.ref, comp_keys)

    response = _call(
        lambda: workspaces.save(pull.ref, state), no_derivation_allowed, "saving a workspace"
    )

    assert no_derivation_allowed == [], (
        f"saving curation state derived: {no_derivation_allowed}. F14-S2 autosaves on every "
        "workspace mutation; a derive here is paid on every slider drag."
    )
    assert response.status_code < 300, f"{response.status_code}: {response.text[:300]}"
