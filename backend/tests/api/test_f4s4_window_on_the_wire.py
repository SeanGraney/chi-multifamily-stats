"""F4-S4 [INVARIANT] — the window filter and cohort assignment, as they reach
`POST /api/derive`. QA-authored, written before the developer starts.

WHY ANY OF THIS IS LAYER 2 AT ALL
----------------------------------
Almost all of F4-S4 is Layer 1 and lives in `backend/tests/unit/test_f4s4_*`
— a window boundary is a pure function over plain values and belongs nowhere
near HTTP. Three claims genuinely cannot be made down there, and only those
three are here (AGENT_QA.md's split rule: exhaustive below, representative
above):

1. **The window actually travels.** The bounds a search was pulled under live
   in the cache manifest (`storage/cache.py::write_manifest`, `window=`), and
   `storage/pulls.py::_load_cache_backed_pull` is what feeds them to the
   shaping chain. A pipeline that filtered perfectly against a window nobody
   handed it would pass every Layer-1 test in this story and return the whole
   padded pull to the user. This is the assembled-chain claim — "given a pull
   stored like *this*, the API returns *that*" — which is decision-procedure
   step 2, not step 1.

2. **`cohort_year` survives to the wire.** `DerivedComp.cohort_year` and
   `CohortStat.year` are separate fields on the response; the domain object
   being right does not prove either of them was populated from it.

3. **Window and cohort are Group A** (PM's mid-flight note): they depend on
   the raw pull, config and `as_of` only — never on curation. Group A is
   memoized once per pull, so a window or cohort decision that *did* depend on
   the user's selection would be silently frozen at whichever value the first
   request produced, and would then be wrong for every request after it. That
   is a claim about two HTTP requests differing only in their curation state,
   which has no Layer-1 expression at all.

No Playwright spec accompanies this story: F4-S4 changes no user flow and
renders no new component — it decides which comps exist. Every claim above is
a Python-bug claim, so per the decision procedure it stops at Layer 2.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from rentcomp.storage.config import Config
from rentcomp.storage.pulls import load_shaped_pull

try:
    from rentcomp.storage.cache import cache_key, write_manifest, write_raw_response

    _CACHE_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover
    _CACHE_ERROR = exc

needs_cache = pytest.mark.skipif(
    _CACHE_ERROR is not None,
    reason=f"rentcomp.storage.cache is not importable: {_CACHE_ERROR}",
)

DERIVE_PATH = "/api/derive"

SUBJECT = {
    "address": "1234 W Fake St Unit 2",
    "lat": 41.9,
    "lng": -87.68,
    "sqft": 1000.0,
    "beds": 2.0,
    "baths": 1.0,
}


def _record(id_: str, address: str, listed: date, removed: date | None, price: float = 2000.0) -> dict:
    return {
        "id": id_,
        "formattedAddress": f"{address}, Chicago, IL 60609",
        "addressLine1": address,
        "addressLine2": None,
        "city": "Chicago",
        "state": "IL",
        "zipCode": "60609",
        "latitude": 41.83,
        "longitude": -87.66,
        "propertyType": "Apartment",
        "bedrooms": 2,
        "bathrooms": 1,
        "squareFootage": 900,
        "status": "Active" if removed is None else "Inactive",
        "price": price,
        "listingType": "Standard",
        "listedDate": f"{listed.isoformat()}T00:00:00.000Z",
        "removedDate": f"{removed.isoformat()}T00:00:00.000Z" if removed else None,
        "createdDate": f"{listed.isoformat()}T00:00:00.000Z",
        "lastSeenDate": f"{(removed or listed).isoformat()}T00:00:00.000Z",
        "daysOnMarket": None,
        "history": {},
    }


#: `as_of` for the seeded pull — after every date below, so nothing here
#: depends on the removal ladder (F4-S8) or on a censored DOM floor.
_AS_OF = date(2026, 9, 1)

#: Six units. Three have a stitched start inside a Jun 15-30 window and three
#: do not — including the AC's headline pair, which disagree about the answer
#: depending on whether the raw `listedDate` or the stitched start is read.
_UNITS: dict[str, list[tuple[date, date | None]]] = {
    # inside, plainly
    "10 In Window St": [(date(2025, 6, 15), date(2025, 7, 20))],
    "11 In Window St": [(date(2024, 6, 30), date(2024, 8, 2))],
    # inside only by its STITCHED start: listed Jun 22, re-listed Jul 25, so
    # the raw record on the wire from RentCast says July.
    "12 Stitched In St": [(date(2025, 6, 22), date(2025, 7, 4)), (date(2025, 7, 25), date(2025, 8, 20))],
    # outside only by its STITCHED start: re-listed Jun 20 (inside), but it
    # has actually been sitting since May 12.
    "13 Stitched Out St": [(date(2025, 5, 12), date(2025, 6, 1)), (date(2025, 6, 20), date(2025, 7, 10))],
    # plain padding — exactly what the +/-90d pad fetches and this story drops
    "14 Padding St": [(date(2025, 4, 2), date(2025, 5, 9))],
    "15 Padding St": [(date(2025, 9, 14), date(2025, 10, 30))],
}

_WINDOW = ("06-15", "06-30")

#: What the window must admit, and with which cohort. Keys are prefixes of the
#: response's `address`, so this table states the whole expected outcome of the
#: filter in one place.
_EXPECTED_INSIDE = {
    "10 In Window St": (date(2025, 6, 15), 2025),
    "11 In Window St": (date(2024, 6, 30), 2024),
    "12 Stitched In St": (date(2025, 6, 22), 2025),
}


@pytest.fixture
def seeded_pull(tmp_path, monkeypatch) -> str:
    """A real cache entry: raw RentCast-shaped responses plus a manifest whose
    `window` is the Jun 15-30 one. Returns its `pull_ref`.

    Deliberately built through the shipped `storage/cache.py` writers rather
    than by hand, so this fixture cannot drift from the on-disk layout the
    loader actually reads.
    """
    if _CACHE_ERROR is not None:  # pragma: no cover - gated by `needs_cache`
        pytest.skip(str(_CACHE_ERROR))

    home = tmp_path / "rentcomp-home"
    home.mkdir()
    monkeypatch.setenv("RENTCOMP_HOME", str(home))
    load_shaped_pull.cache_clear()

    active: list[dict] = []
    inactive: list[dict] = []
    for address, segments in _UNITS.items():
        for i, (listed, removed) in enumerate(segments):
            record = _record(f"{address}-{i}", address, listed, removed)
            (active if removed is None else inactive).append(record)

    key = cache_key({"address": "F4-S4 window on the wire", "years_back": 3, "window": list(_WINDOW)})
    write_raw_response(key, "y2026-active-off000", json.dumps(active).encode(), meta={"status": "Active"})
    write_raw_response(key, "y2026-inactive-off000", json.dumps(inactive).encode(), meta={"status": "Inactive"})
    write_manifest(key, as_of=_AS_OF, window=_WINDOW)

    yield key
    load_shaped_pull.cache_clear()


def _body(pull_ref: str, **overrides) -> dict:
    body = {
        "pull_ref": pull_ref,
        "subject": SUBJECT,
        "weights": {},
        "include_overrides": [],
        "filters": {"max_distance_mi": None, "hide_censored": False, "leased_only": False},
        "drift_pct": 7.0,
        "candidate_rent": None,
    }
    body.update(overrides)
    return body


def _derive(client, pull_ref: str, **overrides) -> dict:
    response = client.post(DERIVE_PATH, json=_body(pull_ref, **overrides))
    assert response.status_code == 200, f"{response.status_code}: {response.text}"
    return response.json()


# ---------------------------------------------------------------------------
# 1. the window travels from the manifest into the response
# ---------------------------------------------------------------------------


@needs_cache
def test_the_response_holds_only_comps_whose_stitched_start_is_inside_the_pulls_window(
    client, seeded_pull
) -> None:
    """The story's first clause, end to end.

    `DerivedState.comps` is documented as "every **windowed** comp, including
    excluded and filtered ones" — a filter re-labels, but the window is not a
    filter: a padding-only record is not evidence about this cohort at all and
    must not be in the payload wearing an `excluded` label.
    """
    state = _derive(client, seeded_pull)
    addresses = sorted(comp["address"] for comp in state["comps"])

    assert addresses == sorted(_EXPECTED_INSIDE), (
        f"the derive returned {addresses}; expected exactly {sorted(_EXPECTED_INSIDE)}.\n"
        "  '13 Stitched Out St' present  => the window is being applied to the raw listedDate\n"
        "  '12 Stitched In St' missing   => same cause, other direction\n"
        "  padding units present         => the manifest's window never reached the pipeline"
    )


@needs_cache
def test_the_relisted_pair_is_decided_by_the_stitched_start_not_the_raw_listed_date(
    client, seeded_pull
) -> None:
    """The AC's headline case, on the wire. Two units, both re-listed, whose
    raw and stitched answers point opposite ways — so no single wrong rule can
    satisfy both."""
    state = _derive(client, seeded_pull)
    addresses = {comp["address"] for comp in state["comps"]}

    assert "12 Stitched In St" in addresses, (
        "a unit listed 2025-06-22 and re-listed 2025-07-25 was dropped: its stitched start is "
        "inside the window and only its raw listedDate is outside. Spec §3.2 pays a +/-90d pad "
        "precisely to fetch this record."
    )
    assert "13 Stitched Out St" not in addresses, (
        "a unit sitting since 2025-05-12 was kept because its re-list (2025-06-20) falls inside "
        "the window. Its initial ask is May's price, and it would enter the June cohort median "
        "behind every premium as a comp that does not belong to June."
    )


# ---------------------------------------------------------------------------
# 2. cohort_year on the wire
# ---------------------------------------------------------------------------


@needs_cache
def test_every_comps_cohort_year_on_the_wire_is_its_stitched_starts_calendar_year(
    client, seeded_pull
) -> None:
    """The story's second clause, as the client sees it. `12 Stitched In St`
    is the one that matters: it re-listed in July of the same year, so a
    cohort taken from the raw date is still 2025 — which is why the 2024 unit
    (`11 In Window St`) is in the fixture beside it."""
    state = _derive(client, seeded_pull)
    got = {comp["address"]: comp["cohort_year"] for comp in state["comps"]}
    expected = {address: year for address, (_, year) in _EXPECTED_INSIDE.items()}
    assert got == expected, f"cohort_year on the wire: {got}, expected {expected}"


@needs_cache
def test_the_cohort_stats_cover_exactly_the_years_the_windowed_comps_are_in(
    client, seeded_pull
) -> None:
    """`CohortStat.year` is a second, independently-populated statement of the
    same fact; a cohort list that disagreed with the comps' own `cohort_year`
    would give the rail (F8-S3) and the rows (F5-S1) different stories about
    the same evidence."""
    state = _derive(client, seeded_pull)
    comp_years = sorted({comp["cohort_year"] for comp in state["comps"]})
    stat_years = sorted(stat["year"] for stat in state["cohorts"])
    assert stat_years == comp_years == [2024, 2025], (
        f"cohorts={stat_years}, comps' cohort_year={comp_years}"
    )


@needs_cache
def test_no_padding_record_is_counted_anywhere_in_the_breakdown(client, seeded_pull) -> None:
    """`breakdown.pulled` is the top of F7-S1's `included + excluded +
    filtered == pulled` identity, and it counts *windowed* comps. Padding
    records leaking into it would inflate every count the user reads as "how
    much evidence do I have" with records that were never about their window.
    """
    state = _derive(client, seeded_pull)
    breakdown = state["breakdown"]
    assert breakdown["pulled"] == len(_EXPECTED_INSIDE) == len(state["comps"])
    assert breakdown["included"] + breakdown["excluded"] + breakdown["filtered"] == breakdown["pulled"]
    assert sorted(cohort["year"] for cohort in breakdown["per_cohort"]) == [2024, 2025]


# ---------------------------------------------------------------------------
# 3. Group A — window and cohort are curation-independent
# ---------------------------------------------------------------------------


@needs_cache
@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("all-weights-zero", {"weights": {}}),  # placeholder replaced below
        ("filters-on", {"filters": {"max_distance_mi": 0.0001, "hide_censored": True, "leased_only": True}}),
        ("drift-changed", {"drift_pct": -3.0}),
        ("candidate-rent-set", {"candidate_rent": 2400.0}),
    ],
)
def test_curation_never_changes_which_comps_are_in_the_window_or_which_cohort_they_are_in(
    client, seeded_pull, label, overrides
) -> None:
    """The PM's mid-flight constraint, as an assertion.

    Window and cohort are Group A: functions of the raw pull, config and
    `as_of` alone. Group A is memoized once per pull, so if either decision
    ever became sensitive to curation it would be *frozen* at whatever the
    first request happened to produce — the response would look stable and be
    silently wrong for every later request, which is the worst shape a bug can
    take in a tool whose whole promise is that a number traces to its evidence.

    Every override below changes the curation state and nothing about the
    evidence. `filters-on` is the strongest: it re-labels every comp
    `filtered`, and a filter that *removed* comps rather than re-labelling
    them would show up here as a shrunken comp list (F7-S1's own invariant,
    approached from this story's side).
    """
    if label == "all-weights-zero":
        baseline = _derive(client, seeded_pull)
        overrides = {"weights": {comp["key"]: 0.0 for comp in baseline["comps"]}}

    reference = _derive(client, seeded_pull)
    varied = _derive(client, seeded_pull, **overrides)

    def identity(state: dict) -> list[tuple[str, int]]:
        return sorted((comp["key"], comp["cohort_year"]) for comp in state["comps"])

    assert identity(varied) == identity(reference), (
        f"curation change '{label}' changed the windowed comp set or a cohort assignment.\n"
        f"  reference: {identity(reference)}\n"
        f"  varied   : {identity(varied)}"
    )


@needs_cache
def test_a_narrower_window_on_the_same_evidence_yields_fewer_comps(client, seeded_pull, tmp_path) -> None:
    """The control for every test above: prove the response is actually
    responding to the manifest's window, rather than to something about this
    fixture that happens to select three comps whatever the window says.

    Same six units, same `as_of`, same everything — a different window in the
    manifest, and therefore a different pull_ref. Without this, an
    implementation that ignored the window entirely and returned some fixed
    subset could pass the assertions above.
    """
    narrow = ("06-22", "06-22")
    active: list[dict] = []
    inactive: list[dict] = []
    for address, segments in _UNITS.items():
        for i, (listed, removed) in enumerate(segments):
            record = _record(f"{address}-{i}", address, listed, removed)
            (active if removed is None else inactive).append(record)

    key = cache_key({"address": "F4-S4 narrow window", "years_back": 3, "window": list(narrow)})
    write_raw_response(key, "y2026-active-off000", json.dumps(active).encode(), meta={"status": "Active"})
    write_raw_response(key, "y2026-inactive-off000", json.dumps(inactive).encode(), meta={"status": "Inactive"})
    write_manifest(key, as_of=_AS_OF, window=narrow)

    state = _derive(client, key)
    assert [comp["address"] for comp in state["comps"]] == ["12 Stitched In St"], (
        "a single-day 06-22 window over the same evidence returned "
        f"{[c['address'] for c in state['comps']]}; only the unit whose stitched start is "
        "2025-06-22 belongs to it"
    )
    assert state["comps"][0]["cohort_year"] == 2025
