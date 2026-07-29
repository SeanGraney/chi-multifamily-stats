"""F1-S2 — constants, request builders and response readers.

Plain module rather than part of `conftest.py` so the test files can import it
by name: two `conftest` modules are in play here (F0-S2's at
`backend/tests/api/` and F1-S2's beside this file) and `import conftest` from a
test would be ambiguous between them. Fixtures live in `conftest.py`;
everything importable lives here.

THE CONTRACT THESE HELPERS ENCODE (QA-PROPOSED, PM to ratify) — see
`conftest.py`'s docstring for the reasoning. Where a reader tolerates more than
one field name it says so in its failure message, so a rejected test names the
vocabulary it accepted rather than looking like a typo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

WORKSPACES_PATH = "/api/workspaces"
SEARCH_PATH = "/api/search"
DERIVE_PATH = "/api/derive"

TODAY = date.today()

#: The search every F1-S2 test re-uses. Its cache key IS the workspace key —
#: `client/pull.py::pull_ref_for` is `storage/cache.py::cache_key`, which is
#: what makes "curation state -> workspaces/<cache-key>.json" addressable.
SEARCH_BODY: dict[str, Any] = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "bathrooms": None,
    "property_types": ["Multi-Family", "Apartment", "Townhouse"],
    "years_back": 3,
    "window_start": "01-01",
    "window_end": "12-31",
}

PLAN_ARGS = {
    "address": SEARCH_BODY["address"],
    "radius": SEARCH_BODY["radius"],
    "bedrooms": SEARCH_BODY["bedrooms"],
    "bathrooms": SEARCH_BODY["bathrooms"],
    "property_types": SEARCH_BODY["property_types"],
    "years_back": SEARCH_BODY["years_back"],
    "window_start_mmdd": SEARCH_BODY["window_start"],
    "window_end_mmdd": SEARCH_BODY["window_end"],
}

#: A second, different search — a second cache key, so the recents index has
#: more than one thing to be an index over.
OTHER_SEARCH_BODY: dict[str, Any] = {**SEARCH_BODY, "address": "1200 W 18th St, Chicago, IL 60608"}
THIRD_SEARCH_BODY: dict[str, Any] = {**SEARCH_BODY, "address": "4400 N Sheridan Rd, Chicago, IL 60640"}

#: The subject unit. Fixed, so any difference between two derive responses is
#: caused by the curation state under test and nothing else.
SUBJECT: dict[str, Any] = {
    "address": "3651 S Wood St Unit 2",
    "lat": 41.8286,
    "lng": -87.6716,
    "sqft": 1000.0,
    "beds": 3.0,
    "baths": 1.0,
}

#: Accepted names for "which workspace is this row about". The index is keyed
#: on the cache key, which is also the `pull_ref` — both names are honest.
KEY_FIELDS = ("key", "cache_key", "pull_ref", "id")

#: Accepted names for the recents table's `age` column source (F1-S1 renders
#: `age`, and D5 forbids the frontend inventing it).
TIMESTAMP_FIELDS = (
    "saved_at",
    "updated_at",
    "modified_at",
    "last_opened_at",
    "cached_at",
    "fetched_at",
    "as_of",
    "age_days",
)

#: Accepted names for an envelope around the curation state in a GET body.
STATE_FIELDS = ("state", "curation", "workspace", "data")

#: Accepted names for "this row could not be read" on a recents row. The AC
#: says "error row", so something has to say so.
ERROR_FIELDS = ("error", "err", "problem", "failure", "corrupt", "broken")

#: Accepted names for "and here is how to fix it" — the AC's refresh offer.
REFRESH_FIELDS = ("refreshable", "can_refresh", "offer_refresh", "refresh", "recoverable")


def record(n: int, *, days_old: int | None = None) -> dict:
    """One RentCast-shaped listing. Distinct address per `n`, therefore a
    distinct `comp_key` (F13-S1: comps are addressed by address+unit, never by
    a churning listing id)."""
    age = 150 + n if days_old is None else days_old
    return {
        "id": f"f1s2-{n}",
        "formattedAddress": f"{800 + n} N Verify Blvd, Chicago, IL 60609",
        "addressLine1": f"{800 + n} N Verify Blvd",
        "latitude": 41.82,
        "longitude": -87.67,
        "price": 2000 + 50 * n,
        "bedrooms": 3,
        "bathrooms": 1,
        "squareFootage": 900 + 10 * n,
        "listedDate": f"{(TODAY - timedelta(days=age)).isoformat()}T00:00:00.000Z",
        "status": "Active",
    }


def curation(pull_ref: str, **overrides: Any) -> dict[str, Any]:
    """A complete curation state in `DeriveRequest`'s vocabulary."""
    payload: dict[str, Any] = {
        "pull_ref": pull_ref,
        "subject": dict(SUBJECT),
        "weights": {},
        "include_overrides": [],
        "filters": {
            "max_distance_mi": None,
            "hide_censored": False,
            "leased_only": False,
        },
        "drift_pct": 0.0,
        "candidate_rent": None,
    }
    payload.update(overrides)
    return payload


