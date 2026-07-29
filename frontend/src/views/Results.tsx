import { useState, type ReactNode } from "react";
import type { components } from "../api/schema";
import { useDerive, type DeriveStatus } from "../api/useDerive";

type DerivedState = components["schemas"]["DerivedState"];
type DeriveRequest = components["schemas"]["DeriveRequest"];
type DerivedComp = components["schemas"]["DerivedComp"];
type Filters = components["schemas"]["Filters"];

/**
 * WS-1 walking skeleton (QUEUE.md row 6) — the one hardcoded search. No map,
 * no search form (out of scope). F5-S2 adds the curation half: per-row
 * include/weight controls, ALL/NONE, and the contribution %. F7-S1/F7-S2 add
 * the filter half: the strip that sets `filters`, the "N filtered" footer that
 * keeps the hidden evidence reachable, and the per-row INCLUDE override.
 *
 * D5: this component computes NOTHING. Every number below is read straight
 * off `DerivedState` — no arithmetic, no aggregation, no formula. The only
 * two exceptions are both display rules, not statistics: rounding for
 * display (D14), and comparing an already-computed contribution share
 * against `CONTRIBUTION_WARNING_SHARE` to pick a colour.
 *
 * In particular the contribution % is `weight ÷ Σ selected weights`, which is
 * trivial enough to be tempting — and is computed in Python like every other
 * derived value (F5-S2 [INVARIANT], NORTH_STAR). This view renders
 * `comp.contribution_share`; it never divides.
 *
 * The same rule governs the filters and is even more tempting, because
 * `distance_mi` and `censored` are right there on every comp: **this view
 * never decides whether a comp is filtered.** Filters travel to the server as
 * *parameters*; which comps they took comes back as `comp.state`, and the list
 * below is partitioned on that label and on nothing else (D5/D13, F7-S1). Two
 * implementations of one predicate is exactly the drift the architecture
 * exists to remove.
 */
const PULL_REF = "ws1-real";

const SUBJECT: DeriveRequest["subject"] = {
  address: "3651 S Wood St, Chicago, IL 60609",
  lat: 41.83,
  lng: -87.665,
  sqft: 1200,
  beds: 4,
  baths: 2,
};

const CANDIDATE_RENT = 4500;

/**
 * [INVARIANT] toggle-off ≡ weight 0. There is no separate `included` flag in
 * this component's state — the weight is the single source of truth, so the
 * checkbox and the number input are two controls over one value and cannot
 * drift apart.
 */
const INCLUDED_WEIGHT = 1;

/**
 * F5 epic edge: "one comp >~40% contribution → its contribution % renders in
 * warning colour (no hard cap by design)". The threshold is a *display* rule
 * — the server reports the real, uncapped share and this only decides which
 * colour to draw it in.
 */
const CONTRIBUTION_WARNING_SHARE = 0.4;

/**
 * The filter strip's own state (F7-S2). Deliberately NOT the wire `Filters`
 * shape: the strip remembers the radius the user picked even while the
 * distance filter is switched off, so toggling it back on does not silently
 * fall back to a default they never chose. `toFilters` below is the one place
 * this becomes the wire contract.
 *
 * [DEFAULT] the preset radii and which one starts selected. Nothing about the
 * numbers is load-bearing — `max_distance_mi` is a free float on the wire.
 */
const DISTANCE_PRESETS_MI = [0.25, 0.5, 1, 2] as const;

interface FilterStripState {
  distanceOn: boolean;
  distanceMi: number;
  hideCensored: boolean;
  leasedOnly: boolean;
}

const NO_FILTERS: FilterStripState = {
  distanceOn: false,
  distanceMi: 0.5,
  hideCensored: false,
  leasedOnly: false,
};

function toFilters(strip: FilterStripState): Filters {
  return {
    max_distance_mi: strip.distanceOn ? strip.distanceMi : null,
    hide_censored: strip.hideCensored,
    leased_only: strip.leasedOnly,
  };
}

