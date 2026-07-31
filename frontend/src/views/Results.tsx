import { useState, type ReactNode } from "react";
import type { components } from "../api/schema";
import { postJson } from "../api/http";
import { useDerive, type DeriveStatus } from "../api/useDerive";
import type { OpenPull } from "../pull";
import { PROPERTY_TYPES, toRequestBody, type SearchValues } from "./search/searchValues";

type DerivedState = components["schemas"]["DerivedState"];
type DeriveRequest = components["schemas"]["DeriveRequest"];
type DerivedComp = components["schemas"]["DerivedComp"];
type Filters = components["schemas"]["Filters"];
type SearchResult = components["schemas"]["SearchResult"];
type PartialPullInfo = components["schemas"]["PartialPullInfo"];

/**
 * The Results (COMPS) view. F5-S2 added the curation half: per-row
 * include/weight controls, ALL/NONE, and the contribution %. F7-S1/F7-S2 added
 * the filter half: the strip that sets `filters`, the "N filtered" footer that
 * keeps the hidden evidence reachable, and the per-row INCLUDE override. F4-S6
 * adds the three states that are *not* a comp list — no pull open, a pull that
 * found nothing, and a pull with a hole in it.
 *
 * WHERE THE EVIDENCE COMES FROM — F4-S6 AC8, and the defect it closes
 * -------------------------------------------------------------------
 * This view used to open `const PULL_REF = "ws1-real"` and derive it on mount,
 * unconditionally. That made every panel below an analysis of a committed
 * fixture no matter what the user had searched, and nothing on screen said so.
 * The ref now arrives as a prop and there is **no default** — `pull === null`
 * renders an explanation instead of numbers. The distinction matters: a prop
 * with a fallback would still paint a stranger's evidence on first paint, which
 * is the same defect with a longer call chain.
 *
 * Two consequences worth stating because they look like omissions:
 *
 * * nothing in this file spells a pull ref, and nothing may (structurally
 *   pinned by `f4s6-derive-targets-the-users-pull.spec.ts` AC8d); and
 * * a payload whose `meta.pull_ref` is not the open pull's is treated as
 *   *not yet derived*, not as "close enough". `useDerive` keeps the previous
 *   answer on screen while a new one is in flight, which is right for a weight
 *   change (same evidence, better number) and wrong for a ref change (one
 *   address's comps under another address's search). See `payloadForThisPull`.
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
/**
 * ⚠ STILL HARDCODED, AND DELIBERATELY LEFT SO — DISCLOSED TO THE PM (F4-S6).
 *
 * AC8 closes the *evidence* seam: the comps on screen are now the comps the
 * user's own search bought. The **subject** seam is the same shape and is still
 * open, because it cannot be closed from here without breaking D5.
 *
 * `DeriveRequest.subject` needs `lat`/`lng`, and **no response on the wire
 * carries them**: RentCast geocodes the address inside the pull, and neither
 * `SearchResult` nor `SearchPlan` reports the point the search was centred on.
 * So `comp.distance_mi` — and therefore the distance filter — is measured from
 * this fixture address whatever the user searched. `sqft` is the anchor's
 * multiplier and is available (the form collects it), but threading only that
 * through would leave the subject half real and half fixture, which is harder
 * to notice than a subject that is uniformly the fixture's.
 *
 * The fix is a backend datum (the geocoded centre, reported by the pull), not
 * a geocoder in TypeScript. Reported as a finding rather than guessed at.
 */
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

export interface ResultsProps {
  /**
   * The evidence the user opened, or `null` when they have not opened any.
   * There is no default and no fallback — see the module docstring and
   * `pull.ts`.
   */
  pull: OpenPull | null;
  /** Re-open the search form with a loosened constraint (F4-S6 AC3). */
  onWiden: (values: SearchValues) => void;
}

