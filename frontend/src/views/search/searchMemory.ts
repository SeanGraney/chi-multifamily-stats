/**
 * What the search form remembers between visits (F2-S1: "prefill from subject
 * where sensible").
 *
 * The story's clause has two readings and the PM ratified this one: on a
 * *first* search the form is where the subject gets defined, so §2.1's "default
 * exact-match to subject" is satisfied by construction — there is one
 * beds/baths field, and it is the subject's. The reading with a testable
 * consequence is the returning user's: someone narrowing a radius or widening a
 * window should not have to retype an address, beds, baths and sqft to change
 * one number.
 *
 * `localStorage`, not the server: this is unsubmitted form state for a
 * single-user local tool (D1), not evidence and not curation. Curation state
 * belongs in `workspaces/` and is F14's; putting a half-filled form there would
 * make a workspace mean two things.
 *
 * Reads are total — a corrupt or stale entry yields the blank form rather than
 * throwing. A remembered search is a convenience, and no convenience is worth
 * a screen that will not mount.
 */
import { emptySearchValues, type SearchValues } from "./searchValues";

const STORAGE_KEY = "rentcomp.search.last";

/** Every key `SearchValues` has, so a shape change cannot half-restore. */
function isSearchValues(value: unknown): value is SearchValues {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  const strings = [
    "address",
    "bedrooms",
    "bathrooms",
    "sqft",
    "radius",
    "windowStart",
    "windowEnd",
    "yearsBack",
    "sourceMode",
  ];
  if (!strings.every((key) => typeof candidate[key] === "string")) return false;
  return (
    Array.isArray(candidate.propertyTypes) &&
    candidate.propertyTypes.every((entry) => typeof entry === "string")
  );
}

export function loadSearchValues(): SearchValues {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return emptySearchValues();
    const parsed: unknown = JSON.parse(raw);
    return isSearchValues(parsed) ? parsed : emptySearchValues();
  } catch {
    // Private-mode localStorage throws on access in some browsers, and a
    // half-written entry throws on parse. Neither is worth a blank screen.
    return emptySearchValues();
  }
}

export function saveSearchValues(values: SearchValues): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(values));
  } catch {
    // Storage full or unavailable: the search still runs, it just will not be
    // remembered. Silent because there is nothing the user could do about it
    // and nothing they lose right now.
  }
}
