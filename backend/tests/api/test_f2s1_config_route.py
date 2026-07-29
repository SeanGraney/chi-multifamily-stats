"""`GET` / `PUT /api/config` — Layer 2 contract tests (PM ruling 1 on F2-S1).

QA-authored, written RED before any implementation exists.

WHY THIS STORY OWNS THESE ROUTES
---------------------------------
ARCHITECTURE.md §4 lists `GET/PUT /api/config` in the API surface. F0-S5 built
the *store* (`storage/config.py`) and nothing else — there is no route, and no
other story in the backlog claims one. The PM assigned it here because F2-S1 is
the story that finally puts a Settings surface in front of a user.

The store's contract is already Layer-1 exhaustive (`test_config_store.py`);
none of it is repeated here. What this file pins is the **edge translation**,
which is a different question and is where a store contract goes wrong:

* an out-of-contract knob is a `ValueError` in-process → **422** on the wire
  (a 500 would tell the user their tool is broken when their input was);
* a `ConfigError` (the FILE is bad) is **not** a `ValueError` and must not be
  laundered into one — and must never silently become defaults, because a
  tuned 30-day stitch gap quietly reverting to 42 reclassifies leases and
  moves every downstream number with zero signal (F0-S5's own rationale);
* an **`OSError` on the write path must be translated by the handler itself**.
  This is the sharp edge and the reason the PM called it out: `save_config`
  deliberately raises bare `OSError` (2026-07-26 ruling — "a full disk is not
  'your file's contents are bad'"), so a handler that only catches
  `ValueError`/`ConfigError` lets an `OSError` escape as an anonymous 500 with
  a plain-text body and no actionable message.

Layer choice (AGENT_QA.md decision procedure): every assertion here is "given
state X, the API returns Y" — step 2, Layer 2. None of it needs a browser. The
Settings *screen* that drives these routes is L3 and lives in
`e2e/tests/f2-s1-search-form.spec.ts`.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

CONFIG_PATH = "/api/config"

#: §2.3, verbatim — the values a fresh install must report. Restated here
#: rather than imported from `Config()` on purpose: importing the defaults from
#: the thing under test would make the assertion a tautology that survives
#: someone editing a default by mistake.
SPEC_DEFAULTS: dict[str, Any] = {
    "stitch_gap_days": 42,
    "provisional_lease_days": 7,
    "withdrawal_suspect_months": 6,
    "knn_k": 7,
    "bucket_half_width_pct": 4.0,
    "min_cohort_size": 4,
    "drift_source": "manual",
    "km_horizons_days": [14, 30, 45, 60],
}


def route_present(app: Any, path: str, method: str) -> bool:
    """True when `app` exposes `method path` (see `tests/api/conftest.py`)."""
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


@pytest.fixture
def client(app, rentcomp_home):
    """A `TestClient`, or a legible skip while the config route is absent."""
    if not route_present(app, CONFIG_PATH, "GET") or not route_present(app, CONFIG_PATH, "PUT"):
        pytest.skip(f"GET/PUT {CONFIG_PATH} not implemented yet (F2-S1 red)")
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def tolerant_client(app, rentcomp_home):
    """A `TestClient` that turns an *unhandled* exception into a 500 response
    rather than re-raising it into the test.

    Needed for exactly one assertion: "an `OSError` is translated by the
    handler". With the default client an untranslated `OSError` would surface
    as an errored test, which is red but says nothing about the wire. With this
    one, translated and untranslated are both 5xx and are told apart by the
    body — Starlette's fallback is `text/plain` "Internal Server Error", a real
    handler returns JSON naming what to fix.
    """
    if not route_present(app, CONFIG_PATH, "GET") or not route_present(app, CONFIG_PATH, "PUT"):
        pytest.skip(f"GET/PUT {CONFIG_PATH} not implemented yet (F2-S1 red)")
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False)


# ===========================================================================
# existence — the one loud failure everything else skips behind
# ===========================================================================


def test_the_config_route_exists(app) -> None:
    """ARCHITECTURE.md §4 names it; F0-S5 built the store but no route; no
    other story in the backlog owns one (PM ruling, QUEUE row 35)."""
    missing = [
        method for method in ("GET", "PUT") if not route_present(app, CONFIG_PATH, method)
    ]
    assert not missing, (
        f"{'/'.join(missing)} {CONFIG_PATH} absent from the app's OpenAPI document. "
        "ARCHITECTURE.md §4 lists this route; F0-S5 deliberately built only the store "
        "(storage/config.py) and the PM assigned the route to this story. Without it the "
        "Settings screen has nothing to read or write, and §2.3's knobs are hand-edited-file "
        "only."
    )


# ===========================================================================
# GET — the knobs as they are, never as they might be
# ===========================================================================


def test_get_returns_the_spec_2_3_defaults_on_a_fresh_home(client) -> None:
    """First run has no `config.json`, and that is a normal state, not an error
    (`load_config` returns `Config()` and never writes)."""
    response = client.get(CONFIG_PATH)
    assert response.status_code == 200, f"{response.status_code}: {response.text[:400]}"
    payload = response.json()
    for knob, expected in SPEC_DEFAULTS.items():
        assert payload.get(knob) == expected, (
            f"{knob}={payload.get(knob)!r}, spec §2.3 says {expected!r}"
        )


def test_get_never_creates_a_config_file(client, rentcomp_home) -> None:
    """`load_config()` never writes (F0-S5 contract). Reading the Settings
    screen must not turn today's defaults into a frozen file that stops
    tracking a future default change."""
    client.get(CONFIG_PATH)
    assert not (rentcomp_home / "config.json").exists(), (
        "reading the config wrote a config file — F0-S5: load_config() never writes, no "
        "file, no directory, no debris"
    )


def test_get_does_not_expose_the_internal_query_padding_as_a_knob(client) -> None:
    """§2.3: "Query padding | 90 days | fixed, internal". It is a property on
    the value object, not a field — surfacing it in Settings invites editing
    it, and shrinking PAD silently drops re-listed records before the stitcher
    ever sees them (F4-S1)."""
    payload = client.get(CONFIG_PATH).json()
    assert "query_padding_days" not in payload, (
        "query_padding_days must not appear in the config wire shape — it is fixed and "
        "internal (§2.3), and a Settings screen that renders it makes it look adjustable"
    )


def test_get_surfaces_a_corrupt_config_file_instead_of_silently_defaulting(
    tolerant_client, rentcomp_home, clear_caches
) -> None:
    """F0-S5's loudest invariant, at the edge: never a silent fall-back.

    A user whose tuned 30-day stitch gap quietly becomes 42 gets different
    lease classifications and a different anchor with nothing on screen saying
    so. `ConfigError` names the path; the route has to carry that through.
    """
    (rentcomp_home / "config.json").write_text("{ not json", encoding="utf-8")
    clear_caches()

    response = tolerant_client.get(CONFIG_PATH)
    assert response.status_code >= 400, (
        f"a corrupt config.json returned {response.status_code} — if that is a 200 carrying "
        f"defaults, the user is being shown knobs that are not the ones on disk: "
        f"{response.text[:300]}"
    )
    assert "config" in response.text.lower(), (
        "the error must name the offending file so the user can go fix it (F0-S5: every "
        f"ConfigError message names the path). Got: {response.text[:300]}"
    )


# ===========================================================================
# PUT — persistence and the ValueError → 422 translation
# ===========================================================================


def test_put_persists_every_knob_and_get_reflects_it(client, rentcomp_home, clear_caches) -> None:
    """The round trip a Settings screen makes: save, reload, see what you saved."""
    saved = {
        **SPEC_DEFAULTS,
        "stitch_gap_days": 30,
        "knn_k": 5,
        "bucket_half_width_pct": 6.5,
        "drift_source": "manual",
        "km_horizons_days": [7, 21, 60],
    }
    response = client.put(CONFIG_PATH, json=saved)
    assert response.status_code in {200, 204}, (
        f"PUT {CONFIG_PATH} returned {response.status_code}: {response.text[:400]}"
    )

    on_disk = json.loads((rentcomp_home / "config.json").read_text(encoding="utf-8"))
    assert on_disk["stitch_gap_days"] == 30
    assert on_disk["km_horizons_days"] == [7, 21, 60]

    clear_caches()
    reloaded = client.get(CONFIG_PATH).json()
    for knob, expected in saved.items():
        assert reloaded.get(knob) == expected, f"{knob} did not survive the round trip"


def test_put_accepts_a_partial_body_or_refuses_it_but_never_half_applies_it(
    client, rentcomp_home, clear_caches
) -> None:
    """Whether the wire shape is a full `Config` or a patch is the developer's
    [DEFAULT] (PM ruling names the body as "the Config model", so a full body
    must work). What is NOT optional either way: a rejected PUT leaves the
    stored knobs exactly as they were — a half-applied save is the one outcome
    that leaves the user unable to tell what the math is using.
    """
    client.put(CONFIG_PATH, json={**SPEC_DEFAULTS, "stitch_gap_days": 30})
    clear_caches()

    response = client.put(CONFIG_PATH, json={"stitch_gap_days": 11})
    clear_caches()
    after = client.get(CONFIG_PATH).json()

    if response.status_code in {200, 204}:
        assert after["stitch_gap_days"] == 11
        assert after["knn_k"] == SPEC_DEFAULTS["knn_k"]
    else:
        assert response.status_code == 422, (
            f"a partial body must be either applied or refused with 422, got "
            f"{response.status_code}: {response.text[:300]}"
        )
        assert after["stitch_gap_days"] == 30, (
            "a refused PUT changed the stored config — a failed save must be a no-op"
        )


@pytest.mark.parametrize(
    ("knob", "value", "why"),
    [
        ("stitch_gap_days", 6, "§2.3 range 7-60, below floor"),
        ("stitch_gap_days", 61, "§2.3 range 7-60, above ceiling"),
        ("provisional_lease_days", 22, "§2.3 range 3-21"),
        ("withdrawal_suspect_months", 1, "§2.3 range 2-10"),
        ("knn_k", 2, "§2.3 range 3-15"),
        ("knn_k", 16, "§2.3 range 3-15"),
        ("bucket_half_width_pct", 1.5, "§2.3 range ±2-10 percentage POINTS"),
        ("bucket_half_width_pct", 10.5, "§2.3 range ±2-10 percentage POINTS"),
        ("min_cohort_size", 1, "§2.3 range 2-10"),
        ("drift_source", "Manual", "canonical lowercase only (F0-S5)"),
        ("drift_source", "vendor", "not one of zip|cohort|manual"),
        ("km_horizons_days", [], "must not be empty"),
        ("km_horizons_days", [30, 14, 60], "must be strictly increasing, never silently sorted"),
        ("km_horizons_days", [14, 14, 30], "strictly increasing excludes duplicates"),
        ("km_horizons_days", [0, 30], "horizons must be positive"),
    ],
)
def test_put_translates_an_out_of_contract_knob_into_422(
    client, rentcomp_home, knob: str, value: Any, why: str
) -> None:
    """PM ruling 1: `ValueError` → **422** at the API edge.

    Never clamped and never replaced by the default: a clamped knob is a lie —
    the user sees the value they typed while the math uses another one (F0-S5).
    A 500 here would be equally wrong in the other direction: it blames the
    tool for the user's typo, and it is what an un-caught `ValidationError`
    produces if the handler validates by hand instead of at the model.
    """
    response = client.put(CONFIG_PATH, json={**SPEC_DEFAULTS, knob: value})
    assert response.status_code == 422, (
        f"{knob}={value!r} ({why}) returned {response.status_code}, expected 422: "
        f"{response.text[:300]}"
    )
    assert not (rentcomp_home / "config.json").exists(), (
        f"a rejected {knob} was still written to disk — a refused save must not persist"
    )


def test_put_rejects_an_unknown_knob(client) -> None:
    """`extra="forbid"`: an unrecognized key is nearly always a typo of a real
    knob, and ignoring it leaves the user believing they set something they did
    not (F0-S5)."""
    response = client.put(CONFIG_PATH, json={**SPEC_DEFAULTS, "stitch_gap_dayz": 30})
    assert response.status_code == 422, (
        f"a typo'd knob returned {response.status_code}; it must be a loud 422, not a "
        f"silently ignored key: {response.text[:300]}"
    )


def test_put_rejects_the_internal_query_padding(client) -> None:
    """`query_padding_days` is fixed and internal (§2.3) — a property, not a
    field. Accepting it from the wire would make PAD look adjustable."""
    response = client.put(CONFIG_PATH, json={**SPEC_DEFAULTS, "query_padding_days": 30})
    assert response.status_code == 422, (
        f"PUT accepted query_padding_days ({response.status_code}) — §2.3 fixes it, and F4-S1 "
        "depends on it being 90 on both sides of every window"
    )


# ===========================================================================
# PM ruling 1, the sharp edge — OSError must be translated by the handler
# ===========================================================================


def test_put_translates_an_oserror_into_a_named_5xx_not_an_anonymous_500(
    tolerant_client, rentcomp_home
) -> None:
    """The failure mode the PM called out by name.

    `save_config` raises **bare `OSError`** on the write path, deliberately and
    with a recorded rationale (2026-07-26: wrapping it in `ConfigError` would
    dilute that error to mean two different things — "your file's contents are
    bad" and "your disk is full" need different user actions). Consequence: a
    handler that catches only `ValueError`/`ConfigError` lets `OSError` escape,
    and the user gets a bare 500 with `text/plain` "Internal Server Error" and
    no idea that their save did not happen.

    The failure is provoked through the filesystem rather than by monkeypatching
    `save_config`, so the test is indifferent to how the handler imports it: a
    directory sitting where `config.json` belongs makes the final
    `os.replace(tmp, path)` fail on every platform.
    """
    stub = rentcomp_home / "config.json"
    stub.mkdir()
    (stub / "occupied.txt").write_text("not a config file", encoding="utf-8")

    response = tolerant_client.put(CONFIG_PATH, json=dict(SPEC_DEFAULTS))

    assert response.status_code != 200, "a save that could not happen must not report success"
    assert 500 <= response.status_code < 600, (
        f"an unwritable config home is a server-side condition, expected 5xx, got "
        f"{response.status_code}: {response.text[:300]}"
    )
    content_type = response.headers.get("content-type", "")
    assert "json" in content_type, (
        "the response is Starlette's unhandled-exception fallback "
        f"(content-type {content_type!r}), which means the OSError escaped the handler. PM "
        "ruling 1: `OSError` → 5xx must be translated by the handler ITSELF, because "
        "save_config deliberately never raises ConfigError on the write path."
    )
    detail = json.dumps(response.json()).lower()
    assert "config" in detail, (
        f"the translated error must name what could not be written so the user can act on it; "
        f"got {response.text[:300]}"
    )
