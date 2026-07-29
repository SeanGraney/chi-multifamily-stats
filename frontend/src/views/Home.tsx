import { useState } from "react";
import type { components } from "../api/schema";
import SearchForm from "./search/SearchForm";
import type { SearchValues } from "./search/searchValues";

type SearchResult = components["schemas"]["SearchResult"];

/**
 * Home (spec §6.2) — NEW SEARCH, and what the last one produced.
 *
 * The recents table §6.2 also describes is F1-S1's story and is deliberately
 * absent: it reads `GET /api/workspaces`, which does not exist yet.
 *
 * The form REPLACES this view while it is open rather than floating over it.
 * §6.3 calls it a modal and visually this is a plainer thing, but the property
 * that matters is the modal one — while the form is up, nothing behind it is
 * reachable or clickable, so there is no way to start a second search on top of
 * the one being filled in. (Deadline reading of spec §8: working beats pretty.)
 */
export default function Home() {
  const [formOpen, setFormOpen] = useState(false);
  const [outcome, setOutcome] = useState<{ result: SearchResult; address: string } | null>(null);

  if (formOpen) {
    return (
      <SearchForm
        onCancel={() => setFormOpen(false)}
        onSubmitted={(result: SearchResult, values: SearchValues) => {
          setOutcome({ result, address: values.address.trim() });
          setFormOpen(false);
        }}
      />
    );
  }

  return (
    <section className="p-6">
      <h1 className="text-amber text-xl">RentComp</h1>
      <p className="mt-2 text-sm text-grey">
        Price a unit from real comparable listings. Start with a search.
      </p>

      <button
        type="button"
        data-testid="nav-new-search"
        onClick={() => setFormOpen(true)}
        className="mt-4 border border-green px-4 py-2 text-sm text-green"
      >
        NEW SEARCH
      </button>

      {outcome && <SearchOutcome result={outcome.result} address={outcome.address} />}
    </section>
  );
}

/**
 * What the pull came back with — and, when it is not whole, what is absent.
 *
 * ARCHITECTURE §5a is explicit that an incomplete first pull is *usable*, but
 * only "with the gap named loudly": with a 50-call cap, refusing to show
 * anything until the set is perfect would strand the user, and showing it
 * silently would let a missing cohort skew every number downstream with nothing
 * on screen saying so. Spec §7 adds the zero-comp case — suggest widening,
 * rather than leaving the user on an empty view wondering whether the tool
 * broke or the market is thin.
 *
 * Every number here is read off `SearchResult`; nothing is recomputed (D5).
 */
function SearchOutcome({ result, address }: { result: SearchResult; address: string }) {
  return (
    <div data-testid="search-outcome" className="mt-6 border border-grey p-3">
      <h2 className="text-white text-sm font-bold">Last search</h2>
      <p className="mt-1 text-xs text-grey">
        {address} · cache {result.cache_status} · {result.calls_spent} call
        {result.calls_spent === 1 ? "" : "s"} spent of {result.estimated_calls} estimated · ref{" "}
        {result.pull_ref}
      </p>

      {result.complete ? (
        <p className="mt-2 text-sm text-green">Evidence complete — every planned window arrived.</p>
      ) : (
        <div data-testid="pull-gap" role="alert" className="mt-2 text-sm text-rust">
          <p>
            Evidence incomplete: {result.missing.length > 0 ? result.missing.join(", ") : "no window"}{" "}
            missing. {result.calls_to_complete} call
            {result.calls_to_complete === 1 ? "" : "s"} would complete it.
          </p>
          <p className="mt-1 text-xs text-grey">
            Nothing usable came back for these constraints. Try widening the radius, the date
            window, or the property types — or check the address is one RentCast can place.
          </p>
        </div>
      )}
    </div>
  );
}
