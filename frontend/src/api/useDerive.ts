import { useEffect, useRef, useState } from "react";
import type { components } from "./schema";

type DeriveRequest = components["schemas"]["DeriveRequest"];
type DerivedState = components["schemas"]["DerivedState"];

/**
 * The one derivation channel (D13, ADR-001 §4).
 *
 * Curation state is client-owned React state; *derived* state is whatever
 * `/api/derive` last returned. This hook is the whole boundary between the
 * two, and it computes nothing — it posts a request and hands back a payload
 * (D5: the frontend never computes a statistic).
 *
 * Three behaviours, all of them about timing rather than about values, which
 * is exactly why they are the project's one Vitest file (D23):
 *
 * 1. **Debounce.** A change to `request` schedules the POST after
 *    `DERIVE_DEBOUNCE_MS` of quiet; edits arriving inside that window coalesce
 *    into a single request carrying the *last* edit. Without it a slider drag
 *    sprays one request per pixel.
 * 2. **One `AbortController` per request.** Starting a request aborts the
 *    previous one, and unmounting aborts whatever is in flight. An abort is a
 *    cancellation, never an error — the user moved a slider, nothing failed.
 * 3. **A monotonic sequence guard.** `abort()` does *not* un-resolve a
 *    response that was already in flight when the abort was issued, so abort
 *    alone is racy: a stale response can still arrive after a newer one. The
 *    sequence check before `setState` is what makes F0-S2 AC4 ("out-of-order
 *    responses cannot land") structurally true rather than probable.
 */

/**
 * [DEFAULT] ~150ms. The story says "~150ms"; the intent is "debounced so a
 * slider drag doesn't spam the endpoint", not a magic constant. Named rather
 * than inlined so the one place it is tuned is obvious.
 */
export const DERIVE_DEBOUNCE_MS = 150;

/** Presentation detail — not part of the contract QA's tests read. */
export type DeriveStatus = "idle" | "deriving" | "ready" | "error";

export interface UseDeriveResult {
  /** The last `DerivedState` that was allowed to land, or null before the first. */
  derived: DerivedState | null;
  status: DeriveStatus;
  /** Human-readable failure, or null. An aborted request never sets this. */
  error: string | null;
}

interface DeriveState {
  derived: DerivedState | null;
  status: DeriveStatus;
  error: string | null;
}

const IDLE: DeriveState = { derived: null, status: "idle", error: null };

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

export function useDerive(request: DeriveRequest | null): UseDeriveResult {
  const [state, setState] = useState<DeriveState>(IDLE);

  /**
   * The request is compared by *content*, not by object identity: callers
   * build the body inline on every render (see `views/Results.tsx`), so an
   * identity dep would re-derive on every keystroke elsewhere in the tree,
   * and a `useMemo` in every caller would be the same rule enforced by
   * convention instead of by construction. Serializing here also means the
   * dep and the POST body are literally the same string — they cannot drift.
   */
  const body = request === null ? null : JSON.stringify(request);

  /**
   * Monotonic across the hook's whole lifetime (a ref, not per-effect state):
   * "is this response still the newest?" is a question about every request
   * ever issued, not just the current effect's.
   */
  const latestSeq = useRef(0);

  useEffect(() => {
    if (body === null) return;

    let controller: AbortController | null = null;

    const timer = setTimeout(() => {
      controller = new AbortController();
      const seq = ++latestSeq.current;

      void (async () => {
        try {
          const response = await fetch("/api/derive", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body,
            signal: controller!.signal,
          });
          if (!response.ok) {
            throw new Error(
              `POST /api/derive returned ${response.status}: ${await response.text()}`
            );
          }
          const derived = (await response.json()) as DerivedState;
          if (seq !== latestSeq.current) return; // superseded — see (3) above
          setState({ derived, status: "ready", error: null });
        } catch (err: unknown) {
          if (isAbort(err) || seq !== latestSeq.current) return;
          setState((prev) => ({
            derived: prev.derived,
            status: "error",
            error: err instanceof Error ? err.message : String(err),
          }));
        }
      })();
    }, DERIVE_DEBOUNCE_MS);

    setState((prev) => ({ derived: prev.derived, status: "deriving", error: null }));

    return () => {
      clearTimeout(timer);
      controller?.abort();
    };
  }, [body]);

  return state;
}
