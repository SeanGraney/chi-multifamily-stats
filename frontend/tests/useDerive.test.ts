// @vitest-environment jsdom
/**
 * `useDerive` — THE Vitest file (D23, CLAUDE.md: "Plus one Vitest file for
 * useDerive (debounce/abort/latest-wins timing)").
 *
 * QA-authored, written RED before the hook exists (AGENT_QA.md protocol
 * step 3). It carries F5-S2's reassigned scope from QUEUE.md row 14:
 *
 *   V1  ~150ms debounce
 *   V2  one AbortController per request (and an abort is not an error)
 *   V3  latest-wins during slider drags
 *   V4  out-of-order responses cannot land   <- F0-S2's AC4, reassigned here
 *
 * QUEUE.md row 14b then added the ERROR PATH (V5-V8). That path shipped with
 * F5-S2 and was covered by nothing: three separate mutations of `useDerive`'s
 * catch block left V1-V4 all green. It stopped being cosmetic the moment
 * `Results.tsx` grew a `derive-status` line, because the two failure modes are
 * both honesty failures rather than UI bugs:
 *
 *   V5  a SUPERSEDED request that fails must not raise the failure banner
 *       (kills: delete `|| seq !== latestSeq.current` from the catch). A late
 *       500 from an edit the user has already moved past would otherwise paint
 *       "the numbers below are from before your last edit" over numbers that
 *       are correct and current. A false honesty warning is its own honesty
 *       problem — it teaches the user to distrust a banner that is usually
 *       lying, which is exactly how a true one gets ignored.
 *   V6  a non-ok response must never become `derived`
 *       (kills: delete the `!response.ok` throw). A 4xx/5xx JSON body cast to
 *       `DerivedState` renders as derived state — numbers with no evidence
 *       behind them. That is a NORTH_STAR violation, not a UI bug.
 *   V7  an abort that lands while its request is STILL THE LATEST must not
 *       raise the banner (kills: delete `isAbort(err) ||`). See the note on
 *       that test: V2 does not prove `isAbort` necessary, and row 14b's
 *       standing correction is that the two guards in the catch were
 *       individually redundant and only jointly load-bearing.
 *   V8  a successful derive clears a previous failure banner.
 *
 * ---------------------------------------------------------------------------
 * WHY THIS FILE MATTERS BEYOND THIS STORY
 * ---------------------------------------------------------------------------
 * `cd frontend && npx vitest run` currently exits 1 ("No test files found"),
 * so the middle leg of the documented gate — `pytest && npx vitest run &&
 * npx playwright test` — is a HARD FAILURE today, not a benign no-op. This
 * file is what turns that leg green for the first time.
 *
 * ---------------------------------------------------------------------------
 * SCOPE DISCIPLINE — do not let this file grow
 * ---------------------------------------------------------------------------
 * Timing, cancellation, and failure CLASSIFICATION only. Not one assertion
 * about a *value*: what the derived numbers are is Layer 2's job
 * (`backend/tests/api/`), what the screen shows is Playwright's. D23 exists
 * because component tests would mostly assert "given prop, render text" — that
 * is maintenance without insight, and the moment this file starts checking
 * payload contents it has become one.
 *
 * V5-V8 stay inside that line. None of them asserts what a payload contains;
 * they assert *whether a payload is allowed to become derived state at all*,
 * and *which of four concurrent outcomes* (landed / superseded / aborted /
 * failed) a given response is classified as. That is the same timing question
 * V1-V4 ask, asked about the unhappy branch — and it is unreachable from
 * Playwright without deliberately breaking a server mid-drag.
 *
 * ---------------------------------------------------------------------------
 * THE CONTRACT THESE TESTS ARE WRITTEN AGAINST (QA-proposed, PM to confirm)
 * ---------------------------------------------------------------------------
 * `frontend/src/api/useDerive.ts` default-free named export:
 *
 *     export function useDerive(request: DeriveRequest | null): {
 *       derived: DerivedState | null;
 *       status: "idle" | "loading" | "ready" | "error";
 *       error: string | null;
 *     }
 *
 * Behaviour, from ADR-001 §4 and D13:
 *   - a change to `request` schedules `POST /api/derive` after ~150ms of quiet;
 *     edits arriving inside that window coalesce into one request;
 *   - each request gets its own `AbortController`, and the in-flight one is
 *     aborted when a newer request starts (and on unmount);
 *   - a monotonic request sequence guards `setState`, so a response that
 *     resolves *after* its abort still cannot land ("abort alone is racy
 *     against an already-resolved fetch" — ADR-001 §4);
 *   - aborts are never surfaced as errors.
 *
 * V1-V4 read only `.derived` and `.error` off the hook's return value. The
 * `status` enum's spelling was deliberately NOT pinned — it was a presentation
 * detail the developer might name differently, and pinning it would have been
 * a test defect the day someone made a legitimate choice.
 *
 * V5-V8 (row 14b) revisit that on purpose, in ONE direction only: they assert
 * `status !== "error"`, never `status === <some spelling>`. The reason the
 * original stance expired is that the developer has since named it and
 * `Results.tsx` consumes it — `status === "error"` is the literal condition
 * that renders the rust "Re-derive failed" banner, so "must not raise the
 * banner" is now most directly expressed as "status is not the error status".
 * A negative assertion still costs nothing if the enum is renamed: a hook that
 * renames "error" to "failed" passes these tests, and the Playwright layer
 * catches the banner that then never renders. The one thing that would be a
 * defect — pinning the happy-path spelling ("ready" vs "success") — is not
 * done anywhere below.
 *
 * The debounce is asserted as a BAND, not as the literal 150: the story says
 * "~150ms". Nothing may fire at 100ms; exactly one thing must have fired by
 * 200ms after the last edit. Any d in (100, 200] passes, which is what "~"
 * means. A test that pinned exactly 150 would fail a legitimate tweak.
 *
 * ---------------------------------------------------------------------------
 * DEPENDENCIES THIS FILE NEEDS (flagged to the PM — QA does not install)
 * ---------------------------------------------------------------------------
 * `vitest` + `jsdom` as frontend devDependencies, and a `"test": "vitest run"`
 * script. Deliberately NOT `@testing-library/react`: D23 says "no React
 * Testing Library suite", and React 18.3 exports `act` itself, so the ~25-line
 * `renderHook` below needs nothing that is not already a dependency.
 *
 * File location: `frontend/tests/` rather than beside the hook in
 * `frontend/src/api/`, because `frontend/tsconfig.json` includes `src` and
 * `npm run build` runs `tsc --noEmit` — a test importing a not-yet-written
 * module from inside `src` would fail the build, and two Playwright specs
 * build the frontend in `beforeAll`, so this red file would have silently
 * SKIPPED ten unrelated live-server specs. Moving it back into `src` later is
 * fine as long as tsconfig excludes tests.
 */
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { components } from "../src/api/schema";

