"""The monthly API-call ledger — WORKFLOW.md §6's cap, enforced in code.

`<RENTCOMP_HOME>/ledger.json`. The plan is **50 calls per month**, and every
one of them is the owner's money, so the cap is a property of the code rather
than of anybody's discipline: the client consults this before it sends and
increments it *at send time*, not at batch completion (ARCHITECTURE.md §5a —
"the quota is spent either way").

THE MONTH STAMP (PM ruling, F0-S4) — why this file has a `month` field
----------------------------------------------------------------------
`scripts/gate.py` writes `{"calls_this_month": int, "history": [...]}` with no
month in it, so "this month" was never actually true: the counter only ever
went up. At 50 lifetime calls the tool would have refused to fetch anything,
permanently, with no way to tell it a new month had started. The fix is one
field, and its migration rule is the whole point:

* **An ABSENT month reads as the CURRENT month.** The 8 calls already spent
  (`fixtures/live-samples/ledger.json`) stay counted today and roll off at the
  start of next month. It must never read as "unknown, therefore reset to 0" —
  that would silently hand back calls that have already been paid for.
* **A malformed month also reads as the current month**, count preserved. Same
  reasoning, same direction: when in doubt, do not hand back budget.
* **Only a well-formed month strictly EARLIER than today's resets the count.**
  A month in the future (a clock skew, or a hand-edit) keeps the count — the
  fail-closed direction — while the in-memory ledger restamps itself to the
  current month so the next save gets back on a normal cycle.

`load_ledger()` never writes. Reading is not a mutation, and a keyless
fixture-mode run must be able to import this module without creating anything.

The history entries hold params and outcomes and **never a credential** — this
is exactly the sort of file that gets pasted into a report.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from rentcomp.storage import rentcomp_home

__all__ = [
    "MONTHLY_CALL_CAP",
    "LEDGER_FILENAME",
    "CallRecord",
    "Ledger",
    "LedgerError",
    "current_month",
    "ledger_path",
    "load_ledger",
    "save_ledger",
]

#: WORKFLOW.md §6 / CLAUDE.md. The plan's monthly allowance.
MONTHLY_CALL_CAP = 50

LEDGER_FILENAME = "ledger.json"

#: `YYYY-MM`. Zero-padded, so plain string ordering is chronological ordering.
_MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class LedgerError(Exception):
    """The ledger file exists but cannot be understood.

    Deliberately loud rather than "assume zero": a ledger that silently resets
    on a parse error is a ledger that hands the month's remaining budget back
    to whatever corrupted it. The message names the path so the user can go
    look.
    """


@dataclass(frozen=True)
class CallRecord:
    """One request, as it is remembered on disk.

    `status` is `None` between "sent" and "answered" — a call that was billed
    but whose outcome we never learned (a crash, a dropped connection). That is
    a real state and it is worth being able to see.
    """

    sig: str
    params: dict
    status: int | None = None
    total_count: int | None = None

    def as_json(self) -> dict:
        return {
            "sig": self.sig,
            "params": self.params,
            "status": self.status,
            "total_count": self.total_count,
        }


@dataclass(frozen=True)
class Ledger:
    """Calls spent in `month`, and what they were.

    Immutable: `with_call` / `with_outcome` return a new ledger, so the caller
    can never accidentally mutate the count without going through a save.
    """

    month: str
    calls_this_month: int
    history: tuple[CallRecord, ...] = ()

    @property
    def remaining(self) -> int:
        """Calls left in the month. Never negative, even if the file says so."""
        return max(0, MONTHLY_CALL_CAP - self.calls_this_month)

    def with_call(self, *, sig: str, params: dict) -> "Ledger":
        """+1 spent, plus a pending history entry. Persist this BEFORE sending."""
        return replace(
            self,
            calls_this_month=self.calls_this_month + 1,
            history=(*self.history, CallRecord(sig=sig, params=dict(params))),
        )

    def with_outcome(self, *, status: int | None, total_count: int | None) -> "Ledger":
        """Fill in the outcome of the most recent call. Never changes the count —
        the call was billed whatever the far end said."""
        if not self.history:
            return self
        last = replace(self.history[-1], status=status, total_count=total_count)
        return replace(self, history=(*self.history[:-1], last))

    def as_json(self) -> dict:
        # `calls_this_month` and `history` keep the gate's spelling and order so
        # the two files that can spend money stay readable side by side.
        return {
            "month": self.month,
            "calls_this_month": self.calls_this_month,
            "history": [record.as_json() for record in self.history],
        }


def current_month(today: date | None = None) -> str:
    """Today's `YYYY-MM`."""
    return (today or date.today()).strftime("%Y-%m")