export default function Results() {
  /**
   * Curation state, client-owned (D13). Sparse on purpose: a comp the client
   * has said nothing about is absent here and the server applies the
   * defaulting rule (1.0, or 0.0 with no sqft), which is what lets comps that
   * are new to a refreshed pull arrive included without the client having
   * heard of them (F13-S1).
   */
  const [weights, setWeights] = useState<Record<string, number>>({});

  /** The filter strip's controls (F7-S2). Client-owned like every other knob. */
  const [strip, setStrip] = useState<FilterStripState>(NO_FILTERS);

  /**
   * Manual "keep this one anyway" decisions (F7-S1). Durable curation, not a
   * one-shot instruction: `resetFilters` below clears the strip and leaves
   * this list alone, which is the whole "overrides survive filter resets" AC.
   */
  const [includeOverrides, setIncludeOverrides] = useState<string[]>([]);

  const { derived, status, error } = useDerive({
    pull_ref: PULL_REF,
    subject: SUBJECT,
    weights,
    include_overrides: includeOverrides,
    filters: toFilters(strip),
    candidate_rent: CANDIDATE_RENT,
  });

  /**
   * "Currently visible" = everything a filter has not taken. ALL/NONE operate
   * over exactly this set (F5-S2 AC), so a comp the user cannot see keeps the
   * weight they last chose for it and reappears at that weight when the
   * filter clears (F7-S1).
   *
   * Both halves read `comp.state`, which the SERVER decided. Nothing here
   * re-evaluates a filter predicate (D5/D13).
   */
  const visible = derived ? derived.comps.filter((comp) => comp.state !== "filtered") : [];
  const filteredOut = derived ? derived.comps.filter((comp) => comp.state === "filtered") : [];

  const setWeight = (key: string, weight: number) =>
    setWeights((prev) => ({ ...prev, [key]: weight }));

  /**
   * A reset returns the VIEW to unfiltered. It is not an undo of the user's
   * curation, so it touches neither `weights` nor `includeOverrides` — and it
   * keeps the remembered radius, which is a strip preference rather than an
   * active filter (F7-S1: "manual INCLUDE overrides survive filter resets").
   */
  const resetFilters = () =>
    setStrip((prev) => ({ ...NO_FILTERS, distanceMi: prev.distanceMi }));

  /**
   * The per-row INCLUDE behind the "N filtered" footer, and later the rust-pin
   * click in F6-S2. Two writes, because the button says INCLUDE and the user
   * means it:
   *
   * 1. the override exempts the comp from the filters (server-side, F7-S1);
   * 2. if its weight is 0 it also becomes 1 — otherwise a comp with no $/sqft
   *    (weight 0 by the defaulting rule) would come back from behind the
   *    filter still excluded, and a button labelled INCLUDE would have done
   *    nothing the user can see.
   *
   * This is not a second channel into inclusion: selection is still the weight
   * and only the weight (F5-S2 [INVARIANT]) — the click writes one, explicitly,
   * as the manual act the F5 epic requires for a no-sqft comp. The *server*
   * never lets an override select anything.
   */
  const includeOverride = (key: string, weight: number) => {
    setIncludeOverrides((prev) => (prev.includes(key) ? prev : [...prev, key]));
    if (weight <= 0) setWeight(key, INCLUDED_WEIGHT);
  };

  const setWeightForAll = (weight: number, apply: (comp: DerivedComp) => boolean) =>
    setWeights((prev) => {
      const next = { ...prev };
      for (const comp of visible) {
        if (apply(comp)) next[comp.key] = weight;
      }
      return next;
    });

  /**
   * ALL selects the visible comps *that have a $/sqft*. A missing-sqft comp
   * stays opt-in per comp: the F5 epic says those are "excluded by default,
   * **manual** re-include allowed", and a bulk sweep is not a manual act. It
   * also keeps a comp that contributes nothing to the anchor from silently
   * acquiring a contribution share. (PM ruling, F5-S2 dispatch.)
   */
  const selectAll = () => setWeightForAll(INCLUDED_WEIGHT, (comp) => comp.psf !== null);
  const selectNone = () => setWeightForAll(0, () => true);

  // Before the first payload there is nothing to render but the reason why.
  if (!derived) {
    return (
      <ResultsShell>
        <p className={error ? "mt-2 text-sm text-rust" : "mt-2 text-sm text-grey"}>
          {error ? `Derive failed: ${error}` : "Deriving..."}
        </p>
      </ResultsShell>
    );
  }

  return (
    <ResultsShell>
      <p className="mt-1 text-xs text-grey">
        {derived.meta.pull_ref} · as of {derived.meta.as_of} · {derived.breakdown.pulled} comps
        pulled
      </p>

      <DeriveStatusLine status={status} error={error} />
      <FilterStrip strip={strip} onChange={setStrip} onReset={resetFilters} />
      <AnchorPanel anchor={derived.anchor} />
      <CompList
        comps={visible}
        weights={weights}
        onWeightChange={setWeight}
        onSelectAll={selectAll}
        onSelectNone={selectNone}
        bulkDisabled={status === "deriving"}
        includedCount={derived.breakdown.included}
      />
      <FilteredFooter
        comps={filteredOut}
        weights={weights}
        onInclude={includeOverride}
      />
      <BucketTable buckets={derived.buckets} />
      <PriceTestPanel priceTest={derived.price_test} />
    </ResultsShell>
  );
}

