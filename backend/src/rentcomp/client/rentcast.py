"""The RentCast client (F0-S4) — the only code in this project that can spend money.

Two modes, and the default is the free one:

    fixture  (DEFAULT)  serves the raw responses `scripts/gate.py` already paid
                        for, from `fixtures/live-samples/`. Zero network, no
                        key, no ledger entry. Every test, every dev server and
                        every QA run is this mode (WORKFLOW.md §6).
    live                requires BOTH `RENTCOMP_LIVE=1` AND a key (D17). Never
                        the default, never reachable by accident, and every
                        request is counted against a 50/month cap before it is
                        sent.

Endpoint: `GET /listings/rental/long-term` only. That is not a convenience
choice — real listing records are the evidence every downstream number traces
back to, and substituting RentCast's own model output would produce numbers
that look identical and mean something else entirely (NORTH_STAR;
SEMANTIC_CHANGE_PROTOCOL §2 — owner sign-off, never a code change).

WHAT F0-S4 OWNS, AND WHAT IT DELIBERATELY DOES NOT
--------------------------------------------------
Owns: the wire call, pagination via `offset`/`X-Total-Count`, the D17 mode
guard, the 401/429 error taxonomy, the call ledger, and an honest
`complete`/`missing` report on the result.

Does NOT own, on purpose:

* **Parsing records into DTOs.** `models/dto.py` is F4-S2's, written against
  `fixtures/live-samples/` ground truth. Records come back as raw dicts here.
  D24 wants the bytes safe before anything validates them anyway: a validation
  bug must never cost a call.
* **Writing the cache.** F3-S1 owns `cache/<key>/raw/`, F3-S4 the resumable
  manifest. What this story owes them is the `sink` seam (below) and a
  countable gap.
* **The query PLAN** — which windows, how many years, PAD=90 — is F4-S1's.
* **Range parsing from user input** is F2-S2's. `client/query.py` only emits.

THE `sink` SEAM (D24 write-through)
-----------------------------------
`sink(params, raw_bytes, meta)` is called once per *call*, with the response
body exactly as it arrived, **before anything parses it**. Per call rather than
per pull because §5a's per-call file granularity is what makes a partial set
meaningful and a resume cost exactly the remainder. A sink exception is allowed
to propagate: the sink is the durability guarantee, and swallowing its failure
would mean reporting success for a paid-for response that was never kept.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path

import httpx

from rentcomp.client.query import MAX_LIMIT
from rentcomp.storage.ledger import MONTHLY_CALL_CAP, load_ledger, save_ledger
from rentcomp.storage.secrets import load_api_key

__all__ = [
    "API_KEY_HEADER",
    "AuthError",
    "CallBudgetExceededError",
    "FIXTURES_DIR_ENV",
    "FixtureMissingError",
    "LISTINGS_PATH",
    "LIVE_MODE_ENV",
    "LIVE_MODE_VALUE",
    "ListingsPage",
    "ListingsResult",
    "LiveModeDisabledError",
    "MAX_LIMIT",
    "MissingApiKeyError",
    "Mode",
    "RENTCAST_BASE",
    "RateLimitError",
    "RentCastClient",
    "RentCastError",
    "UpstreamError",
    "fixture_signature",
    "resolve_mode",
]

#: Spec §3.1.
RENTCAST_BASE = "https://api.rentcast.io/v1"
#: Spec §3.1. The primary comp pull, and the pipeline's only source of records.
# (The /avm endpoint is a v2 benchmark and is out of the pipeline by design.)
LISTINGS_PATH = "/listings/rental/long-term"
#: Spec §3.1 and the committed schema's securitySchemes: apiKey, in header.
API_KEY_HEADER = "X-Api-Key"

#: D17's flag. Its name and its one enabling value are both fixed.
LIVE_MODE_ENV = "RENTCOMP_LIVE"
LIVE_MODE_VALUE = "1"

#: Points fixture mode at a different dataset (synthetic fixtures, a Playwright
#: globalSetup temp dir). Same shape as F0-S2's `RENTCOMP_FIXTURE_PULLS_DIR`.
FIXTURES_DIR_ENV = "RENTCOMP_FIXTURES_DIR"

#: The gate writes a sidecar next to the responses it saved; its `history`
#: carries the `X-Total-Count` each call reported. That is the only place the
#: truncation of a saved response is recorded, so fixture mode reads it to
#: report the same `total_count` the live call did.
FIXTURE_LEDGER_FILENAME = "ledger.json"

_TIMEOUT_SECONDS = 30


# ---------------------------------------------------------------------------
# errors — spec §7: "surface plainly; never partial-render stale-mixed data"
# ---------------------------------------------------------------------------


class RentCastError(Exception):
    """Anything this client can go wrong with.

    One base so a caller can say "the pull failed" without enumerating causes;
    distinct subclasses because 401 and 429 demand *opposite* user actions
    ("your key is wrong" vs "you are out of calls this month"), and a shared
    message would send the user off re-entering a perfectly good key.
    """

    #: HTTP status when there was one. The API layer chooses a response code
    #: from this rather than by string-matching a message.
    status_code: int | None = None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LiveModeDisabledError(RentCastError):
    """Live mode was asked for in code without `RENTCOMP_LIVE=1` in the
    environment. D17 has to hold against the next story's developer, not only
    against a stray env var."""


class MissingApiKeyError(RentCastError):
    """Live mode with no key. Raised *before* a transport exists — otherwise a
    keyless run would spend a call to discover it has no key."""


class AuthError(RentCastError):
    """HTTP 401. The key is wrong, expired, or not on a plan for this endpoint."""


class RateLimitError(RentCastError):
    """HTTP 429. Never retried automatically — a retry loop against a metered
    API spends the very budget it is reacting to."""


class UpstreamError(RentCastError):
    """Any other non-2xx, or a 2xx body that is not the JSON we were promised.

    Typed rather than degraded to an empty list on purpose: `[]` would render
    as F4's "0 comps in radius" empty state and send the user off widening a
    radius to fix a server outage.
    """


class FixtureMissingError(RentCastError):
    """Fixture mode has no saved response for these params.

    Emphatically NOT the same thing as an empty market: six of the gate's eight
    responses are a real, paid-for `[]`. "No fixture" and "no comps" mean
    opposite things to every downstream state.
    """


class CallBudgetExceededError(RentCastError):
    """The month's 50 calls are gone. Raised before sending, never after."""