export default function Results({ pull, onWiden }: ResultsProps) {
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

  /**
   * Bumped when a resume completes the pull (F4-S6 AC6). A resume adds the
   * missing window *under the existing ref*, so the derive request body is
   * byte-identical before and after and nothing else would re-issue it — the
   * user would have finished the pull and still be reading the numbers computed
   * without it.
   */
  const [evidenceEpoch, setEvidenceEpoch] = useState(0);

  /**
   * The whole of AC8, in one expression: with no pull open there is no request,
   * so there is nothing for a default ref to be defaulted *to*. `useDerive`
   * treats `null` as "issue nothing" rather than as "wait for a value".
   */
  const { derived, status, error } = useDerive(
    pull === null
      ? null
      : {
          pull_ref: pull.ref,
          subject: SUBJECT,
          weights,
          include_overrides: includeOverrides,
          filters: toFilters(strip),
          candidate_rent: CANDIDATE_RENT,
        },
    evidenceEpoch,
  );

  /**
   * The last payload, but only if it is about the pull that is currently open.
   *
   * `useDerive` deliberately keeps the previous answer standing while a new
   * request is in flight — that is what stops the screen flashing empty on every
   * slider move. Across a *ref* change that same behaviour would leave one
   * search's comps, anchor and buckets on screen under another search's
   * identity, which is the AC8 defect re-created by timing instead of by a
   * constant. Cheap to rule out, because the payload names its own pull.
   */
  const payloadForThisPull =
    pull !== null && derived !== null && derived.meta.pull_ref === pull.ref ? derived : null;

  /**
   * "Currently visible" = everything a filter has not taken. ALL/NONE operate
   * over exactly this set (F5-S2 AC), so a comp the user cannot see keeps the
   * weight they last chose for it and reappears at that weight when the
   * filter clears (F7-S1).
   *
   * Both halves read `comp.state`, which the SERVER decided. Nothing here
   * re-evaluates a filter predicate (D5/D13).
   */
  const visible = payloadForThisPull
    ? payloadForThisPull.comps.filter((comp) => comp.state !== "filtered")
    : [];
  const filteredOut = payloadForThisPull
    ? payloadForThisPull.comps.filter((comp) => comp.state === "filtered")
    : [];

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

  /**
   * F4-S6 AC8b, the load-bearing half. No pull open ⇒ no request was issued and
   * there is nothing to render but the way in. Note what is NOT here: no
   * "loading", no skeleton panels, no anchor. An anchor is a claim about a
   * market, and there is no evidence behind this one.
   */
  if (pull === null) {
    return (
      <ResultsShell>
        <p data-testid="no-pull-open" className="mt-2 text-sm text-grey">
          No pull is open, so there is nothing to analyse. Start a search from Home — the
          evidence you pull there is what this view derives from.
        </p>
      </ResultsShell>
    );
  }

  // Before the first payload for THIS pull there is nothing to render but the
  // reason why — plus whatever the search itself already said about the gap,
  // which is the only honest thing on screen when the evidence will not derive
  // at all (§5a: an incomplete pull is usable only with the gap named loudly).
  if (!payloadForThisPull) {
    return (
      <ResultsShell>
        <p
          role={error ? "alert" : undefined}
          className={error ? "mt-2 text-sm text-rust" : "mt-2 text-sm text-grey"}
        >
          {error ? `Derive failed: ${error}` : "Deriving..."}
        </p>
        {error && pull.outcome && !pull.outcome.complete && (
          <SearchGapNotice outcome={pull.outcome} />
        )}
      </ResultsShell>
    );
  }

  const partial = payloadForThisPull.meta.partial_pull;

  /**
   * F4 epic edge: "0 comps → empty state naming the binding constraint +
   * widen shortcuts."
   *
   * Rendered INSTEAD of the panels, not above them. That is the same render
   * exclusivity rule F11's guard state follows, and for the same reason: a
   * three-row bucket table full of dashes sitting under an empty state is two
   * answers to one question, and the dashes read as "the data came back empty"
   * rather than as "this search matched nothing".
   */
  if (payloadForThisPull.comps.length === 0) {
    return (
      <ResultsShell>
        <PullIdentityLine derived={payloadForThisPull} />
        {partial && <PartialPullBanner partial={partial} pull={pull} onResumed={() => setEvidenceEpoch((n) => n + 1)} />}
        <EmptyState
          search={pull.search}
          droppedOutsideWindow={payloadForThisPull.breakdown.dropped_outside_window ?? null}
          hasGap={partial !== null && partial !== undefined}
          onWiden={onWiden}
        />
      </ResultsShell>
    );
  }

  return (
    <ResultsShell>
      <PullIdentityLine derived={payloadForThisPull} />

      {partial && (
        <PartialPullBanner
          partial={partial}
          pull={pull}
          onResumed={() => setEvidenceEpoch((n) => n + 1)}
        />
      )}
      <DeriveStatusLine status={status} error={error} />
      <FilterStrip strip={strip} onChange={setStrip} onReset={resetFilters} />
      <AnchorPanel anchor={payloadForThisPull.anchor} />
      <CompList
        comps={visible}
        weights={weights}
        onWeightChange={setWeight}
        onSelectAll={selectAll}
        onSelectNone={selectNone}
        bulkDisabled={status === "deriving"}
        includedCount={payloadForThisPull.breakdown.included}
      />
      <FilteredFooter
        comps={filteredOut}
        count={payloadForThisPull.breakdown.filtered}
        weights={weights}
        onInclude={includeOverride}
      />
      <BucketTable buckets={payloadForThisPull.buckets} />
      <PriceTestPanel priceTest={payloadForThisPull.price_test} />
    </ResultsShell>
  );
}