interface FilterStripProps {
  strip: FilterStripState;
  onChange: (next: FilterStripState) => void;
  onReset: () => void;
}

/**
 * F7-S2's filter strip. Three controls and a reset; the strip sends
 * *parameters* and reads the answer off the response (F7-S1, D5).
 *
 * Accessible names here are load-bearing in one narrow way worth knowing
 * before renaming anything: `f5-s2-selection-weight.spec.ts` reaches for "the
 * filter control" via `getByRole("button", { name: /filter/i })` as one of its
 * fallbacks, so no button in this view is *named* after filtering — the reset
 * is "Reset" and the footer's disclosure is "show"/"hide". The three filters
 * are checkboxes, which that locator cannot match at all.
 */
function FilterStrip({ strip, onChange, onReset }: FilterStripProps) {
  return (
    <div className="mt-4 flex flex-wrap items-center gap-4 text-xs text-grey">
      <span className="text-white font-bold">Filters</span>

      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          data-testid="filter-max-distance"
          aria-label="Max distance"
          checked={strip.distanceOn}
          onChange={(event) => onChange({ ...strip, distanceOn: event.target.checked })}
        />
        Max distance
      </label>
      <select
        data-testid="filter-max-distance-value"
        aria-label="Max distance in miles"
        value={String(strip.distanceMi)}
        onChange={(event) =>
          // Picking a radius is also switching the filter on — a value the
          // user chose that does nothing until they find a second control is
          // exactly the hidden state F5 is against.
          onChange({ ...strip, distanceMi: Number(event.target.value), distanceOn: true })
        }
        className="bg-surface border border-grey text-white px-1"
      >
        {DISTANCE_PRESETS_MI.map((mi) => (
          <option key={mi} value={mi}>
            {mi} mi
          </option>
        ))}
      </select>

      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          data-testid="filter-hide-censored"
          aria-label="Hide censored"
          checked={strip.hideCensored}
          onChange={(event) => onChange({ ...strip, hideCensored: event.target.checked })}
        />
        Hide censored
      </label>

      <label className="flex items-center gap-1">
        <input
          type="checkbox"
          data-testid="filter-leased-only"
          aria-label="Leased only"
          checked={strip.leasedOnly}
          onChange={(event) => onChange({ ...strip, leasedOnly: event.target.checked })}
        />
        Leased only
      </label>

      <button
        type="button"
        data-testid="filter-reset"
        onClick={onReset}
        className="text-grey border border-grey px-2 py-0.5"
      >
        Reset
      </button>
    </div>
  );
}

