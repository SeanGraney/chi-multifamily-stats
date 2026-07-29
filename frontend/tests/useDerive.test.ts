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
 * Timing and cancellation only. Not one assertion about a *value*: what the
 * derived numbers are is Layer 2's job (`backend/tests/api/`), what the screen
 * shows is Playwright's. D23 exists because component tests would mostly
 * assert "given prop, render text" — that is maintenance without insight, and
 * the moment this file starts checking payload contents it has become one.
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
 * These tests read only `.derived` and `.error` off the hook's return value.
 * The `status` enum's spelling is deliberately NOT pinned — it is a
 * presentation detail the developer may name differently, and pinning it
 * would be a test defect the day someone makes a legitimate choice.
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

/** Only the two fields these tests read. See the contract note above. */
interface DeriveHookValue {
  derived: unknown;
  error?: unknown;
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
  /** Resolve this call's `fetch()` promise with `payload`. */
  respondWith(payload: unknown): Promise<void>;
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
        call.settled = true;
        resolveOuter({
          ok: true,
          status: 200,
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
});