def ledger_path() -> Path:
    """``<RENTCOMP_HOME>/ledger.json`` — resolved at call time (never snapshotted)."""
    return rentcomp_home() / LEDGER_FILENAME


def load_ledger() -> Ledger:
    """Read the ledger, applying the month rule. Never writes anything.

    A missing file is a fresh month at zero — the normal state of a new
    install. A file that is present but unreadable/unparseable raises
    `LedgerError`; see the module docstring for why that is not a silent zero.
    """
    path = ledger_path()
    month_now = current_month()

    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return Ledger(month=month_now, calls_this_month=0)
    except (OSError, UnicodeDecodeError) as exc:
        raise LedgerError(f"cannot read the call ledger {path}: {exc}") from exc

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LedgerError(f"the call ledger {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise LedgerError(
            f"the call ledger {path} must be a JSON object, got {type(payload).__name__}"
        )

    spent = payload.get("calls_this_month", 0)
    if isinstance(spent, bool) or not isinstance(spent, int) or spent < 0:
        raise LedgerError(
            f"the call ledger {path} has a non-numeric 'calls_this_month' ({spent!r}). "
            "Fix it by hand — guessing here would either hand back calls that were paid "
            "for or block calls that were not."
        )

    stored_month = payload.get("month")
    if isinstance(stored_month, str) and _MONTH_PATTERN.match(stored_month):
        if stored_month < month_now:
            # A genuinely past month: the allowance has rolled over.
            return Ledger(month=month_now, calls_this_month=0)
        # Same month (or, defensively, a future one) — keep the count and
        # restamp to today; the restamp only reaches disk on the next save.
        return Ledger(
            month=month_now,
            calls_this_month=spent,
            history=_history(payload.get("history")),
        )

    # ABSENT or malformed month => this month, count preserved. This is the
    # migration path for the gate's committed ledger (8 calls, no month stamp):
    # those 8 stay spent today and roll off next month.
    return Ledger(
        month=month_now,
        calls_this_month=spent,
        history=_history(payload.get("history")),
    )


def save_ledger(ledger: Ledger) -> Path:
    """Persist the ledger; return the path.

    tmp -> fsync -> rename (ARCHITECTURE.md §5a). A crash mid-write must never
    leave a ledger that parses as a *smaller* number than what was spent.
    """
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(ledger.as_json(), indent=2) + "\n"

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


def _history(value: object) -> tuple[CallRecord, ...]:
    """Best-effort read of the history list.

    Lenient on purpose, and *only* here: history is diagnostic, the count is
    the money. An entry we cannot read is skipped rather than being allowed to
    take down a run whose budget arithmetic is perfectly sound.
    """
    if not isinstance(value, list):
        return ()
    records = []
    for entry in value:
        if not isinstance(entry, dict):
            continue
        records.append(
            CallRecord(
                sig=str(entry.get("sig", "")),
                params=entry.get("params") if isinstance(entry.get("params"), dict) else {},
                status=entry.get("status") if isinstance(entry.get("status"), int) else None,
                total_count=(
                    entry.get("total_count")
                    if isinstance(entry.get("total_count"), int)
                    else None
                ),
            )
        )
    return tuple(records)