function PullIdentityLine({ derived }: { derived: DerivedState }) {
  return (
    <p className="mt-1 text-xs text-grey">
      {derived.meta.pull_ref} · as of {derived.meta.as_of} · {derived.breakdown.pulled} comps
      pulled
    </p>
  );
}

/**
 * What the *search* said was missing, for the one state where there is no
 * derived payload to say it better.
 *
 * Kept deliberately separate from `PartialPullBanner` rather than merged into
 * one "gap" component with two data sources. They describe different things:
 * this one describes a pull that would not derive at all, and the banner
 * describes the evidence a comp list on screen was actually computed from. A
 * single component fed from whichever source happened to be available would
 * blur exactly that distinction.
 */
function SearchGapNotice({ outcome }: { outcome: SearchResult }) {
  return (
    <div data-testid="pull-gap" role="alert" className="mt-2 text-sm text-rust">
      <p>
        This search is short {outcome.missing.length > 0 ? outcome.missing.join(", ") : "a window"}
        , and nothing it did hold could be derived.
      </p>
      <p className="mt-1 text-xs text-grey">
        That is a gap in the evidence, not a verdict on the market — do not read it as "no comps
        here". Completing the pull is the fix; widening the search is not.
      </p>
    </div>
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
  /**
   * `breakdown.filtered`, not `comps.length`. The two are equal by F7-S1's
   * reconciliation invariant, which is exactly why the server's number is the
   * one to render: if they ever disagreed, the honest thing is for the screen
   * to say what the derivation says, not to quietly paper over it with a count
   * of the rows this component happens to have been handed.
   */
  count: number;
  weights: Record<string, number>;
  onInclude: (key: string, weight: number) => void;
}

/**
 * The "N filtered" footer (F7-S2). The comps here left the list and every
 * calculation, but they did not leave the evidence: this is where they stay
 * reachable, and each one carries the INCLUDE that puts it back.
 *
 * The disclosure starts COLLAPSED, per F7-S2: the footer's resting state is
 * the `"N filtered · show"` line, and the rows (with their per-row INCLUDE)
 * appear on the first click of the toggle. The count is always visible even
 * when closed, so the filtered set is never silently absent from the screen —
 * that visibility, not an open list, is what keeps the filtered comps honest.
 */
function FilteredFooter({ comps, count, weights, onInclude }: FilteredFooterProps) {
  const [open, setOpen] = useState(false);

  if (comps.length === 0) return null;

  return (
    <div data-testid="filtered-footer" className="mt-4 border-t border-grey pt-2">
      <p className="text-xs text-rust flex items-center gap-2">
        <span>{count} filtered</span>
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

/**
 * The partial-pull state (F4-S6 AC4/AC5/AC6; D24 §5a).
 *
 * "A missing cohort skews every downstream number and must never be silently
 * absent" — so the workspace stays fully usable and the gap is named *next to*
 * the numbers it affects, rather than the evidence being withheld until the set
 * is perfect. With a 50-call monthly cap, refusing to render an incomplete pull
 * would strand the user; rendering it silently would let a hole in the evidence
 * move every statistic with nothing on screen saying so.
 *
 * NAMED, NOT COUNTED. The window labels are the manifest's own strings
 * (`client/pull.py::_label` → `"2019 Inactive"`), rendered verbatim. A view that
 * rebuilt "2019 Inactive" from a year plus a status would be a second
 * implementation of a naming convention it does not own, and it would disagree
 * with the manifest on exactly the windows nobody checks.
 *
 * ⚠ WHAT IS DELIBERATELY NOT RENDERED HERE: `partial.calls_to_complete`.
 * The story's example copy is "2025 inactive missing · **1 call to complete**",
 * and the datum is on the wire and correct as far as the manifest knows — but
 * the quote-vs-spend defect behind it is still open (QUEUE row 13j), so a number
 * printed here would be a *cost promise* to the owner that the system cannot yet
 * stand behind. Naming the windows is honest and actionable on its own; quoting
 * a price we know can be wrong is worse than quoting none. Held out by PM ruling
 * at dispatch, not by oversight — see the handoff note.
 */
function PartialPullBanner({
  partial,
  pull,
  onResumed,
}: {
  partial: PartialPullInfo;
  pull: OpenPull;
  onResumed: () => void;
}) {
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);

  /**
   * AC6: finish the pull, do not re-buy it.
   *
   * `resume` and `force_refresh` are different consents and stay different
   * fields on the wire. `force_refresh` is F3-S2's REFRESH click and re-fetches
   * every window (`client/pull.py`: `known = {} if force_refresh`), so wiring
   * this button to it would bill a one-window gap as a full re-pull. The
   * orchestrator already resumes correctly; `resume: true` is the request that
   * asks it to.
   */
  const resume = async () => {
    setResuming(true);
    setResumeError(null);
    try {
      await postJson<SearchResult>("/api/search", {
        ...toRequestBody(pull.search),
        resume: true,
      });
      // The ref is unchanged and the evidence behind it is not, so the derive
      // has to be re-issued explicitly — see `useDerive`'s `epoch`.
      onResumed();
    } catch (err: unknown) {
      setResumeError(err instanceof Error ? err.message : String(err));
    } finally {
      setResuming(false);
    }
  };

  return (
    <div data-testid="partial-pull" role="alert" className="mt-3 border border-rust p-2 text-sm text-rust">
      <p>
        <span className="font-bold">Evidence incomplete</span> — {partial.missing.join(" · ")}{" "}
        missing.
      </p>
      <p className="mt-1 text-xs text-grey">
        Every number below is computed without {partial.missing.length === 1 ? "that window" : "those windows"}. The
        comps that did arrive are real and usable; the totals are not the whole market.
      </p>
      <button
        type="button"
        data-testid="resume-pull"
        onClick={resume}
        disabled={resuming}
        className="mt-2 border border-rust px-2 py-0.5 text-xs text-rust disabled:opacity-40"
      >
        {resuming ? "Resuming…" : "Resume pull"}
      </button>
      {resumeError && (
        <p data-testid="resume-error" className="mt-1 text-xs text-rust">
          Resume failed: {resumeError}
        </p>
      )}
    </div>
  );
}

/**
 * F4's 0-comp edge: "empty state naming the binding constraint + widen
 * shortcuts."
 *
 * WHICH CONSTRAINT BOUND IS A SERVER FACT, NOT A GUESS (D5)
 * ---------------------------------------------------------
 * Two searches can both end at zero comps and want opposite actions from the
 * user, and `breakdown.pulled == 0` cannot tell them apart:
 *
 *   * the source returned nothing for these constraints at all → the radius,
 *     the bed filter or the property types are what bound; widening the date
 *     window would spend calls and return the same nothing;
 *   * the source returned records and the **date window** dropped every one of
 *     them → the window is what bound, and widening the radius is the wrong
 *     move.
 *
 * `breakdown.dropped_outside_window` is what separates them. It is F4-S4's
 * `ShapingSummary.dropped_outside_window`, which the pipeline has computed since
 * F4-S4 and `shape_raw_pull` threw away; F4-S6 puts it on the wire, because an
 * empty state that "names the binding constraint" without it can only guess.
 * `null` means the pull was not shaped from raw records (a synthetic fixture),
 * in which case this says so by not claiming to know.
 *
 * The *values* echoed back are the user's own search, not anything derived —
 * that is what makes the message actionable rather than a restatement of "0".
 *
 * ON THE EPIC'S EXAMPLE COPY ("0 in radius; nearest match at 1.3mi"): the
 * nearest-match half is not purchasable. It needs a listing *outside* the radius
 * the pull paid to search, i.e. a second RentCast call against a 50-call monthly
 * cap, every time a search comes back empty. Naming the constraints actually
 * applied, with the counts already in hand, is the affordable and honest form of
 * the same sentence. Flagged to the PM rather than quietly dropped.
 */
function EmptyState({
  search,
  droppedOutsideWindow,
  hasGap,
  onWiden,
}: {
  search: SearchValues;
  droppedOutsideWindow: number | null;
  hasGap: boolean;
  onWiden: (values: SearchValues) => void;
}) {
  const windowBound = droppedOutsideWindow !== null && droppedOutsideWindow > 0;
  const types = search.propertyTypes.join(", ");

  return (
    <div data-testid="empty-state" className="mt-4 border border-amber p-3 text-sm">
      <h2 className="text-amber font-bold">No comps came back for this search</h2>

      <p data-testid="empty-state-constraint" className="mt-2 text-white">
        {windowBound ? (
          <>
            {droppedOutsideWindow} listing{droppedOutsideWindow === 1 ? " was" : "s were"} pulled
            within {search.radius} mi of {search.address.trim()}, and every one of them was first
            listed outside your {search.windowStart}–{search.windowEnd} date window. The{" "}
            <span className="text-amber">date window</span> is what is binding here — widening the
            radius would cost calls and change nothing.
          </>
        ) : (
          <>
            Nothing at all came back within{" "}
            <span className="text-amber">{search.radius} mi</span> of {search.address.trim()} for{" "}
            <span className="text-amber">{search.bedrooms} bed</span>
            {search.bathrooms.trim() ? ` · ${search.bathrooms.trim()} bath` : ""} ·{" "}
            <span className="text-amber">{types}</span>, across {search.yearsBack} cohort years in
            the {search.windowStart}–{search.windowEnd} window. The radius, the bed filter or the
            property types are what bound — the date window dropped nothing, because there was
            nothing to drop.
          </>
        )}
      </p>

      {hasGap && (
        <p className="mt-2 text-xs text-rust">
          This pull is also missing a window (above). Finish it before widening — the gap may be
          the whole reason this is empty, and completing it costs less than a new search.
        </p>
      )}

      <p className="mt-3 text-xs text-grey">
        Widening re-opens the search with one constraint loosened. Nothing is fetched until you
        submit it, so you see the call cost first.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <button
          type="button"
          data-testid="widen-radius"
          onClick={() => onWiden({ ...search, radius: String(widerRadius(search.radius)) })}
          className="border border-green px-2 py-0.5 text-xs text-green"
        >
          Widen radius to {widerRadius(search.radius)} mi
        </button>
        <button
          type="button"
          data-testid="widen-window"
          onClick={() => onWiden({ ...search, yearsBack: String(moreYears(search.yearsBack)) })}
          className="border border-green px-2 py-0.5 text-xs text-green"
        >
          Look back {moreYears(search.yearsBack)} years
        </button>
        <button
          type="button"
          data-testid="widen-types"
          onClick={() => onWiden({ ...search, propertyTypes: [...PROPERTY_TYPES] })}
          className="border border-green px-2 py-0.5 text-xs text-green"
        >
          Include every property type
        </button>
      </div>
    </div>
  );
}

/**
 * [DEFAULT] how much a widen widens. Doubling the radius is a suggestion the
 * user then edits in the form — it is not a derivation and nothing downstream
 * depends on the exact figure. Clamped to the `SearchParams.radius` bound (100
 * mi) so the shortcut can never propose a search the wire would refuse.
 */
function widerRadius(current: string): number {
  const value = Number(current.trim());
  const widened = Number.isFinite(value) && value > 0 ? value * 2 : 1;
  return Math.min(100, Math.round(widened * 100) / 100);
}

/** [DEFAULT] one more cohort year, clamped to §2.1's 1–5 range. */
function moreYears(current: string): number {
  const value = Number(current.trim());
  return Math.min(5, (Number.isInteger(value) && value >= 1 ? value : 2) + 1);
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