# ---------------------------------------------------------------------------
# mode
# ---------------------------------------------------------------------------


class Mode(str, Enum):
    FIXTURE = "fixture"
    LIVE = "live"


def resolve_mode() -> Mode:
    """The mode the environment asks for, read **at call time**.

    Never snapshotted at import: tests, E2E `globalSetup` and the dev server
    all set the flag after this module has been imported, and an import-time
    snapshot would make the answer depend on import order — exactly the
    property you do not want guarding a spend.

    **Fails closed.** Only the literal `1` enables live mode. `0`, `""`, `no`,
    `off`, `false` and `2` are all fixture mode, because every one of them is
    something a person might type meaning "off" (or meaning nothing at all),
    and reading any of them as "on" costs real money. `true`/`yes` are rejected
    too — D17 spells the flag `RENTCOMP_LIVE=1`, and one spelling is easier to
    audit than a vocabulary.

    The *key* is not consulted here. Live-with-no-key must reach
    `MissingApiKeyError` (a loud, named refusal), not silently degrade to
    fixture mode and look like it worked.
    """
    return (
        Mode.LIVE
        if os.environ.get(LIVE_MODE_ENV, "").strip() == LIVE_MODE_VALUE
        else Mode.FIXTURE
    )


def fixture_signature(params: Mapping) -> str:
    """The filename stem a set of params maps to in `fixtures/live-samples/`.

    Byte-for-byte the scheme `scripts/gate.py` used when it wrote those files:
    `sha256(json.dumps(params, sort_keys=True))[:16]`. It is duplicated here
    rather than imported because `scripts/` is not an importable package — and
    it is pinned by a test that hashes the eight real param dicts out of the
    gate's own ledger, so it cannot drift silently.
    """
    canonical = json.dumps(dict(params), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def fixtures_dir() -> Path:
    """Where saved responses are read from. Resolved at call time."""
    override = os.environ.get(FIXTURES_DIR_ENV)
    if override:
        return Path(override).expanduser()
    # __file__ = <repo>/backend/src/rentcomp/client/rentcast.py -> parents[4] = <repo>
    return Path(__file__).resolve().parents[4] / "fixtures" / "live-samples"


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListingsPage:
    """One call's worth of response.

    `raw` is the body **exactly as it arrived** — bytes, unparsed. F3-S1 stores
    these bytes verbatim, and a client that round-tripped them through
    `json.dumps` would re-space and re-order nothing visible today and
    something invisible later.
    """

    records: tuple[dict, ...]
    total_count: int | None
    offset: int
    raw: bytes


@dataclass(frozen=True)
class ListingsResult:
    """A whole pull: every page, and an honest account of what is missing.

    `complete` is the authoritative signal, not `missing`: when the server
    never told us a total, a pull stopped by the call budget is incomplete by a
    quantity nobody can name, and reporting `missing=0` there would read as
    "nothing is missing".
    """

    records: tuple[dict, ...]
    total_count: int | None
    fetched: int
    complete: bool
    missing: int
    calls_spent: int
    pages: tuple[ListingsPage, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# the client
# ---------------------------------------------------------------------------

#: `sink(params, raw_bytes, meta)`.
Sink = Callable[[dict, bytes, dict], None]


class RentCastClient:
    """Fetches listings in whichever mode the environment allows.

    Constructing one is free and touches nothing: no environment read that
    matters, no transport, no file. Every decision that could cost money is
    made inside `fetch_listings`, at call time.

    Args:
        mode: force a mode. `None` (default) means "whatever the environment
            allows". `Mode.LIVE` is *requested*, not granted — without
            `RENTCOMP_LIVE=1` it raises `LiveModeDisabledError`, so writing
            `RentCastClient(mode=Mode.LIVE)` in a later story is not a way
            around D17. `Mode.FIXTURE` is always honoured.
        transport: an `httpx.BaseTransport` to send through. The only way to
            exercise the live path in a test without spending a call —
            `httpx.MockTransport` answers inside the process and never opens a
            socket.
        api_key: overrides the stored/env key. Whitespace-only counts as absent.
        fixtures_dir: overrides where fixture mode reads from.
        sink: `sink(params, raw, meta)`, called per call before parsing (D24).
    """

    def __init__(
        self,
        mode: Mode | str | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        api_key: str | None = None,
        fixtures_dir: Path | str | None = None,
        sink: Sink | None = None,
    ) -> None:
        self._requested_mode = Mode(mode) if mode is not None else None
        self._transport = transport
        self._api_key = (api_key or "").strip() or None
        self._fixtures_dir = Path(fixtures_dir) if fixtures_dir is not None else None
        self._sink = sink

    # -- public ------------------------------------------------------------

    def fetch_listings(self, params: Mapping, *, max_calls: int | None = None) -> ListingsResult:
        """Fetch every page of one `/listings/rental/long-term` query.

        `params` is the wire dict `client.query.build_listing_params` produces —
        it doubles as the fixture-lookup key, so it is passed through
        unmodified apart from `offset`, which pagination owns.

        `max_calls` caps this pull specifically (the monthly ledger caps the
        month). Hitting either cap mid-pull returns what was already paid for,
        marked incomplete, rather than raising: with 50 calls a month, throwing
        away a page you just bought is the one unrecoverable mistake.
        """
        params = dict(params)
        mode = self._effective_mode()
        if mode is Mode.FIXTURE:
            return self._fetch_from_fixtures(params)
        return self._fetch_live(params, max_calls=max_calls)

    # -- mode --------------------------------------------------------------

    def _effective_mode(self) -> Mode:
        env_mode = resolve_mode()
        if self._requested_mode is None:
            return env_mode
        if self._requested_mode is Mode.LIVE and env_mode is not Mode.LIVE:
            raise LiveModeDisabledError(
                f"live mode was requested in code but {LIVE_MODE_ENV}={LIVE_MODE_VALUE} is not "
                "set. Live RentCast calls spend against a 50/month cap and require BOTH the "
                "environment flag and a key (D17); the default is fixture mode."
            )
        return self._requested_mode

    # -- fixture mode ------------------------------------------------------

    def _fetch_from_fixtures(self, params: dict) -> ListingsResult:
        """Serve the saved response for these params. Never writes anything.

        One saved response is one page: fixture mode does not paginate, because
        a second page would be a *different* signature the gate never bought.
        A truncated fixture therefore reports itself truncated, which is the
        point — the committed Inactive pull really is 500 of 690, and every
        downstream story must develop against that fact rather than against a
        dataset that quietly claims to be whole.
        """
        root = self._fixtures_dir or fixtures_dir()
        sig = fixture_signature(params)
        path = root / f"{sig}.json"

        try:
            raw = path.read_bytes()
        except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
            raise FixtureMissingError(
                f"no saved response for signature {sig} (looked in {root}). These params have "
                "never been fetched, which is NOT the same as a market with no comps in it — "
                "either fix the params or save a fixture at "
                f"{path.name}."
            ) from exc
        except OSError as exc:
            raise FixtureMissingError(f"cannot read fixture {path}: {exc}") from exc

        records = _extract_records(_parse_json(raw, source=str(path)))
        total_count = self._fixture_total_count(root, sig)
        if total_count is None:
            # No sidecar entry (a synthetic or hand-made fixture): what is on
            # disk is all there is, so it is by definition the whole answer.
            total_count = len(records)

        page = ListingsPage(
            records=tuple(records),
            total_count=total_count,
            offset=int(params.get("offset") or 0),
            raw=raw,
        )
        fetched = len(records)
        return ListingsResult(
            records=tuple(records),
            total_count=total_count,
            fetched=fetched,
            complete=fetched >= total_count,
            missing=max(0, total_count - fetched),
            calls_spent=0,
            pages=(page,),
        )

    @staticmethod
    def _fixture_total_count(root: Path, sig: str) -> int | None:
        """The `X-Total-Count` the gate recorded for this signature, if any.

        Read from the sidecar rather than inferred from the file, because the
        two differ exactly when it matters: 500 records saved, 690 reported.
        """
        try:
            payload = json.loads((root / FIXTURE_LEDGER_FILENAME).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        for entry in payload.get("history") or ():
            if isinstance(entry, dict) and entry.get("sig") == sig:
                total = entry.get("total_count")
                return total if isinstance(total, int) and not isinstance(total, bool) else None
        return None

    # -- live mode ---------------------------------------------------------

    def _fetch_live(self, params: dict, *, max_calls: int | None) -> ListingsResult:
        api_key = self._api_key or load_api_key()
        if not api_key:
            # Before any transport exists: a keyless live run must not spend a
            # call to learn it has no key.
            raise MissingApiKeyError(
                f"{LIVE_MODE_ENV}={LIVE_MODE_VALUE} is set but no RentCast API key is "
                "configured. Set RENTCAST_API_KEY, or save one in Settings "
                "(<RENTCOMP_HOME>/secrets.json). Live mode requires both (D17)."
            )

        ledger = load_ledger()
        limit = _page_limit(params)

        pages: list[ListingsPage] = []
        records: list[dict] = []
        total_count: int | None = None
        offset = int(params.get("offset") or 0)
        calls_spent = 0
        stopped_early = False

        with self._open(api_key) as http:
            while True:
                if max_calls is not None and calls_spent >= max_calls:
                    stopped_early = True
                    break
                if ledger.remaining <= 0:
                    if not pages:
                        raise CallBudgetExceededError(
                            f"the monthly RentCast budget is spent "
                            f"({ledger.calls_this_month}/{MONTHLY_CALL_CAP} calls in "
                            f"{ledger.month}). Nothing was sent."
                        )
                    # Mid-pull: keep what was already paid for and name the gap.
                    stopped_early = True
                    break

                page_params = _page_params(params, offset)
                sig = fixture_signature(page_params)

                # The quota is spent the moment the request leaves, whatever
                # comes back — so it is counted (and persisted) first (§5a).
                ledger = ledger.with_call(sig=sig, params=page_params)
                save_ledger(ledger)
                calls_spent += 1

                try:
                    response = http.get(LISTINGS_PATH, params=page_params)
                except httpx.HTTPError as exc:
                    ledger = ledger.with_outcome(status=None, total_count=None)
                    save_ledger(ledger)
                    raise UpstreamError(
                        f"the RentCast request failed before a response arrived: "
                        f"{_redact(str(exc), api_key)}"
                    ) from exc

                page_total = _header_total_count(response)
                ledger = ledger.with_outcome(
                    status=response.status_code, total_count=page_total
                )
                save_ledger(ledger)

                if response.status_code >= 400:
                    raise _error_for(response, api_key)

                raw = response.content
                self._emit(
                    page_params,
                    raw,
                    {
                        "sig": sig,
                        "offset": offset,
                        "status": response.status_code,
                        "total_count": page_total,
                        "fetched_at": date.today().isoformat(),
                    },
                )

                page_records = _extract_records(
                    _parse_json(raw, source=f"{RENTCAST_BASE}{LISTINGS_PATH} offset={offset}")
                )
                pages.append(
                    ListingsPage(
                        records=tuple(page_records),
                        total_count=page_total,
                        offset=offset,
                        raw=raw,
                    )
                )
                records.extend(page_records)
                if page_total is not None:
                    total_count = page_total

                if not page_records:
                    # Defensive: a server that keeps answering "no records"
                    # while claiming a larger total would otherwise bill one
                    # call per loop iteration forever.
                    break
                if total_count is None:
                    # No X-Total-Count: a page shorter than `limit` is the end
                    # of the set (spec §3.2).
                    if len(page_records) < limit:
                        break
                elif len(records) >= total_count:
                    break
                offset += len(page_records)

        fetched = len(records)
        return ListingsResult(
            records=tuple(records),
            total_count=total_count,
            fetched=fetched,
            complete=not stopped_early
            and (total_count is None or fetched >= total_count),
            missing=max(0, total_count - fetched) if total_count is not None else 0,
            calls_spent=calls_spent,
            pages=tuple(pages),
        )

    def _open(self, api_key: str) -> httpx.Client:
        """Build the transport. Only ever reached on the live path.

        The key goes in a header and nowhere else — a key in the query string
        lands in every proxy log between here and RentCast, and in RentCast's
        own access logs.
        """
        return httpx.Client(
            base_url=RENTCAST_BASE,
            headers={API_KEY_HEADER: api_key},
            transport=self._transport,
            timeout=_TIMEOUT_SECONDS,
        )

    def _emit(self, params: dict, raw: bytes, meta: dict) -> None:
        """D24 write-through: hand the bytes to the sink BEFORE parsing them.

        Only on success. An error body is not a response — writing a 401's JSON
        into `cache/<key>/raw/` would make F3-S4's resume logic believe that
        query was satisfied. Failures are recorded in the ledger's history
        instead, which is where §5a puts "which failed and why".
        """
        if self._sink is not None:
            self._sink(params, raw, meta)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _page_params(params: dict, offset: int) -> dict:
    """The params for one page. `offset` is absent on page 0 so it hashes to the
    same signature as the gate's un-offset call."""
    page = {key: value for key, value in params.items() if key != "offset"}
    if offset:
        page["offset"] = offset
    return page


def _page_limit(params: dict) -> int:
    """The page size the server will actually honour (the schema caps it at 500)."""
    try:
        limit = int(params.get("limit") or MAX_LIMIT)
    except (TypeError, ValueError):
        limit = MAX_LIMIT
    return max(1, min(limit, MAX_LIMIT))


def _header_total_count(response: httpx.Response) -> int | None:
    raw = response.headers.get("X-Total-Count")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _parse_json(raw: bytes, *, source: str):
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpstreamError(
            f"RentCast returned a body that is not JSON ({source}): {exc}"
        ) from exc


def _extract_records(data) -> list[dict]:
    """Records out of a response body.

    The endpoint answers with a bare JSON array; the `{"listings": [...]}`
    shape is accepted too because that is what `scripts/gate.py` already
    tolerates and the two must not disagree about what a response is.
    """
    if isinstance(data, list):
        records = data
    elif isinstance(data, dict):
        records = data.get("listings", [])
    else:
        raise UpstreamError(
            f"RentCast returned {type(data).__name__}, expected a list of listings"
        )
    if not isinstance(records, list):
        raise UpstreamError("RentCast returned a 'listings' value that is not a list")
    return [record for record in records if isinstance(record, dict)]


def _error_for(response: httpx.Response, api_key: str) -> RentCastError:
    """Map a non-2xx onto the taxonomy. 401 and 429 are never each other."""
    status = response.status_code
    detail = _redact(_short_body(response), api_key)
    if status == 401:
        return AuthError(
            "RentCast rejected the API key (HTTP 401). Check the key in Settings or "
            f"RENTCAST_API_KEY — it may be wrong, expired, or not entitled to this "
            f"endpoint. {detail}".strip(),
            status_code=status,
        )
    if status == 429:
        return RateLimitError(
            "RentCast rate-limited this request (HTTP 429). Nothing was retried — a retry "
            "spends the same budget it is reacting to. Wait, then try again; the call is "
            f"already counted against the month. {detail}".strip(),
            status_code=status,
        )
    return UpstreamError(
        f"RentCast returned HTTP {status}. {detail}".strip(), status_code=status
    )


def _short_body(response: httpx.Response, limit: int = 200) -> str:
    try:
        text = response.text
    except Exception:  # pragma: no cover - a body that will not even decode
        return ""
    text = " ".join(text.split())
    if not text:
        return ""
    return f"Response: {text[:limit]}" + ("..." if len(text) > limit else "")


def _redact(text: str, api_key: str | None) -> str:
    """Never let a credential reach a message, a log, or a PM report.

    Belt and braces: RentCast has no reason to echo the key back, but an error
    string is the one place a secret escapes without anyone deciding to print
    it.
    """
    if api_key and api_key in text:
        return text.replace(api_key, "<redacted>")
    return text
