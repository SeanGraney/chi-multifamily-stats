import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import type { components } from "../../api/schema";
import { isAbort, postJson } from "../../api/http";
import {
  PROPERTY_TYPES,
  SOURCE_MODES,
  type SearchValues,
  isPlannable,
  toRequestBody,
  validate,
  warnings,
} from "./searchValues";
import { loadSearchValues, saveSearchValues } from "./searchMemory";

type SearchPlan = components["schemas"]["SearchPlan"];
type SearchResult = components["schemas"]["SearchResult"];

/**
 * F2 · New Search (spec §6.3) — every §2.1 field, inline validation, and a
 * live cohort/cost preview that costs nothing to keep current.
 *
 * D5/D13, restated because this is the screen most tempting to break it: this
 * component computes no statistic. The call estimate and the cohort years are
 * read straight off `POST /api/search/plan`'s response. The only arithmetic
 * here is `validate()`'s — "is this box filled in", which is not derivation.
 *
 * WHY THE PREVIEW HAS ITS OWN ROUTE
 * ----------------------------------
 * It fires on every edit. `POST /api/search` pulls on a cache miss, so driving
 * the preview from it would spend the month's 50 calls while the user was still
 * typing an address. `POST /api/search/plan` is structurally incapable of
 * spending (`api/plan.py`), which is what makes a live preview safe at all.
 *
 * WHAT HAPPENS ON SUBMIT, AND WHERE THIS STORY STOPS
 * ---------------------------------------------------
 * The form dispatches `POST /api/search` and hands the outcome back. F2 step 4
 * branches to F3's cache modal when an entry already exists — that modal is
 * F3-S2's story, and it is safe to be missing today because the submit route
 * never re-fetches a cached search without `force_refresh` (F4-S9). The
 * preview's `cache_status` is already on screen, so the user is not surprised
 * either way. Rendering the pulled comps is F4-S6's.
 */
export interface SearchFormProps {
  onCancel: () => void;
  onSubmitted: (result: SearchResult, values: SearchValues) => void;
  /**
   * Open the form on these values instead of the remembered ones (F4-S6 AC3 —
   * a widen shortcut hands back the same search with one constraint loosened).
   * Absent means the normal `searchMemory` behaviour.
   */
  initialValues?: SearchValues;
}

/**
 * Debounce before previewing. Longer than `useDerive`'s 150ms (D13) on
 * purpose: a derive is local arithmetic, while this reads a manifest off disk,
 * and nothing on screen is waiting for it — the number matters when the user
 * stops typing, not while they are mid-address.
 */
const PREVIEW_DEBOUNCE_MS = 400;

