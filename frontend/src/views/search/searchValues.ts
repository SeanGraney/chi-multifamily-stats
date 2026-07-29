/**
 * F2-S1 — the search form's values, its inline validation, and the request
 * body it becomes. Separated from the component so all three are readable (and
 * changeable) without a browser in the loop.
 *
 * WHAT THIS FILE IS ALLOWED TO DECIDE, AND WHAT IT IS NOT (D5/D13)
 * -----------------------------------------------------------------
 * Allowed: whether the user has finished filling in a form. That is input
 * validation, not derivation — "address is blank" is a fact about a text box.
 *
 * Not allowed, and deliberately absent:
 *
 * * **The call estimate.** `POST /api/search/plan` computes it, and the
 *   component renders the number it gets back. `2 * yearsBack` looks like the
 *   same answer and is wrong on exactly the days a cohort window has not opened
 *   yet (F4-S1 AC4) — a user consenting to 6 calls and being charged 4 is the
 *   benign direction; the estimator is [INVARIANT] so that the other direction
 *   cannot happen either.
 * * **The cohort years.** Also the plan's, for the same reason: the planner's
 *   calendar rules (New Year wrap, Feb-29 clamp) are not something to
 *   re-implement against `new Date()`.
 * * **RentCast wire syntax.** `"1-3"` is submitted as the user typed it and
 *   `SearchRequest.plan_args()` runs it through F2-S2's parser. A
 *   `replace("-", ":")` here would be a second source of the colon rule that
 *   eight paid-for live calls bought, sitting outside every test protecting the
 *   first.
 *
 * The `min <= max` check below is the one that looks closest to the line. It is
 * on the right side of it: the backend parser refuses `"4-2"` rather than
 * reordering it (F2-S2, PM ruling), so without this the user's only feedback
 * would be a 422 after submitting — which is exactly what the story's "inline
 * validation (… min≤max)" clause exists to prevent.
 */
import type { components } from "../../api/schema";

export type SearchRequestBody = components["schemas"]["SearchRequest"];

export interface SearchValues {
  address: string;
  bedrooms: string;
  bathrooms: string;
  sqft: string;
  radius: string;
  windowStart: string;
  windowEnd: string;
  yearsBack: string;
  propertyTypes: string[];
  /** Exact only in V1 — see `SOURCE_MODES`. */
  sourceMode: string;
}

/**
 * §2.1: "Default: Multi-Family + Apartment + Townhouse". Apartment is in the
 * set on purpose — unit-level listings in Chicago 2-4 flats are frequently
 * typed "Apartment" in RentCast's taxonomy, so dropping it silently narrows
 * every pull.
 */
export const PROPERTY_TYPES = [
  "Multi-Family",
  "Apartment",
  "Townhouse",
  "Single Family",
  "Condo",
] as const;

export const DEFAULT_PROPERTY_TYPES = ["Multi-Family", "Apartment", "Townhouse"];

/**
 * §2.1's "Comp source mode | Exact / RentCast".
 *
 * Rendered, defaulted to Exact, and **disabled** (PM ruling A5): §5.5/§6.4 put
 * the `/avm` benchmark in v2, and NORTH_STAR excludes `/avm` from the pipeline
 * entirely — it is RentCast's own model output, and substituting it for
 * observed comps would corrupt what "premium" and "anchor" mean while passing
 * every numeric test we have. Showing the control as not-yet-available is
 * honest; silently omitting a documented §2.1 field is not.
 *
 * Wiring the RentCast option to anything is a Semantic Change Protocol event,
 * not a UI change.
 */
export const SOURCE_MODES = [
  { value: "exact", label: "Exact — /listings comps (primary)" },
  { value: "rentcast", label: "RentCast AVM benchmark — v2" },
] as const;

const MMDD = /^(\d{2})-(\d{2})$/;
const BARE = /^\d+(?:\.\d+)?$/;
const RANGE = /^(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)$/;