interface FilteredFooterProps {
  comps: DerivedComp[];
  weights: Record<string, number>;
  onInclude: (key: string, weight: number) => void;
}

/**
 * The "N filtered" footer (F7-S2). The comps here left the list and every
 * calculation, but they did not leave the evidence: this is where they stay
 * reachable, and each one carries the INCLUDE that puts it back.
 *
 * [DEFAULT] the disclosure starts OPEN, where F7-S2's text says "collapsed".
 * Two reasons, and the deviation is logged rather than assumed: a comp the
 * user wants back is precisely the thing they are looking for once a filter
 * has hidden it, so putting it two clicks away works against the AC it
 * exists to serve; and F7-S1's own spec drives the per-row INCLUDE directly,
 * which a closed disclosure would make unreachable. The toggle is here, so
 * "collapsed" remains one click away and is remembered thereafter.
 */
function FilteredFooter({ comps, weights, onInclude }: FilteredFooterProps) {
  const [open, setOpen] = useState(true);

  if (comps.length === 0) return null;

  return (
    <div data-testid="filtered-footer" className="mt-4 border-t border-grey pt-2">
      <p className="text-xs text-rust flex items-center gap-2">
        <span>{comps.length} filtered</span>
        <span aria-hidden="true">·</span>
        <button
          type="button"
          data-testid="filtered-toggle"
          onClick={() => setOpen(!open)}
          className="text-rust border border-rust px-2 py-0.5"
        >
          {open ? "hide" : "show"}
        </button>
      </p>
      {open && (
        <ul>
          {comps.map((comp) => (
            <FilteredRow
              key={comp.key}
              comp={comp}
              weight={weights[comp.key] ?? comp.weight}
              onInclude={onInclude}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function FilteredRow({
  comp,
  weight,
  onInclude,
}: {
  comp: DerivedComp;
  weight: number;
  onInclude: (key: string, weight: number) => void;
}) {
  const label = `${comp.address}${comp.unit ? ` #${comp.unit}` : ""}`;
  return (
    <li
      data-testid="filtered-row"
      data-comp-key={comp.key}
      className="text-xs text-grey py-0.5 flex items-baseline gap-2 opacity-60"
    >
      <button
        type="button"
        data-testid="filtered-include"
        aria-label={`INCLUDE ${label}`}
        onClick={() => onInclude(comp.key, weight)}
        className="text-rust border border-rust px-2"
      >
        INCLUDE
      </button>
      <span>
        {label} — ${comp.initial_ask.toFixed(0)}
        {" · "}
        {comp.distance_mi.toFixed(2)} mi
        {" · "}
        {comp.censored ? `${comp.effective_dom}d and counting (censored)` : `${comp.effective_dom} days`}
        {comp.removal_class ? ` · ${comp.removal_class}` : ""}
      </span>
    </li>
  );
}

function ResultsShell({ children }: { children: ReactNode }) {
  return (
    <section className="p-6">
      <h1 className="text-amber text-xl">Results</h1>
      {children}
    </section>
  );
}

/**
 * Whether the panels below are still the answer to the edit the user just
 * made. F5's success criterion is "no hidden state": numbers left standing
 * after a re-derive failed, or while one is in flight, are exactly that — the
 * screen looks settled and is not.
 */
function DeriveStatusLine({
  status,
  error,
}: {
  status: DeriveStatus;
  error: string | null;
}) {
  if (status === "error") {
    return (
      <p data-testid="derive-status" className="mt-2 text-xs text-rust">
        Re-derive failed — the numbers below are from before your last edit: {error}
      </p>
    );
  }
  if (status === "deriving") {
    return (
      <p data-testid="derive-status" className="mt-2 text-xs text-grey">
        Re-deriving...
      </p>
    );
  }
  return null;
}

/**
 * `data-testid="anchor"` is on the panel, not on the "Anchor:" caption: the
 * testid has to name the element whose text CHANGES when the evidence does,
 * or a spec asserting "the anchor on screen moved" would be reading a constant
 * label and passing for the wrong reason.
 */
function AnchorPanel({ anchor }: { anchor: DerivedState["anchor"] }) {
  if (!anchor) {
    return (
      <p data-testid="anchor" className="mt-4 text-sm text-grey">
        No anchor — no selected comp has a $/sqft.
      </p>
    );
  }
  return (
    <div data-testid="anchor" className="mt-4 text-sm">
      <span className="text-white">Anchor: </span>
      <span>
        ${anchor.rent.mid.toFixed(0)}/mo (${anchor.psf.mid.toFixed(2)}/sqft) — band ${anchor.rent.low.toFixed(0)}
        {" – "}${anchor.rent.high.toFixed(0)}, drift {anchor.drift_pct}% · {anchor.n_comps} comps
      </span>
    </div>
  );
}

interface CompListProps {
  comps: DerivedComp[];
  weights: Record<string, number>;
  onWeightChange: (key: string, weight: number) => void;
  onSelectAll: () => void;
  onSelectNone: () => void;
  /**
   * True while a re-derive is in flight. ALL/NONE act on the *visible* set,
   * and "visible" is a server label that a request in flight is about to
   * change — sweeping against the previous answer would write weights over
   * comps the user can no longer see, which is the hidden state F5-S2 and
   * F7-S1 both forbid. Disabled rather than queued: there is nothing
   * meaningful to queue, and the wait is one debounce.
   */
  bulkDisabled: boolean;
  includedCount: number;
}

function CompList({
  comps,
  weights,
  onWeightChange,
  onSelectAll,
  onSelectNone,
  bulkDisabled,
  includedCount,
}: CompListProps) {
  return (
    <div className="mt-4">
      <div className="flex items-center gap-4">
        <h2 className="text-white text-sm font-bold">
          Comps — {includedCount} included of {comps.length} shown
        </h2>
        <button
          type="button"
          data-testid="select-all"
          onClick={onSelectAll}
          disabled={bulkDisabled}
          className="text-xs text-green border border-green px-2 py-0.5 disabled:opacity-40"
        >
          ALL
        </button>
        <button
          type="button"
          data-testid="select-none"
          onClick={onSelectNone}
          disabled={bulkDisabled}
          className="text-xs text-grey border border-grey px-2 py-0.5 disabled:opacity-40"
        >
          NONE
        </button>
      </div>
      <ul>
        {comps.map((comp) => (
          <CompRow
            key={comp.key}
            comp={comp}
            // The weight the user chose, else the one the server derived for
            // them. One value drives both controls below — see INCLUDED_WEIGHT.
            weight={weights[comp.key] ?? comp.weight}
            onWeightChange={onWeightChange}
          />
        ))}
      </ul>
    </div>
  );
}

interface CompRowProps {
  comp: DerivedComp;
  weight: number;
  onWeightChange: (key: string, weight: number) => void;
}

function CompRow({ comp, weight, onWeightChange }: CompRowProps) {
  const included = weight > 0;
  const label = `${comp.address}${comp.unit ? ` #${comp.unit}` : ""}`;

  return (
    <li
      data-testid="comp-row"
      data-comp-key={comp.key}
      className="text-sm text-grey py-0.5 flex items-baseline gap-2"
    >
      <input
        type="checkbox"
        data-testid="comp-include-toggle"
        aria-label={`Include ${label}`}
        checked={included}
        onChange={() => onWeightChange(comp.key, included ? 0 : INCLUDED_WEIGHT)}
      />
      <input
        type="number"
        data-testid="comp-weight"
        aria-label={`Weight for ${label}`}
        min={0}
        step={0.25}
        value={String(weight)}
        onChange={(event) => {
          const raw = event.target.value;
          // An emptied box reads as 0, which is exactly "not selected" — the
          // same state the checkbox writes, because there is only one state.
          const parsed = raw === "" ? 0 : Number(raw);
          if (!Number.isFinite(parsed) || parsed < 0) return;
          onWeightChange(comp.key, parsed);
        }}
        className="w-16 bg-surface border border-grey text-white px-1"
      />
      <ContributionCell share={comp.contribution_share} />
      <span>
        {label} — ${comp.initial_ask.toFixed(0)}
        {" · "}
        premium {comp.premium === null ? "—" : `${(comp.premium * 100).toFixed(1)}%`}
        {" · "}
        cohort {comp.cohort_year}
        {" · "}
        {comp.censored ? `${comp.effective_dom}d and counting (censored)` : `${comp.effective_dom} days`}
        {comp.removal_class ? ` · ${comp.removal_class}` : ""}
      </span>
    </li>
  );
}

/**
 * The server's `contribution_share`, rendered. Never recomputed here: making
 * this a local `weight / total` would be a D5/D13 violation and would give a
 * second implementation of a formula that already exists in Python.
 */
function ContributionCell({ share }: { share: number | null }) {
  const dominant = share !== null && share > CONTRIBUTION_WARNING_SHARE;
  return (
    <span
      data-testid="comp-contribution"
      title={dominant ? "This comp dominates the analysis" : undefined}
      className={dominant ? "text-amber font-bold w-16" : "text-grey w-16"}
    >
      {share === null ? "—" : `${(share * 100).toFixed(1)}%`}
    </span>
  );
}

function BucketTable({ buckets }: { buckets: DerivedState["buckets"] }) {
  return (
    <div className="mt-4">
      <h2 className="text-white text-sm font-bold">Buckets</h2>
      <table className="text-sm text-grey">
        <thead>
          <tr>
            <th className="pr-4 text-left">Bucket</th>
            <th className="pr-4 text-left">Count</th>
            <th className="pr-4 text-left">Leased DOM (median)</th>
            <th className="pr-4 text-left">Cut-before-lease rate</th>
          </tr>
        </thead>
        <tbody>
          {buckets.map((bucket) => (
            <tr key={bucket.id}>
              <td className="pr-4">{bucket.id}</td>
              <td className="pr-4">{bucket.count}</td>
              <td className="pr-4">
                {bucket.leased_dom_median === null ? "—" : `${bucket.leased_dom_median} days`}
              </td>
              <td className="pr-4">
                {bucket.cut_before_lease_rate === null
                  ? "—"
                  : `${(bucket.cut_before_lease_rate * 100).toFixed(0)}%`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PriceTestPanel({ priceTest }: { priceTest: DerivedState["price_test"] }) {
  if (!priceTest) {
    return <p className="mt-4 text-sm text-grey">No price test — no candidate rent supplied.</p>;
  }

  if (priceTest.state === "insufficient_evidence") {
    return (
      <div data-testid="price-test-guard" className="mt-4 text-sm text-rust">
        <h2 className="text-white text-sm font-bold">Price test: insufficient evidence</h2>
        <p>
          Candidate ${priceTest.candidate_rent.toFixed(0)}/mo ({(priceTest.candidate_premium.mid * 100).toFixed(1)}%
          premium, {priceTest.bucket} bucket) — reason: {priceTest.reason}. {priceTest.neighbors.length} nearest
          comp(s) surfaced.
        </p>
      </div>
    );
  }

  // CurveResult — out of scope for WS-1 (F11-S2), but rendered honestly if
  // the pipeline ever produces one.
  return (
    <div data-testid="price-test-curve" className="mt-4 text-sm text-grey">
      <h2 className="text-white text-sm font-bold">Price test: curve</h2>
      <p>Candidate ${priceTest.candidate_rent.toFixed(0)}/mo, {priceTest.bucket} bucket.</p>
    </div>
  );
}
