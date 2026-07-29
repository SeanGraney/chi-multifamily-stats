import { useState } from "react";
import type { components } from "../api/schema";
import { useDerive, type DeriveStatus } from "../api/useDerive";

type DerivedState = components["schemas"]["DerivedState"];
type DeriveRequest = components["schemas"]["DeriveRequest"];
type DerivedComp = components["schemas"]["DerivedComp"];

/**
 * WS-1 walking skeleton (QUEUE.md row 6) — the one hardcoded search. No map,
 * no search form (out of scope). F5-S2 adds the curation half: per-row
 * include/weight controls, ALL/NONE, and the contribution %.
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

export default function Results() {
  /**
   * Curation state, client-owned (D13). Sparse on purpose: a comp the client
   * has said nothing about is absent here and the server applies the
   * defaulting rule (1.0, or 0.0 with no sqft), which is what lets comps that
   * are new to a refreshed pull arrive included without the client having
   * heard of them (F13-S1).
   */
  const [weights, setWeights] = useState<Record<string, number>>({});

  const { derived, status, error } = useDerive({
    pull_ref: PULL_REF,
    subject: SUBJECT,
    weights,
    candidate_rent: CANDIDATE_RENT,
  });

  /**
   * "Currently visible" = everything a filter has not taken. ALL/NONE operate
   * over exactly this set (F5-S2 AC), so a comp the user cannot see keeps the
   * weight they last chose for it and reappears at that weight when the
   * filter clears (F7-S1).
   */
  const visible = derived ? derived.comps.filter((comp) => comp.state !== "filtered") : [];

  const setWeight = (key: string, weight: number) =>
    setWeights((prev) => ({ ...prev, [key]: weight }));

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

  if (error && !derived) {
    return (
      <section className="p-6">
        <h1 className="text-amber text-xl">Results</h1>
        <p className="mt-2 text-sm text-rust">Derive failed: {error}</p>
      </section>
    );
  }

  if (!derived) {
    return (
      <section className="p-6">
        <h1 className="text-amber text-xl">Results</h1>
        <p className="mt-2 text-sm text-grey">Deriving...</p>
      </section>
    );
  }

  return (
    <section className="p-6">
      <h1 className="text-amber text-xl">Results</h1>
      <p className="mt-1 text-xs text-grey">
        {derived.meta.pull_ref} · as of {derived.meta.as_of} · {derived.breakdown.pulled} comps
        pulled
      </p>

      <DeriveStatusLine status={status} error={error} />
      <AnchorPanel anchor={derived.anchor} />
      <CompList
        comps={derived.comps}
        weights={weights}
        onWeightChange={setWeight}
        onSelectAll={selectAll}
        onSelectNone={selectNone}
        includedCount={derived.breakdown.included}
      />
      <BucketTable buckets={derived.buckets} />
      <PriceTestPanel priceTest={derived.price_test} />
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

function AnchorPanel({ anchor }: { anchor: DerivedState["anchor"] }) {
  if (!anchor) {
    return (
      <p className="mt-4 text-sm text-grey">
        No anchor — no selected comp has a $/sqft.
      </p>
    );
  }
  return (
    <div className="mt-4 text-sm">
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
  includedCount: number;
}

function CompList({
  comps,
  weights,
  onWeightChange,
  onSelectAll,
  onSelectNone,
  includedCount,
}: CompListProps) {
  return (
    <div className="mt-4">
      <div className="flex items-center gap-4">
        <h2 className="text-white text-sm font-bold">
          Comps ({includedCount} of {comps.length} included)
        </h2>
        <button
          type="button"
          data-testid="select-all"
          onClick={onSelectAll}
          className="text-xs text-green border border-green px-2 py-0.5"
        >
          ALL
        </button>
        <button
          type="button"
          data-testid="select-none"
          onClick={onSelectNone}
          className="text-xs text-grey border border-grey px-2 py-0.5"
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