def fully_curated(pull_ref: str, keys: list[str]) -> dict[str, Any]:
    """A workspace with EVERY field moved off its default.

    Every value here is one a naive serializer drops or flattens: a `0.0`
    weight (falsy, and "toggle-off IS weight 0" — F5-S2 [INVARIANT]), a
    non-empty override list, all three filters set, a negative drift (markets
    go down), and a candidate rent — the field the AC names by name.
    """
    return curation(
        pull_ref,
        weights={keys[0]: 3.5, keys[1]: 0.0},
        include_overrides=[keys[1]],
        filters={
            "max_distance_mi": 1.25,
            "hide_censored": True,
            "leased_only": True,
        },
        drift_pct=-2.5,
        candidate_rent=2450.0,
    )


@dataclass
class Pull:
    """A real, fixture-mode pull: a cache entry on disk and the ref for it."""

    ref: str
    fixtures_dir: Any
    home: Any

    @property
    def entry(self):
        return self.home / "cache" / self.ref

    def raw_snapshot(self) -> dict[str, bytes]:
        """Every raw response byte currently filed under this pull.

        The structural half of "zero API calls": these bytes were paid for
        once, and nothing a workspace does may add to them, change them or
        replace them. Asserted instead of a ledger counter, which cannot move
        in fixture mode and would make the assertion inert against a route that
        re-pulled everything (F4-S9 QA).
        """
        return {p.name: p.read_bytes() for p in (self.entry / "raw").glob("*.json")}

    def raw_stat_snapshot(self) -> dict[str, tuple[int, int]]:
        """`(size, mtime_ns)` per raw response — the stronger form of the pin.

        Bytes alone would not notice a re-fetch that happened to write the same
        content, which in fixture mode is exactly what a re-pull looks like.
        """
        out = {}
        for p in (self.entry / "raw").glob("*.json"):
            stat = p.stat()
            out[p.name] = (stat.st_size, stat.st_mtime_ns)
        return out

    def drop_fixtures(self) -> None:
        """Delete the saved responses a re-fetch would need.

        The other half of the pin: with these gone a route that went back to
        the source cannot quietly succeed and look identical — it either fails
        or files different bytes, and both are visible on disk.
        """
        for saved in self.fixtures_dir.glob("*.json"):
            saved.unlink()


def cache_snapshot(home) -> dict[str, bytes]:
    """Every byte under `cache/`, whole tree — ARCHITECTURE.md §5's immutable
    half, including the manifests."""
    root = home / "cache"
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def route_present(app: Any, path: str, method: str) -> bool:
    """True when `app` exposes `method path`.

    Asked of the OpenAPI document first: FastAPI 0.140 keeps an included router
    as one nested entry in `app.routes` instead of flattening it, so a naive
    scan reports "missing" for every router-registered endpoint (F0-S2's
    conftest found this). The flat scan stays as a fallback.
    """
    try:
        operations = (app.openapi().get("paths") or {}).get(path) or {}
    except Exception:  # pragma: no cover - defensive
        operations = {}
    if method.lower() in operations:
        return True
    for route in getattr(app, "routes", []):
        if getattr(route, "path", None) != path:
            continue
        if method in (getattr(route, "methods", None) or {method}):
            return True
    return False


def _param_variants(template: str) -> list[str]:
    """The same route with any plausible name for its path parameter — the
    parameter's *name* is the developer's call, its position is not."""
    return [template.replace("{key}", "{%s}" % name)
            for name in ("key", "cache_key", "workspace_key", "pull_ref", "ref")]


def missing_routes(app: Any) -> list[str]:
    """Which of ARCHITECTURE.md §4's three workspace routes are absent."""
    absent = []
    for method, template in (
        ("GET", WORKSPACES_PATH),
        ("GET", WORKSPACES_PATH + "/{key}"),
        ("PUT", WORKSPACES_PATH + "/{key}"),
    ):
        candidates = [template] if "{key}" not in template else _param_variants(template)
        if not any(route_present(app, candidate, method) for candidate in candidates):
            absent.append(f"{method} {template}")
    return absent