/** A radius past which a Chicago micro-market search is probably a slip. Soft:
 *  F2's edge says "absurd radius/window → soft warning, allow" — the tool does
 *  not know the user's intent and must not decide for them. */
const WIDE_RADIUS_MI = 25;

/** Days either side of today for the default seasonal window. A *form default*,
 *  not a derivation: which comps it selects is entirely the planner's. */
const DEFAULT_WINDOW_HALF_WIDTH_DAYS = 15;

function monthDay(date: Date): string {
  const month = `${date.getMonth() + 1}`.padStart(2, "0");
  const day = `${date.getDate()}`.padStart(2, "0");
  return `${month}-${day}`;
}

/**
 * A blank form: the season around today, two cohorts, the §2.1 type defaults.
 *
 * The window defaults to "listings from around this time of year" because that
 * is what a year-agnostic seasonal window is *for* — a January default would
 * make the first search of every summer wrong in a way nobody notices.
 */
export function emptySearchValues(today: Date = new Date()): SearchValues {
  const start = new Date(today);
  start.setDate(start.getDate() - DEFAULT_WINDOW_HALF_WIDTH_DAYS);
  const end = new Date(today);
  end.setDate(end.getDate() + DEFAULT_WINDOW_HALF_WIDTH_DAYS);
  return {
    address: "",
    bedrooms: "",
    bathrooms: "",
    sqft: "",
    radius: "1",
    windowStart: monthDay(start),
    windowEnd: monthDay(end),
    // §2.1: "Years back | 1-5, default 2".
    yearsBack: "2",
    propertyTypes: [...DEFAULT_PROPERTY_TYPES],
    sourceMode: "exact",
  };
}

export type FieldErrors = Partial<Record<keyof SearchValues, string>>;

/**
 * Validate a bed/bath field in the user's own hyphen form.
 *
 * Returns the message to show, or `undefined`. Fractions are legal for
 * bathrooms and not for bedrooms (§2.1); `min > max` is refused rather than
 * swapped, matching the backend parser exactly — a form that reordered what the
 * user typed while the parser refused it would be two different products.
 */
function validateRange(raw: string, label: string, allowFractions: boolean): string | undefined {
  const value = raw.trim();
  if (!value) return undefined;

  const bare = BARE.exec(value);
  if (bare) {
    if (!allowFractions && value.includes(".")) {
      return `${label} must be a whole number (studios are "0")`;
    }
    return undefined;
  }

  const range = RANGE.exec(value);
  if (!range) {
    return `${label} must be a number like "2" or a range like "1-3"`;
  }
  const [, lowRaw, highRaw] = range;
  if (!allowFractions && (lowRaw.includes(".") || highRaw.includes("."))) {
    return `${label} must be whole numbers (studios are "0")`;
  }
  if (Number(lowRaw) > Number(highRaw)) {
    return `${label} range is backwards — ${lowRaw} is greater than ${highRaw}. Enter ${highRaw}-${lowRaw}.`;
  }
  return undefined;
}

/** `"06-15"` → is that a month-day that exists? `"02-30"` is not, in any year. */
function validateMonthDay(raw: string, label: string): string | undefined {
  const value = raw.trim();
  if (!value) return `${label} is required (MM-DD)`;
  const parts = MMDD.exec(value);
  if (!parts) return `${label} must be MM-DD, e.g. 06-15`;
  const month = Number(parts[1]);
  const day = Number(parts[2]);
  if (month < 1 || month > 12) return `${label}: ${parts[1]} is not a month`;
  // 2000 is a leap year, so 02-29 is accepted here and clamped per cohort year
  // by the planner — the same reading `_parse_mmdd` takes.
  const probe = new Date(2000, month - 1, day);
  if (day < 1 || probe.getMonth() !== month - 1 || probe.getDate() !== day) {
    return `${label}: ${value} is not a real date`;
  }
  return undefined;
}