type DeriveRequest = components["schemas"]["DeriveRequest"];

/** Only the fields these tests read. See the contract note above. */
interface DeriveHookValue {
  derived: unknown;
  error?: unknown;
  /** Read by V5-V8 only, and only ever compared with `!== "error"`. */
  status?: unknown;
}
type UseDeriveHook = (request: DeriveRequest | null) => DeriveHookValue;

// ---------------------------------------------------------------------------
// Lazy import — a missing module must be ONE legible failure, never a
// collection error that hides the state of every test beside it
// (AGENT_QA.md: "What must never be in a commit: a collection error").
//
// The specifier is built at RUNTIME and the file is probed with `existsSync`
// first, on purpose. A literal `await import("../src/api/useDerive")` is
// resolved by Vite at TRANSFORM time, so while the hook does not exist the
// whole file fails to load with "Failed to resolve import" and vitest reports
// `no tests` — the unreadable red this structure exists to prevent. Verified
// both ways before commit.
// ---------------------------------------------------------------------------

const HOOK_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src/api");
const HOOK_CANDIDATES = ["useDerive.ts", "useDerive.tsx"];

let useDerive: UseDeriveHook | null = null;
let importError: unknown = null;

beforeAll(async () => {
  const found = HOOK_CANDIDATES.map((name) => path.join(HOOK_DIR, name)).find(existsSync);
  if (!found) {
    importError = new Error(
      `no ${HOOK_CANDIDATES.join(" / ")} in ${HOOK_DIR} — the hook has not been written yet`
    );
    return;
  }
  try {
    const mod = (await import(/* @vite-ignore */ pathToFileURL(found).href)) as {
      useDerive?: unknown;
    };
    useDerive = typeof mod.useDerive === "function" ? (mod.useDerive as UseDeriveHook) : null;
    if (!useDerive) importError = new Error(`${found} has no named export \`useDerive\``);
  } catch (err) {
    importError = err;
  }
});

