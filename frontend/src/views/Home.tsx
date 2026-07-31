import { useState } from "react";
import type { components } from "../api/schema";
import type { OpenPull } from "../pull";
import SearchForm from "./search/SearchForm";
import type { SearchValues } from "./search/searchValues";

type SearchResult = components["schemas"]["SearchResult"];

export interface HomeProps {
  /** F4-S6 AC8: the one way a search becomes the open pull. See `pull.ts`. */
  onOpenPull: (pull: OpenPull) => void;
  /** Values to open the form with — set by a widen shortcut (F4-S6 AC3). */
  prefill: SearchValues | null;
  onPrefillConsumed: () => void;
}

/**
 * Home (spec §6.2) — NEW SEARCH, and the way into a workspace.
 *
 * The recents table §6.2 also describes is F1-S1's story and is deliberately
 * absent. `GET /api/workspaces` now exists (F1-S2), so what F1-S1 still owes is
 * the table itself; when it lands, a row click calls the same `onOpenPull` this
 * form does and needs no new mechanism (see `pull.ts`, "the two doors").
 *
 * The form REPLACES this view while it is open rather than floating over it.
 * §6.3 calls it a modal and visually this is a plainer thing, but the property
 * that matters is the modal one — while the form is up, nothing behind it is
 * reachable or clickable, so there is no way to start a second search on top of
 * the one being filled in. (Deadline reading of spec §8: working beats pretty.)
 *
 * WHAT USED TO BE HERE, AND WHY IT IS GONE (F4-S6)
 * ------------------------------------------------
 * A `SearchOutcome` panel rendered the finished search's `pull_ref` as text,
 * along with its cache status, call count and gap. Two things were wrong with
 * it and only one of them was cosmetic:
 *
 * 1. **It was the end of the road for the ref.** Nothing carried it into
 *    Results, which derived a hardcoded fixture instead — the AC8 defect. A
 *    search now hands its ref straight to `onOpenPull`, so the evidence the user
 *    paid for is the evidence they are shown.
 * 2. **It conflated "incomplete" with "empty".** Inside the `!result.complete`
 *    branch it printed both the gap *and* "Nothing usable came back for these
 *    constraints. Try widening the radius, the date window, or the property
 *    types…". But `SearchResult` carries no comp count — `complete` is about
 *    which *windows arrived*, not about whether the pull found anything — so a
 *    pull that was one window short and returned twelve perfectly good comps
 *    told the user nothing came back and sent them off widening a search that
 *    was working. The two states have opposite remedies (finish the pull for one
 *    call vs. change a constraint and pay for a whole new one), and they are now
 *    two separate renders on Results, each reached from evidence that can
 *    actually tell them apart: `meta.partial_pull` for the gap, `comps.length`
 *    for the empty state.
 */
export default function Home({ onOpenPull, prefill, onPrefillConsumed }: HomeProps) {
  const [formOpen, setFormOpen] = useState(false);

  // A widen shortcut re-opens the form with the loosened search already in it
  // (F4-S6 AC3). It is a request to *edit* a search, not to run one: the cost
  // preview is attached to the form, and F3's "no API call ever happens without
  // the user having chosen it" means the second search is still theirs to
  // submit.
  const showForm = formOpen || prefill !== null;

  if (showForm) {
    return (
      <SearchForm
        initialValues={prefill ?? undefined}
        onCancel={() => {
          setFormOpen(false);
          onPrefillConsumed();
        }}
        onSubmitted={(result: SearchResult, values: SearchValues) => {
          setFormOpen(false);
          onPrefillConsumed();
          // The whole point of this story: the ref the user just paid for is
          // the ref the next screen derives (F4 flow step 4).
          onOpenPull({ ref: result.pull_ref, search: values, outcome: result });
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
    </section>
  );
}