# ---------------------------------------------------------------------------
# response readers
# ---------------------------------------------------------------------------


def curation_of(payload: Any) -> dict:
    """The curation state inside a `GET /api/workspaces/{key}` body.

    Tolerates a thin envelope, so the exactness tests pin the *values* rather
    than a wrapper shape the developer has not chosen yet.
    """
    assert isinstance(payload, dict), (
        f"a stored workspace must come back as a JSON object, got {type(payload).__name__}"
    )
    if "weights" in payload or "candidate_rent" in payload:
        return payload
    for field in STATE_FIELDS:
        nested = payload.get(field)
        if isinstance(nested, dict):
            return nested
    raise AssertionError(
        f"could not find the curation state in the response body {sorted(payload)}. Expected "
        f"the `DeriveRequest` fields at the top level, or nested under one of {STATE_FIELDS}."
    )


def as_derive_body(loaded: Any, key: str) -> dict:
    """A restored workspace, ready to POST to `/api/derive`.

    Fills in `pull_ref` when the stored state omits it: the workspace key IS
    the cache key IS the `pull_ref`, so a developer who leaves it out of the
    body has not lost anything, and a test that failed over its absence would
    be pinning an envelope rather than the restoration.
    """
    state = dict(curation_of(loaded))
    state.setdefault("pull_ref", key)
    return state


def row_key(row: Any) -> str:
    """The workspace a recents row is about."""
    assert isinstance(row, dict), f"a recents row must be a JSON object, got {row!r}"
    for field in KEY_FIELDS:
        value = row.get(field)
        if isinstance(value, str) and value:
            return value
    raise AssertionError(
        f"recents row {sorted(row)} identifies no workspace. F1-S1 has to open the row it "
        f"renders, so one of {KEY_FIELDS} must carry the cache key."
    )


def row_timestamp(row: Any) -> Any:
    """Whatever on the row makes F1-S1's `age` column computable."""
    for field in TIMESTAMP_FIELDS:
        if row.get(field) is not None:
            return row[field]
    raise AssertionError(
        f"recents row {sorted(row)} carries nothing to compute an age from. F1-S1's table has "
        f"an `age` column and D5 forbids the frontend deriving a value from nothing, so one of "
        f"{TIMESTAMP_FIELDS} must be present."
    )


def row_error(row: Any) -> Any:
    """The row's error marker, or `None` if it claims to be healthy."""
    assert isinstance(row, dict), f"a recents row must be a JSON object, got {row!r}"
    for field in ERROR_FIELDS:
        value = row.get(field)
        if value:
            return value
    return None


def row_offers_refresh(row: Any) -> bool:
    """Whether the row tells F1-S1 that refresh is the way out.

    A truthy refresh flag, or an error message that says the word — either is
    enough for the AC's "offers refresh"; an error row with no route out of it
    is not.
    """
    for field in REFRESH_FIELDS:
        if row.get(field):
            return True
    error = row_error(row)
    return isinstance(error, str) and "refresh" in error.lower()


def _rows(payload: Any) -> list:
    rows = payload if isinstance(payload, list) else None
    if rows is None and isinstance(payload, dict):
        rows = payload.get("workspaces", payload.get("items", payload.get("recents")))
    assert isinstance(rows, list), (
        f"GET {WORKSPACES_PATH} must return a list of recents rows (optionally wrapped in "
        f"`workspaces`/`items`/`recents`); got {type(payload).__name__}: {str(payload)[:200]}"
    )
    return rows


def rows_in(response: Any) -> list[dict]:
    return _rows(response.json())


def keys_in(response: Any) -> list[str]:
    """The workspace keys a `GET /api/workspaces` response lists, in order."""
    return [row_key(row) for row in rows_in(response)]


def rows_by_key(response: Any) -> dict[str, dict]:
    """The recents rows, addressable by the workspace they are about."""
    return {row_key(row): row for row in rows_in(response)}


def is_json_error(response: Any) -> bool:
    """True when a >=400 response is a *deliberate* error rather than a crash.

    An unhandled exception under `raise_server_exceptions=False` comes back as
    a plain-text "Internal Server Error" with no `detail`. That is the
    anonymous 500 "never a crash" forbids, and it is exactly what F4-S9's QA
    found two corruption shapes escaping as — a `TypeError`/`AttributeError`
    raised inside coercion, past an `except (OSError, ValueError, KeyError)`.
    """
    try:
        body = response.json()
    except Exception:
        return False
    return isinstance(body, dict) and body.get("detail") is not None