function requireHook(): UseDeriveHook {
  if (!useDerive) {
    throw new Error(
      "frontend/src/api/useDerive.ts does not export `useDerive` yet " +
        `(F5-S2 red): ${String(importError)}`
    );
  }
  return useDerive;
}

// ---------------------------------------------------------------------------
// A 25-line renderHook. React 18.3 exports `act` directly, so this needs no
// testing library (see the dependency note above).
// ---------------------------------------------------------------------------

interface HookHandle {
  readonly current: DeriveHookValue;
  rerender(request: DeriveRequest | null): void;
  unmount(): void;
}

function renderUseDerive(initial: DeriveRequest | null): HookHandle {
  const hook = requireHook();
  const box: { value: DeriveHookValue } = { value: { derived: null } };

  function Probe({ request }: { request: DeriveRequest | null }) {
    box.value = hook(request);
    return null;
  }

  const container = document.createElement("div");
  document.body.appendChild(container);
  let root: Root | null = null;

  act(() => {
    root = createRoot(container);
    root.render(createElement(Probe, { request: initial }));
  });

  return {
    get current() {
      return box.value;
    },
    rerender(request) {
      act(() => {
        root?.render(createElement(Probe, { request }));
      });
    },
    unmount() {
      act(() => {
        root?.unmount();
      });
      container.remove();
    },
  };
}

// ---------------------------------------------------------------------------
// A controllable fetch: every call is captured, and its promise is resolved by
// the test, in whatever order the test likes. That ordering freedom is the
// entire point of V3/V4.
// ---------------------------------------------------------------------------

interface CapturedCall {
  url: string;
  method: string;
  body: Record<string, unknown>;
  signal: AbortSignal | undefined;
  /** Resolve this call's `fetch()` promise with a 200 carrying `payload`. */
  respondWith(payload: unknown): Promise<void>;
  /**
   * Resolve it with an arbitrary status. `fetch` does NOT reject on 4xx/5xx —
   * it resolves with `ok: false` — which is precisely why `!response.ok` has
   * to be checked by hand and why forgetting to check it is invisible (V6).
   */
  respondWithStatus(status: number, payload: unknown): Promise<void>;
  /** Reject it the way a real aborted fetch rejects. */
  rejectAsAbort(): Promise<void>;
  settled: boolean;
}

let calls: CapturedCall[] = [];

function installFetch(): void {
  calls = [];
  const fetchMock = vi.fn((input: unknown, init?: RequestInit) => {
    let resolveOuter!: (value: unknown) => void;
    let rejectOuter!: (reason: unknown) => void;
    const promise = new Promise<unknown>((resolve, reject) => {
      resolveOuter = resolve;
      rejectOuter = reject;
    });

    const call: CapturedCall = {
      url: String(input),
      method: (init?.method ?? "GET").toUpperCase(),
      body: typeof init?.body === "string" ? JSON.parse(init.body) : {},
      signal: init?.signal ?? undefined,
      settled: false,
      async respondWith(payload) {
        await call.respondWithStatus(200, payload);
      },
      async respondWithStatus(status, payload) {
        call.settled = true;
        resolveOuter({
          ok: status >= 200 && status < 300,
          status,
          json: async () => payload,
          text: async () => JSON.stringify(payload),
        });
        await flushMicrotasks();
      },
      async rejectAsAbort() {
        call.settled = true;
        const err = new DOMException("The operation was aborted.", "AbortError");
        rejectOuter(err);
        await flushMicrotasks();
      },
    };
    calls.push(call);
    return promise;
  });
  vi.stubGlobal("fetch", fetchMock);
}