/** Everything that must be fixed before the search may be dispatched. */
export function validate(values: SearchValues): FieldErrors {
  const errors: FieldErrors = {};

  if (!values.address.trim()) {
    errors.address = "Address is required — RentCast geocodes it to centre the comp search.";
  }

  const sqft = Number(values.sqft.trim());
  if (!values.sqft.trim() || !Number.isFinite(sqft) || sqft <= 0) {
    // A block, not a warning: the anchor is a weighted median $/sqft times the
    // subject's sqft, so without it there is no anchor to compute at all.
    errors.sqft = "Unit sqft is required — the anchor is $/sqft × subject sqft.";
  }

  if (!values.bedrooms.trim()) {
    errors.bedrooms = 'Bedrooms is required — e.g. "3" for an exact match, or "2-4" for a range.';
  } else {
    errors.bedrooms = validateRange(values.bedrooms, "Bedrooms", false);
  }
  errors.bathrooms = validateRange(values.bathrooms, "Bathrooms", true);

  const radius = Number(values.radius.trim());
  if (!values.radius.trim() || !Number.isFinite(radius) || radius <= 0) {
    errors.radius = "Radius is required and must be greater than 0.";
  } else if (radius > 100) {
    errors.radius = "Radius must be 100 miles or less.";
  }

  errors.windowStart = validateMonthDay(values.windowStart, "Window start");
  errors.windowEnd = validateMonthDay(values.windowEnd, "Window end");

  const yearsBack = Number(values.yearsBack.trim());
  if (!Number.isInteger(yearsBack) || yearsBack < 1 || yearsBack > 5) {
    errors.yearsBack = "Years back must be a whole number from 1 to 5.";
  }

  if (values.propertyTypes.length === 0) {
    errors.propertyTypes = "Choose at least one property type.";
  }

  for (const key of Object.keys(errors) as (keyof SearchValues)[]) {
    if (errors[key] === undefined) delete errors[key];
  }
  return errors;
}

/** Soft advisories. Never block — F2's edge is explicit that an absurd radius
 *  or window is allowed once the user has seen the flag. */
export function warnings(values: SearchValues): FieldErrors {
  const result: FieldErrors = {};
  const radius = Number(values.radius.trim());
  if (Number.isFinite(radius) && radius > WIDE_RADIUS_MI) {
    result.radius =
      `${values.radius.trim()} miles is a very wide comp radius for a small-multifamily ` +
      "micro-market — allowed, but check it is what you meant.";
  }
  return result;
}

/**
 * The wire body, with every string trimmed (PM ruling 3).
 *
 * Trimmed here as well as on the server, because both matter and for different
 * reasons: the server's trim is what makes the contract true for any client,
 * and this one is what makes the request the user can *see* in devtools the
 * same as the one that was sent. `plan_pull_queries` refuses `"06-01 "`
 * outright and F3-S1's cache key is a hash of these exact strings, so a space
 * that survived would either 422 or make the user pay twice for one search.
 *
 * `sqft` and `sourceMode` are deliberately absent: neither changes which
 * records come back, and keeping them out is what makes the cache key a
 * function of the search rather than of everything the user has touched.
 */
export function toRequestBody(values: SearchValues): SearchRequestBody {
  const bathrooms = values.bathrooms.trim();
  return {
    address: values.address.trim(),
    radius: Number(values.radius.trim()),
    bedrooms: values.bedrooms.trim(),
    bathrooms: bathrooms || null,
    property_types: values.propertyTypes,
    years_back: Number(values.yearsBack.trim()),
    window_start: values.windowStart.trim(),
    window_end: values.windowEnd.trim(),
  };
}

/**
 * Whether the *pull parameters* are complete enough to price.
 *
 * Deliberately not "no errors at all": `sqft` is a subject attribute that never
 * reaches `SearchRequest`, so a preview that waited for it would withhold the
 * call estimate over the one field that cannot possibly change it.
 *
 * Takes the already-computed errors rather than re-running `validate`, so the
 * component's memo is the single evaluation per keystroke.
 */
export function isPlannable(errors: FieldErrors): boolean {
  return (Object.keys(errors) as (keyof SearchValues)[]).every((key) => key === "sqft");
}
