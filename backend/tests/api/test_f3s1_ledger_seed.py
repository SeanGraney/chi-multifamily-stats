"""F3-S1 [BE] — the ledger-seeding precondition folded into the dispatch (PM
ruling, not the story-doc text). QA-authored, written RED before any
implementation exists (AGENT_QA.md protocol).

THE AC THIS FILE OWNS (PM-relayed, dispatch item 1)
-----------------------------------------------------
    "Seed `<RENTCOMP_HOME>/ledger.json` from the gate's 8 already-spent
     calls ... on a fresh machine the product's own ledger starts at 0, so
     the code-enforced 50/month cap would permit 50 new calls when only 42
     actually remain. This story must make the product ledger aware of that
     spend — this is a hard precondition for any live-mode run, not a
     nice-to-have."

WHY THIS IS F3-S1'S, NOT F0-S4'S
---------------------------------
F0-S4 built the ledger *mechanism* (the month-rollover rule, the atomic
write, the cap) and it is done — `storage/ledger.py` already treats an
ABSENT month as "this month, count preserved" specifically so the gate's 8
calls would survive a migration. But nothing today ever COPIES the gate's
committed `fixtures/live-samples/ledger.json` onto a machine's real
`<RENTCOMP_HOME>/ledger.json` — a brand-new install still starts at
`calls_this_month == 0`. F0-S4's rule protects the count once it is there;
F3-S1 is what puts it there. (The PM's dispatch draws this line explicitly,
and it sits naturally alongside this story's other "make the real cache
finally live" work.)

WHY LAYER 2
-----------
This is "given state X (a fresh RENTCOMP_HOME, live mode requested), the
client's ledger-consulting boundary returns Y (a budget that already
accounts for 8 spent calls)" — an assembled-behaviour assertion over the
client's public surface, same reasoning and same fixtures as
`test_rentcast_client_surface.py` (`live_client`, `home`, a mocked
transport, zero live calls).

CONTRACT
--------
I don't pin *where* the seed happens (on `load_ledger()` itself, at app
startup, as a one-time migration file copy) — only the OUTCOME: by the time
a live call is about to be sent from a machine that has never run before,
the effective ledger already reflects the gate's spend. `load_ledger()` is
the client's actual read boundary (`client/rentcast.py::_fetch_live` calls
it directly), so asserting through it is the least implementation-coupled
way to hold this without guessing a seeding function's name.

ZERO LIVE CALLS: every test drives the client end-to-end through
`live_client(...)`, which only ever hands it an `httpx.MockTransport` — see
`test_rentcast_client_surface.py`'s own docstring for the safety argument;
this file reuses the identical pattern.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_LEDGER = REPO_ROOT / "fixtures" / "live-samples" / "ledger.json"

try:
    from rentcomp.client.rentcast import RentCastClient
    from rentcomp.storage.ledger import MONTHLY_CALL_CAP, load_ledger

    _IMPORT_ERROR: Exception | None = None
except ImportError as _exc:  # pragma: no cover - already green today (F0-S4 landed)
    _IMPORT_ERROR = _exc

needs_client = pytest.mark.skipif(
    _IMPORT_ERROR is not None, reason=f"rentcomp client/ledger not importable: {_IMPORT_ERROR}"
)

#: Not a credential.
FAKE_KEY = "qa-fake-key-not-a-real-credential"

QUERY = {
    "address": "3651 S Wood St, Chicago, IL 60609",
    "radius": 2.0,
    "bedrooms": "3:4",
    "propertyType": "Multi-Family|Apartment|Townhouse|Single Family|Condo",
    "status": "Active",
    "daysOld": "1:1095",
    "limit": 500,
    "includeTotalCount": "true",
}


def gate_calls_already_spent() -> int:
    payload = json.loads(GATE_LEDGER.read_text(encoding="utf-8"))
    return int(payload["calls_this_month"])


@pytest.fixture
def fresh_home(tmp_path, monkeypatch) -> Path:
    """A machine that has NEVER run this product before: no ledger.json at
    all, storage root pointed at an empty temp dir. This is the state F1-S1
    of the actual owner's install (or any dev machine after a `git clone`)
    starts from — not a QA convenience."""
    root = tmp_path / "brand-new-rentcomp-home"
    root.mkdir()
    for name in ("RENTCOMP_LIVE", "RENTCAST_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENTCOMP_HOME", str(root))
    assert not (root / "ledger.json").exists(), "test setup bug: ledger.json already present"
    return root


def constant_response(total: int = 0):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], headers={"X-Total-Count": str(total)})

    return responder


@needs_client
def test_the_gate_ledger_fixture_really_has_eight_spent_calls() -> None:
    """Pins the number this whole file is about, against the actual
    committed fixture rather than a hardcoded literal — if the gate ever
    spends differently, this test (not a magic "8") is what moves."""
    assert gate_calls_already_spent() == 8


@needs_client
def test_a_brand_new_install_ledger_already_accounts_for_the_gates_spend(fresh_home: Path) -> None:
    """THE core AC. Before this story: `load_ledger()` on a fresh
    `RENTCOMP_HOME` returns `calls_this_month == 0` — that IS the F0-S4
    contract for an unseeded install, and it is exactly the bug this AC
    exists to close. After F3-S1: a fresh install's ledger must already
    reflect at least the gate's already-spent calls, without a single live
    call having been made in this test."""
    spent = gate_calls_already_spent()
    ledger = load_ledger()
    assert ledger.calls_this_month >= spent, (
        f"a brand-new RENTCOMP_HOME reports {ledger.calls_this_month} calls spent; expected "
        f"at least the gate's {spent} already-spent calls to be seeded in. Un-seeded, the "
        f"50/month cap would hand back {MONTHLY_CALL_CAP - ledger.calls_this_month} calls that "
        "were already paid for."
    )
    assert ledger.remaining <= MONTHLY_CALL_CAP - spent


@needs_client
def test_seeding_happens_before_the_first_live_call_is_even_possible(
    fresh_home: Path, monkeypatch
) -> None:
    """This is a HARD PRECONDITION for live mode, not just a fact about
    `load_ledger()` in isolation: the very first live call this process ever
    attempts must already be counted against the seeded baseline, proven by
    driving the real client (mocked transport, zero network) rather than
    reading the ledger file directly."""
    spent_before = gate_calls_already_spent()
    monkeypatch.setenv("RENTCOMP_LIVE", "1")
    monkeypatch.setenv("RENTCAST_API_KEY", FAKE_KEY)
    client = RentCastClient(transport=httpx.MockTransport(constant_response()))
    client.fetch_listings(dict(QUERY), max_calls=1)

    ledger = load_ledger()
    assert ledger.calls_this_month >= spent_before + 1, (
        f"after one live call from a fresh install, the ledger shows {ledger.calls_this_month} "
        f"spent; expected at least {spent_before + 1} (the gate's {spent_before} seeded calls "
        "plus this one)"
    )


@needs_client
def test_seeding_never_double_counts_on_a_second_read(fresh_home: Path) -> None:
    """Whatever the seeding mechanism is, it must not re-apply the gate's 8
    calls every time `load_ledger()` is called — that would make the budget
    shrink on every read of an idle process."""
    first = load_ledger().calls_this_month
    second = load_ledger().calls_this_month
    third = load_ledger().calls_this_month
    assert first == second == third, (
        f"repeated load_ledger() calls on the same untouched home returned {first}, {second}, "
        f"{third} — seeding must be idempotent, not applied fresh on every read"
    )


@needs_client
def test_an_already_seeded_home_is_not_reseeded_on_top(fresh_home: Path) -> None:
    """A ledger that already has its own real spend (e.g. the owner has made
    live calls since installing) must not have the gate's 8 calls added a
    SECOND time on top — seeding is a one-time bootstrap from "never run
    before", not an unconditional additive migration applied forever."""
    (fresh_home / "ledger.json").write_text(
        json.dumps({"calls_this_month": 15, "history": []}), encoding="utf-8"
    )
    ledger = load_ledger()
    assert ledger.calls_this_month == 15, (
        f"a home with its own already-recorded spend (15) came back as {ledger.calls_this_month} "
        "— seeding must only apply to a machine that has genuinely never run before"
    )