async function flushMicrotasks(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Advance fake time inside `act`, flushing effects and microtasks with it. */
async function advance(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

// ---------------------------------------------------------------------------
// Request bodies. Only `drift_pct` varies — it is the slider the story's
// "latest-wins during slider drags" clause is about, and using one varying
// field keeps "which edit is this?" readable in a failure message.
// ---------------------------------------------------------------------------

function requestAt(driftPct: number): DeriveRequest {
  return {
    pull_ref: "synthetic-basic",
    subject: {
      address: "1234 W Fake St Unit 2",
      lat: 41.9,
      lng: -87.68,
      sqft: 1000,
      beds: 2,
      baths: 1,
    },
    weights: {},
    include_overrides: [],
    filters: { max_distance_mi: null, hide_censored: false, leased_only: false },
    drift_pct: driftPct,
    candidate_rent: null,
  } as DeriveRequest;
}

/** A stand-in DerivedState. Nothing here inspects its contents — by design. */
function payloadFor(driftPct: number): unknown {
  return { __marker: `derived@drift=${driftPct}` };
}

function marker(value: unknown): string | undefined {
  return (value as { __marker?: string } | null)?.__marker;
}

/**
 * "The failure banner is NOT up." Both halves are asserted because
 * `Results.tsx` renders the rust banner off `status === "error"` and renders
 * the reason off `error` — a hook that set one without the other would leave
 * either a banner with no reason or a reason with no banner, and only one of
 * those is visible to a test that checks a single field.
 *
 * See the contract note: `status` is compared with `!==` only, never pinned to
 * a spelling.
 */
function expectNoFailureBanner(handle: HookHandle, why: string): void {
  expect(handle.current.error ?? null, why).toBeNull();
  expect(handle.current.status, why).not.toBe("error");
}

/**
 * Drive one edit all the way to a fired request. Long enough to clear any
 * debounce in the accepted band, so tests about *ordering* are not also
 * silently testing the debounce.
 */
async function editAndFlush(handle: HookHandle, driftPct: number): Promise<CapturedCall> {
  const before = calls.length;
  handle.rerender(requestAt(driftPct));
  await advance(1000);
  expect(
    calls.length,
    `editing drift_pct to ${driftPct} did not produce exactly one new request ` +
      `(had ${before}, now ${calls.length})`
  ).toBe(before + 1);
  return calls[calls.length - 1];
}

beforeEach(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  installFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

// ---------------------------------------------------------------------------

describe("useDerive", () => {
  it("V0: the hook module exists and exports useDerive", () => {
    expect(
      importError,
      "frontend/src/api/useDerive.ts must export a `useDerive` function (D13). " +
        "Every test below is gated on this one, so this is the single failure to read " +
        "first while the story is red."
    ).toBeNull();
    expect(typeof useDerive).toBe("function");
  });

  it("V1: rapid edits coalesce into one POST /api/derive after ~150ms of quiet", async () => {
    const handle = renderUseDerive(requestAt(1));

    // A drag: five edits, 20ms apart — well inside any debounce in the band.
    for (const drift of [2, 3, 4, 5, 6]) {
      handle.rerender(requestAt(drift));
      await advance(20);
    }
    expect(
      calls.length,
      "a request fired while edits were still arriving every 20ms — the debounce is " +
        "either absent or far shorter than ~150ms, and a slider drag would spray one " +
        "request per pixel"
    ).toBe(0);

    // 100ms of quiet after the last edit: still nothing.
    await advance(100);
    expect(
      calls.length,
      "a request fired only 100ms after the last edit — the debounce window is too short " +
        "(the story says ~150ms; this test accepts anything in (100ms, 200ms])"
    ).toBe(0);

    // By 200ms after the last edit: exactly one, carrying the LAST edit.
    await advance(100);
    expect(
      calls.length,
      "no request had fired 200ms after the last edit — the debounce never elapsed, or " +
        "the window is longer than ~150ms by enough to be felt as lag"
    ).toBe(1);

    expect(calls[0].url).toContain("/api/derive");
    expect(calls[0].method).toBe("POST");
    expect(
      calls[0].body.drift_pct,
      "the coalesced request carried a stale drift_pct — coalescing must keep the LAST " +
        "edit, not the first one in the window"
    ).toBe(6);

    handle.unmount();
  });

  it("V2: every request gets its own AbortController, and the superseded one is aborted", async () => {
    const handle = renderUseDerive(requestAt(1));
    const first = await editAndFlush(handle, 2);

    expect(
      first.signal,
      "fetch was called without an AbortSignal — with no controller there is nothing to " +
        "cancel, and a slow response from an abandoned edit will land on the screen (D13)"
    ).toBeDefined();
    expect(first.signal!.aborted, "the first request was aborted before it even ran").toBe(false);

    const second = await editAndFlush(handle, 3);

    expect(
      second.signal,
      "the second request carried no AbortSignal"
    ).toBeDefined();
    expect(
      second.signal,
      "both requests share one AbortController — aborting the stale one would then abort " +
        "the live one too. D13 says ONE controller PER REQUEST"
    ).not.toBe(first.signal);
    expect(
      first.signal!.aborted,
      "the superseded request was never aborted — its response is still coming, and the " +
        "browser is still holding the connection open for an answer nobody wants"
    ).toBe(true);
    expect(
      second.signal!.aborted,
      "the newest request was aborted — starting a request must abort the PREVIOUS one, " +
        "not itself"
    ).toBe(false);

    // An abort is a cancellation, not a failure (ADR-001 §4): the real fetch
    // rejects with an AbortError, and the user must never see that as an error.
    await first.rejectAsAbort();
    expect(
      handle.current.error ?? null,
      "the aborted request's rejection surfaced as an error state — the user cancelled " +
        "nothing; they just moved a slider"
    ).toBeNull();

    // And unmounting must abort what is still in flight.
    handle.unmount();
    expect(
      second.signal!.aborted,
      "unmounting left a request in flight — its response will try to setState on an " +
        "unmounted component"
    ).toBe(true);
  });

  it("V3: every edit in a drag re-derives, and the state tracks the latest one", async () => {
    const handle = renderUseDerive(requestAt(1));

    // A drag slow enough that each response keeps up — the ordinary case, and
    // deliberately NOT V4's race. What this catches is the opposite failure:
    // a hook that derives once and then stops (an effect with the wrong deps,
    // a memo on the request, a "already loaded" guard), which leaves a stale
    // panel on screen while the user keeps dragging. F5's success criterion is
    // "no hidden state"; a panel that stopped updating is the purest form of it.
    for (const drift of [2, 3, 4]) {
      const call = await editAndFlush(handle, drift);
      expect(
        call.body.drift_pct,
        `the request fired for edit drift_pct=${drift} carried ` +
          `${String(call.body.drift_pct)} instead — the body is not built from the ` +
          "current curation state"
      ).toBe(drift);

      await call.respondWith(payloadFor(drift));
      expect(
        marker(handle.current.derived),
        `after editing drift_pct to ${drift} and its response arriving, the hook still ` +
          `exposes ${String(marker(handle.current.derived))}. The panel is stale: the ` +
          "user moved the slider and the numbers did not follow (D13)"
      ).toBe(`derived@drift=${drift}`);
    }

    expect(
      calls.length,
      "three edits produced a number of requests other than three — either an edit was " +
        "dropped or one edit fired more than once"
    ).toBe(3);

    handle.unmount();
  });

  it("V4: a response that resolves after a newer one cannot land (F0-S2 AC4)", async () => {
    const handle = renderUseDerive(requestAt(1));

    const stale = await editAndFlush(handle, 2);
    const latest = await editAndFlush(handle, 3);

    // The newest answer arrives first and lands.
    await latest.respondWith(payloadFor(3));
    expect(
      marker(handle.current.derived),
      "the newest response did not land at all"
    ).toBe("derived@drift=3");

    // Now the stale one resolves — late, and already aborted. A real fetch can
    // do exactly this: abort() does not un-resolve a response that was already
    // in flight when the abort was issued, which is why ADR-001 §4 asks for a
    // monotonic sequence check IN ADDITION to the AbortController.
    expect(
      stale.signal?.aborted,
      "the stale request was not even aborted — see V2"
    ).toBe(true);
    await stale.respondWith(payloadFor(2));

    expect(
      marker(handle.current.derived),
      "an out-of-order response OVERWROTE a newer one. The screen now shows derived " +
        "state for an edit the user has already moved past, with no way to tell — this " +
        "is F0-S2 AC4, and abort alone does not prevent it: a monotonic request sequence " +
        "must be checked before setState"
    ).toBe("derived@drift=3");

    expect(
      handle.current.error ?? null,
      "the late stale response produced an error state"
    ).toBeNull();

    handle.unmount();
  });

  // -------------------------------------------------------------------------
  // V5-V8 — the error path (QUEUE.md row 14b)
  // -------------------------------------------------------------------------

  it("V5: a SUPERSEDED request that fails does not raise the failure banner", async () => {
    // ---------------------------------------------------------------------
    // The failure V1-V4 could not see. A drag issues request after request;
    // one of the abandoned ones comes back 500 *after* the user has moved on.
    // The response is worthless either way — but the question is what the
    // screen says about the numbers that are ALREADY on it, and those numbers
    // are correct and current. Raising "Re-derive failed — the numbers below
    // are from before your last edit" over them is a lie in the honest
    // direction, which is the kind that erodes the banner's meaning fastest:
    // a warning that cries wolf during every drag is a warning nobody reads
    // on the one drag where it is true.
    //
    // This is the test that makes `|| seq !== latestSeq.current` in the catch
    // load-bearing. Deleting that clause leaves V1-V4 all green.
    // ---------------------------------------------------------------------
    const handle = renderUseDerive(requestAt(1));

    const settled = await editAndFlush(handle, 2);
    await settled.respondWith(payloadFor(2));
    expect(marker(handle.current.derived), "the baseline response never landed").toBe(
      "derived@drift=2"
    );

    // Two more edits. The middle one is now superseded and abandoned.
    const superseded = await editAndFlush(handle, 3);
    const newest = await editAndFlush(handle, 4);
    expect(
      superseded.signal?.aborted,
      "the superseded request was not aborted — see V2"
    ).toBe(true);

    // ...and it 500s, late. A real `fetch` resolves (it does not reject) on a
    // 5xx, so this arrives through the success path and is thrown by the
    // `!response.ok` check — landing in the same catch an abort lands in.
    await superseded.respondWithStatus(500, { detail: "derive blew up" });

    expectNoFailureBanner(
      handle,
      "a request the user had already moved past failed, and the hook raised the failure " +
        "banner over derived state that is correct and current. The banner claims 'the " +
        "numbers below are from before your last edit' — here they are not, so the warning " +
        "is false. The catch must ignore a failure whose request is no longer the latest, " +
        "exactly as the success path already ignores a superseded response (V4)"
    );
    expect(
      marker(handle.current.derived),
      "the superseded failure also disturbed the derived state it had no claim on"
    ).toBe("derived@drift=2");

    // And the hook is not wedged: the request that IS current still lands.
    await newest.respondWith(payloadFor(4));
    expect(
      marker(handle.current.derived),
      "after swallowing a superseded failure the hook stopped accepting the newest response"
    ).toBe("derived@drift=4");
    expectNoFailureBanner(handle, "a successful derive left an error set");

    handle.unmount();
  });

  it("V6: a non-ok response never becomes derived state", async () => {
    // ---------------------------------------------------------------------
    // NORTH_STAR, not UI polish. `fetch` resolves on 4xx/5xx — `ok` is false
    // but the promise is fulfilled — so `await response.json()` on an error
    // body succeeds and `as DerivedState` silently accepts it. FastAPI's
    // error bodies are JSON, so this is not a hypothetical shape: a 422 body
    // would be cast, stored, and rendered.
    //
    // The payload below therefore carries the SAME marker shape a real
    // response carries. If the `!response.ok` throw is deleted, the error body
    // lands as derived state and this test fails on the marker — which is the
    // whole point: a test whose error body were obviously non-DerivedState
    // (a bare string, an empty object) would pass under the mutant, because
    // nothing downstream validates the shape. Every number on screen must
    // trace back to real comps; an error envelope traces back to nothing.
    // ---------------------------------------------------------------------
    const handle = renderUseDerive(requestAt(1));

    const good = await editAndFlush(handle, 2);
    await good.respondWith(payloadFor(2));
    expect(marker(handle.current.derived), "the baseline response never landed").toBe(
      "derived@drift=2"
    );

    const failing = await editAndFlush(handle, 3);
    await failing.respondWithStatus(500, {
      ...(payloadFor(3) as object),
      detail: "internal server error",
    });

    expect(
      marker(handle.current.derived),
      "a 500 response body was accepted as derived state and is now what the panels render. " +
        "`fetch` does not reject on a 5xx, so `response.ok` must be checked by hand before " +
        "`response.json()` is cast to DerivedState. Every number this tool shows must trace " +
        "back to observed comps (NORTH_STAR); an error envelope traces back to nothing, and " +
        "it will not look wrong on screen — it will look like numbers"
    ).toBe("derived@drift=2");

    // The failure IS the current request, so it must be surfaced — the other
    // half of the same guard. Silently keeping the old numbers with no banner
    // would be the opposite dishonesty: a screen that looks settled and is not.
    expect(
      handle.current.error ?? null,
      "the current request returned 500 and the hook reported no error at all — the panels " +
        "are stale and nothing on screen says so"
    ).not.toBeNull();
    expect(
      String(handle.current.error),
      "the error message does not mention the HTTP status, so the user (and the next " +
        "debugger) cannot tell a server fault from a network one"
    ).toContain("500");
    expect(handle.current.status, "a failed current derive did not reach the error status").toBe(
      "error"
    );

    handle.unmount();
  });

  it("V7: an abort that lands while its request is still the latest is not an error", async () => {
    // ---------------------------------------------------------------------
    // THE INDIVIDUAL-NECESSITY TEST for `isAbort`, and row 14b's standing
    // correction: V2 does NOT prove `isAbort` is needed. V2 aborts request A,
    // then lets B fire (it advances 1000ms), and only then rejects A — by
    // which point `latestSeq` has already moved, so the seq check alone
    // suppresses it. Delete `isAbort(err) ||` and V1-V4 stay green. Two
    // guards, each individually redundant against the old suite, only jointly
    // load-bearing: the signature of a coverage gap, not of good coverage.
    //
    // The window where ONLY `isAbort` can save you is real and is ~150ms wide,
    // which is to say it is the entire duration of every slider drag: the
    // effect cleanup aborts the in-flight request IMMEDIATELY on an edit, but
    // the replacement request does not exist yet — it is behind the debounce,
    // and `seq` is not incremented until the timer fires. So between the edit
    // and the debounce elapsing, the aborted request is still the latest one,
    // and its AbortError arrives with `seq === latestSeq`.
    // ---------------------------------------------------------------------
    const handle = renderUseDerive(requestAt(1));

    const inFlight = await editAndFlush(handle, 2);
    await inFlight.respondWith(payloadFor(2));
    expect(marker(handle.current.derived), "the baseline response never landed").toBe(
      "derived@drift=2"
    );

    const stillInFlight = await editAndFlush(handle, 3);

    // Edit again, but stop INSIDE the debounce window.
    handle.rerender(requestAt(4));
    await advance(50);

    expect(
      stillInFlight.signal?.aborted,
      "editing did not abort the in-flight request — see V2"
    ).toBe(true);
    expect(
      calls.length,
      "a replacement request fired within 50ms of the edit, so this test is no longer " +
        "exercising the window it exists for (the debounce collapsed — see V1). With a " +
        "newer request already issued, the sequence guard would mask the abort and " +
        "`isAbort` would go untested again"
    ).toBe(2);

    // The abort rejects now — while its own request is still the newest one
    // ever issued. Nothing but `isAbort` stands between this and the banner.
    await stillInFlight.rejectAsAbort();

    expectNoFailureBanner(
      handle,
      "an AbortError was surfaced as a failure. The user cancelled nothing — they moved a " +
        "slider, and the hook itself issued the abort. This is the case the sequence guard " +
        "CANNOT catch, because the replacement request is still behind the debounce and " +
        "`latestSeq` has not moved yet: an abort must be classified as a cancellation on " +
        "its own evidence (`err.name === 'AbortError'`), not incidentally"
    );
    expect(
      marker(handle.current.derived),
      "the abort discarded derived state that was still the best answer available"
    ).toBe("derived@drift=2");

    handle.unmount();
  });

  it("V8: a successful derive clears a previous failure banner", async () => {
    // A banner that outlives its cause is the same lie as V5's, told later:
    // the numbers below are now current, and the screen still says they are
    // not. The user's next move after a failed derive is to nudge something
    // and try again, so this is the ordinary path out of an error, not an
    // edge case.
    const handle = renderUseDerive(requestAt(1));

    const failing = await editAndFlush(handle, 2);
    await failing.respondWithStatus(503, { detail: "service unavailable" });
    expect(
      handle.current.error ?? null,
      "the 503 on the current request was not reported — see V6"
    ).not.toBeNull();

    const recovered = await editAndFlush(handle, 3);
    await recovered.respondWith(payloadFor(3));

    expect(
      marker(handle.current.derived),
      "the retry succeeded but its payload never landed"
    ).toBe("derived@drift=3");
    expectNoFailureBanner(
      handle,
      "the failure banner is still up over numbers that just came back successfully. " +
        "'Re-derive failed — the numbers below are from before your last edit' is now false, " +
        "and it is stuck: a successful derive must clear the error it replaces"
    );

    handle.unmount();
  });
});