export default function SearchForm({ onCancel, onSubmitted, initialValues }: SearchFormProps) {
  const [values, setValues] = useState<SearchValues>(() => initialValues ?? loadSearchValues());
  const [touched, setTouched] = useState<Partial<Record<keyof SearchValues, boolean>>>({});
  const [attempted, setAttempted] = useState(false);
  const [plan, setPlan] = useState<SearchPlan | null>(null);
  const [planError, setPlanError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const errors = useMemo(() => validate(values), [values]);
  const softWarnings = useMemo(() => warnings(values), [values]);
  const plannable = isPlannable(errors);

  // Serialized rather than passed as an object so the effect re-runs when the
  // request actually changes, not on every keystroke that leaves it identical.
  const requestKey = useMemo(
    () => (plannable ? JSON.stringify(toRequestBody(values)) : null),
    [plannable, values],
  );

  useEffect(() => {
    if (!requestKey) {
      setPlan(null);
      setPlanError(null);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      postJson<SearchPlan>("/api/search/plan", JSON.parse(requestKey), controller.signal)
        .then((next) => {
          setPlan(next);
          setPlanError(null);
        })
        .catch((error: unknown) => {
          if (isAbort(error)) return; // a newer edit won — normal, not a failure
          setPlan(null);
          setPlanError(error instanceof Error ? error.message : String(error));
        });
    }, PREVIEW_DEBOUNCE_MS);

    // One AbortController per request (D13): the latest response wins, and a
    // superseded one never lands on screen out of order.
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [requestKey]);

  function set<K extends keyof SearchValues>(field: K, value: SearchValues[K]) {
    setValues((current) => ({ ...current, [field]: value }));
    setTouched((current) => ({ ...current, [field]: true }));
  }

  function toggleType(type: string) {
    setValues((current) => ({
      ...current,
      propertyTypes: current.propertyTypes.includes(type)
        ? current.propertyTypes.filter((entry) => entry !== type)
        : [...current.propertyTypes, type],
    }));
    setTouched((current) => ({ ...current, propertyTypes: true }));
  }

  /** An error is shown once the user has been near the field, or once they have
   *  tried to submit — so a blank form does not open shouting. */
  function shownError(field: keyof SearchValues): string | undefined {
    return attempted || touched[field] ? errors[field] : undefined;
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setAttempted(true);
    setSubmitError(null);
    if (Object.keys(errors).length > 0) {
      // The whole point of inline validation: an invalid search never reaches
      // the route that can spend money.
      return;
    }
    const requestBody = toRequestBody(values);

    /**
     * Make sure the plan is in hand BEFORE the progress surface goes up.
     *
     * The whole content of that surface is the planner's cohort years (F4 step
     * 2's "per-year pull"; D5 — the browser must never reconstruct them), and
     * the preview is debounced 400ms, so a user who fills the last field and
     * submits immediately would otherwise watch a phase list that cannot say
     * which years it is pulling. Cheap and safe to wait for: `POST
     * /api/search/plan` is structurally incapable of spending a call
     * (`api/plan.py`), and it is skipped entirely in the common case where the
     * preview has already landed.
     *
     * Best-effort: a plan that fails to load must not block the search the user
     * asked for. The progress surface degrades to the phases without years, and
     * the search itself carries its own error reporting.
     */
    if (!plan) {
      try {
        setPlan(await postJson<SearchPlan>("/api/search/plan", requestBody));
      } catch {
        /* the pull is still the user's to run — see above */
      }
    }

    setSubmitting(true);
    try {
      const result = await postJson<SearchResult>("/api/search", requestBody);
      saveSearchValues(values);
      onSubmitted(result, values);
    } catch (error: unknown) {
      setSubmitError(error instanceof Error ? error.message : String(error));
    } finally {
      setSubmitting(false);
    }
  }

  // F4 step 2: "progress indicator: per-year pull → stitching → deriving". The
  // pull is one long request, so while it is in flight there is nothing else
  // honest to show — the form is replaced rather than left sitting behind a
  // disabled button for the length of a real pull. A failure puts it back, with
  // `submitError` on it.
  if (submitting) {
    return <PipelineProgress plan={plan} />;
  }

  return (
    <form
      data-testid="search-form"
      aria-label="New search"
      onSubmit={handleSubmit}
      className="p-6 max-w-[560px]"
    >
      <h1 className="text-amber text-xl">New search</h1>

      <Field id="search-address" label="Address" error={shownError("address")}>
        <input
          id="search-address"
          data-testid="search-address"
          type="text"
          value={values.address}
          placeholder="Street, City, State, Zip"
          onChange={(event) => set("address", event.target.value)}
          className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
        />
      </Field>

      <div className="flex gap-3">
        <Field id="search-bedrooms" label="Bedrooms" error={shownError("bedrooms")}>
          <input
            id="search-bedrooms"
            data-testid="search-bedrooms"
            type="text"
            value={values.bedrooms}
            placeholder='3 or 2-4'
            onChange={(event) => set("bedrooms", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>

        <Field id="search-bathrooms" label="Bathrooms" error={shownError("bathrooms")}>
          <input
            id="search-bathrooms"
            data-testid="search-bathrooms"
            type="text"
            value={values.bathrooms}
            placeholder="1.5 or blank for any"
            onChange={(event) => set("bathrooms", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>
      </div>

      <div className="flex gap-3">
        <Field id="search-sqft" label="Unit sqft" error={shownError("sqft")}>
          <input
            id="search-sqft"
            data-testid="search-sqft"
            type="text"
            inputMode="numeric"
            value={values.sqft}
            onChange={(event) => set("sqft", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>

        <Field id="search-radius" label="Radius (mi)" error={shownError("radius")}>
          <input
            id="search-radius"
            data-testid="search-radius"
            type="text"
            inputMode="decimal"
            value={values.radius}
            onChange={(event) => set("radius", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
          {softWarnings.radius && (
            <p data-testid="search-warning-radius" className="mt-1 text-xs text-amber">
              {softWarnings.radius}
            </p>
          )}
        </Field>
      </div>

      <div className="flex gap-3">
        <Field id="search-window-start" label="Window start (MM-DD)" error={shownError("windowStart")}>
          <input
            id="search-window-start"
            data-testid="search-window-start"
            type="text"
            value={values.windowStart}
            placeholder="06-15"
            onChange={(event) => set("windowStart", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>

        <Field id="search-window-end" label="Window end (MM-DD)" error={shownError("windowEnd")}>
          <input
            id="search-window-end"
            data-testid="search-window-end"
            type="text"
            value={values.windowEnd}
            placeholder="06-30"
            onChange={(event) => set("windowEnd", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>

        <Field id="search-years-back" label="Years back" error={shownError("yearsBack")}>
          <input
            id="search-years-back"
            data-testid="search-years-back"
            type="number"
            min={1}
            max={5}
            step={1}
            value={values.yearsBack}
            onChange={(event) => set("yearsBack", event.target.value)}
            className="w-full border border-grey bg-transparent px-2 py-1 text-sm"
          />
        </Field>
      </div>

      <fieldset data-testid="search-property-types" className="mt-3 border border-grey p-2">
        <legend className="text-xs uppercase text-grey">Property types</legend>
        <div className="flex flex-wrap gap-3">
          {PROPERTY_TYPES.map((type) => (
            <label key={type} className="flex items-center gap-1 text-sm text-white">
              <input
                type="checkbox"
                value={type}
                checked={values.propertyTypes.includes(type)}
                onChange={() => toggleType(type)}
              />
              {type}
            </label>
          ))}
        </div>
        {shownError("propertyTypes") && (
          <p data-testid="search-error-propertyTypes" role="alert" className="mt-1 text-xs text-rust">
            {shownError("propertyTypes")}
          </p>
        )}
      </fieldset>

      <Field id="search-source-mode" label="Comp source">
        <select
          id="search-source-mode"
          data-testid="search-source-mode"
          value={values.sourceMode}
          disabled
          onChange={(event) => set("sourceMode", event.target.value)}
          className="w-full border border-grey bg-transparent px-2 py-1 text-sm text-grey"
        >
          {SOURCE_MODES.map((mode) => (
            <option key={mode.value} value={mode.value}>
              {mode.label}
            </option>
          ))}
        </select>
        {/* PM ruling A5. The RentCast/AVM mode is a v2 *benchmark* (§5.5, §6.4)
            and NORTH_STAR excludes /avm from the pipeline entirely — it is
            RentCast's own model output, not observed evidence. Shown as
            unavailable rather than omitted, because a silently missing §2.1
            field looks like a bug. */}
        <p className="mt-1 text-xs text-grey">
          Exact only in V1. The RentCast AVM benchmark arrives in v2 — it is a
          model estimate, never comp evidence.
        </p>
      </Field>

      <PreviewLine plan={plan} plannable={plannable} error={planError} values={values} />

      {submitError && (
        <p data-testid="search-error-submit" role="alert" className="mt-3 text-sm text-rust">
          {submitError}
        </p>
      )}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          data-testid="search-cancel"
          onClick={onCancel}
          className="border border-grey px-4 py-1 text-sm text-grey"
        >
          Cancel
        </button>
        <button
          type="submit"
          data-testid="search-submit"
          disabled={submitting}
          className="border border-green px-4 py-1 text-sm text-green"
        >
          {submitting ? "Searching…" : "Search"}
        </button>
      </div>
    </form>
  );
}

/**
 * F4 step 2's progress indicator: **per-year pull → stitching → deriving**
 * (F4-S6 AC1).
 *
 * PHASE-LEVEL, AND HONEST ABOUT WHY
 * ----------------------------------
 * `POST /api/search` is one request that runs the whole plan server-side, so
 * the browser cannot observe which phase is executing. Two options follow, and
 * only one of them is allowed here: animate a bar that *looks* like measured
 * progress, or name the phases the pipeline actually runs and say plainly that
 * they are a description rather than a position. A progress indicator that
 * implies knowledge it does not have is the same class of defect as a number
 * the evidence does not support — smaller stakes, identical shape — so this
 * names the work and claims nothing about how far through it is.
 *
 * THE YEARS ARE THE PLAN'S, NEVER THE BROWSER'S ARITHMETIC (D5)
 * -------------------------------------------------------------
 * `plan.cohorts[].year` comes off `POST /api/search/plan`. Reconstructing it
 * here as `new Date().getFullYear() - i` would be a second implementation of
 * the planner's calendar rules — it wraps a window that spans New Year and
 * clamps Feb 29 in non-leap years — and the two would disagree on exactly the
 * dates a user would never think to check. Same rule the preview line above
 * already follows, and pinned by `f4s6-pipeline-states.spec.ts` AC1b, which
 * serves a plan of 1999/1998 precisely because no clock can produce those.
 *
 * `fetchable: false` is a planned window that has not opened yet (F4-S1 AC4).
 * It is listed and marked rather than hidden: a cohort silently missing from
 * the progress list is the same "silently absent" problem one layer up.
 */
function PipelineProgress({ plan }: { plan: SearchPlan | null }) {
  return (
    <section data-testid="pipeline-progress" role="status" aria-live="polite" className="p-6">
      <h1 className="text-amber text-xl">Running the pull</h1>
      <p className="mt-2 text-xs text-grey">
        One request runs the whole plan, so these are the phases it works
        through — not a measured position.
      </p>
      <ol className="mt-3 text-sm text-white">
        {(plan?.cohorts ?? []).map((cohort) => (
          <li key={cohort.year} className="py-0.5">
            Pull · {cohort.year}
            {cohort.fetchable ? (
              ""
            ) : (
              <span className="text-grey"> — not yet open, nothing to fetch</span>
            )}
          </li>
        ))}
        <li className="py-0.5">Stitch · re-lists merged into one spell</li>
        <li className="py-0.5">Derive · premiums, anchor, buckets</li>
      </ol>
      {plan && (
        <p className="mt-3 text-xs text-grey">
          {plan.cohorts.length} {plan.cohorts.length === 1 ? "cohort" : "cohorts"} · est.{" "}
          {plan.estimated_calls} API calls
        </p>
      )}
    </section>
  );
}

/** Label above, control below, error underneath — one layout, ten fields. */
function Field({
  id,
  label,
  error,
  children,
}: {
  id: string;
  label: string;
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="mt-3 flex-1">
      <label htmlFor={id} className="block text-xs uppercase text-grey">
        {label}
      </label>
      {children}
      {error && (
        <p data-testid={`search-error-${id.replace("search-", "")}`} role="alert" className="mt-1 text-xs text-rust">
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * Spec §6.3's preview line: *"Jun 15–30 · 2026, 2025 (2 cohorts) · est. N API
 * calls."*
 *
 * Every fact in it comes from the response. The cohort years in particular are
 * the backend's, not `new Date().getFullYear() - i`: the planner wraps a window
 * that spans New Year and clamps Feb 29 in non-leap years, and a second
 * implementation of those rules here would disagree with the pull on exactly
 * the dates the user would never think to check.
 */
function PreviewLine({
  plan,
  plannable,
  error,
  values,
}: {
  plan: SearchPlan | null;
  plannable: boolean;
  error: string | null;
  values: SearchValues;
}) {
  if (!plannable) {
    return (
      <p className="mt-4 text-xs text-grey">
        Fill in the address, bedrooms, radius and date window to price this search.
      </p>
    );
  }
  if (error) {
    return (
      <p data-testid="search-error-preview" role="alert" className="mt-4 text-xs text-rust">
        {error}
      </p>
    );
  }
  if (!plan) {
    return (
      <p data-testid="search-preview" className="mt-4 text-xs text-grey">
        Pricing this search…
      </p>
    );
  }

  const years = plan.cohorts.map((cohort) => cohort.year).join(", ");
  const notYetOpen = plan.cohorts.filter((cohort) => !cohort.fetchable).map((c) => c.year);

  return (
    <p data-testid="search-preview" className="mt-4 text-xs text-white">
      {values.windowStart.trim()}–{values.windowEnd.trim()} · {years} ({plan.cohorts.length}{" "}
      {plan.cohorts.length === 1 ? "cohort" : "cohorts"}) · est. {plan.estimated_calls} API calls
      {notYetOpen.length > 0 && (
        <span className="text-grey">
          {" "}
          · {notYetOpen.join(", ")} planned but not yet open — free, nothing to fetch
        </span>
      )}
      {plan.cache_status !== "miss" && (
        <span className="text-green">
          {" "}
          · already{" "}
          {plan.cache_status === "hit" ? "cached — this search costs nothing" : "partly cached"}
        </span>
      )}
    </p>
  );
}
