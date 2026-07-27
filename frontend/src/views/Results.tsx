import { useEffect, useState } from "react";
import type { components } from "../api/schema";

type DerivedState = components["schemas"]["DerivedState"];
type DeriveRequest = components["schemas"]["DeriveRequest"];

/**
 * WS-1 walking skeleton (QUEUE.md row 6) — the one hardcoded search, fired
 * on mount. No selection/weight UI, no map, no form (out of scope): this
 * view exists to prove the real pull -> stitch -> anchor -> bucket ->
 * price-test chain end to end with a real browser round trip.
 *
 * D5: this component computes NOTHING. Every number below is read straight
 * off `DerivedState` — no arithmetic, no aggregation, no formula. Formatting
 * (rounding for display, D14) is the only thing done here.
 */
const HARDCODED_REQUEST: DeriveRequest = {
  pull_ref: "ws1-real",
  subject: {
    address: "3651 S Wood St, Chicago, IL 60609",
    lat: 41.83,
    lng: -87.665,
    sqft: 1200,
    beds: 4,
    baths: 2,
  },
  candidate_rent: 4500,
};

type FetchState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "ready"; data: DerivedState };

export default function Results() {
  const [state, setState] = useState<FetchState>({ status: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/derive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(HARDCODED_REQUEST),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`POST /api/derive returned ${res.status}: ${await res.text()}`);
        }
        return (await res.json()) as DerivedState;
      })
      .then((data) => setState({ status: "ready", data }))
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setState({ status: "error", message: err instanceof Error ? err.message : String(err) });
      });
    return () => controller.abort();
  }, []);

  if (state.status === "loading") {
    return (
      <section className="p-6">
        <h1 className="text-amber text-xl">Results</h1>
        <p className="mt-2 text-sm text-grey">Deriving...</p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="p-6">
        <h1 className="text-amber text-xl">Results</h1>
        <p className="mt-2 text-sm text-rust">Derive failed: {state.message}</p>
      </section>
    );
  }

  const { data } = state;

  return (
    <section className="p-6">
      <h1 className="text-amber text-xl">Results</h1>
      <p className="mt-1 text-xs text-grey">
        {data.meta.pull_ref} · as of {data.meta.as_of} · {data.breakdown.pulled} comps pulled
      </p>

      <AnchorPanel anchor={data.anchor} />
      <CompList comps={data.comps} />
      <BucketTable buckets={data.buckets} />
      <PriceTestPanel priceTest={data.price_test} />
    </section>
  );
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

function CompList({ comps }: { comps: DerivedState["comps"] }) {
  return (
    <div className="mt-4">
      <h2 className="text-white text-sm font-bold">Comps ({comps.length})</h2>
      <ul>
        {comps.map((comp) => (
          <li key={comp.key} data-testid="comp-row" className="text-sm text-grey py-0.5">
            {comp.address}
            {comp.unit ? ` #${comp.unit}` : ""} — ${comp.initial_ask.toFixed(0)}
            {" · "}
            premium {comp.premium === null ? "—" : `${(comp.premium * 100).toFixed(1)}%`}
            {" · "}
            cohort {comp.cohort_year}
            {" · "}
            {comp.censored ? `${comp.effective_dom}d and counting (censored)` : `${comp.effective_dom} days`}
            {comp.removal_class ? ` · ${comp.removal_class}` : ""}
          </li>
        ))}
      </ul>
    </div>
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
