import type { components } from "./api/schema";
import type { SearchValues } from "./views/search/searchValues";

type SearchResult = components["schemas"]["SearchResult"];

/**
 * The evidence the user currently has open — F4-S6 AC8, the search→Results seam.
 *
 * WHY THIS TYPE EXISTS INSTEAD OF A `pullRef` PROP
 * ------------------------------------------------
 * Through F7-S1 `views/Results.tsx` held `const PULL_REF = "ws1-real"` and that
 * fixture ref was the only thing that ever reached `POST /api/derive`. `Home`
 * ran a real search, received a real `SearchResult`, rendered its `pull_ref` as
 * *text*, and dropped it. So the owner could spend real API calls on a real
 * address, open Results, and be shown the analysis of a fixture — every panel
 * internally consistent, every number correctly computed, all of it about
 * evidence nobody asked for. That is the confident-output-the-evidence-does-not
 * -support failure `NORTH_STAR.md` exists to prevent, at the highest level this
 * product has, and it is F4 flow step 4 ("the system lands the user on Results")
 * verbatim.
 *
 * Threading the ref through as a prop **with a default** would not have fixed
 * it, and neither would remembering the last ref across a reload: both still let
 * Results paint numbers for a pull the user did not open. So the rule this type
 * encodes is stronger than "the ref is passed in":
 *
 *   **A pull ref may only ever arrive from a response at runtime, and no view
 *   owns a fallback for it.** `null` is a first-class state with its own render
 *   (see `Results`), not a hole to be filled with a default.
 *
 * That is why there is no `emptyPull()`, no default export of a constant, and
 * nothing in `frontend/src/` that spells a ref literally — pinned structurally
 * by `e2e/tests/f4s6-derive-targets-the-users-pull.spec.ts` AC8d, which walks
 * the whole source tree for `"ws1-*"` and 64-char-hex literals.
 *
 * THE TWO DOORS INTO RESULTS
 * ---------------------------
 * There are exactly two legitimate producers of an `OpenPull`, and this shape
 * is deliberately buildable by both:
 *
 * 1. **A search** (F2-S1/F4-S9, this story). `SearchForm` posts `/api/search`,
 *    and `ref` is the `pull_ref` off that response; `search` is what the user
 *    typed into the form.
 * 2. **A recents row** (F1-S1, QUEUE row 38 — not built yet). `GET
 *    /api/workspaces` already ships, and `Workspace.search` is a `SearchParams`
 *    carrying the same eight fields `SearchValues` holds as strings. F1-S1
 *    builds one of these from the row it opened and calls the *same*
 *    `onOpenPull` — no second mechanism, and nothing here assumes a search
 *    happened in this session.
 *
 * Whichever story lands second inherits the seam, so `App` owns this state and
 * both doors write it.
 */
export interface OpenPull {
  /**
   * The ref `POST /api/derive` resolves. Always `SearchResult.pull_ref` (or the
   * `pull_ref` a recents row opened) — never a literal, never a default.
   */
  readonly ref: string;

  /**
   * The constraints that bought this evidence, as the user expressed them.
   *
   * Carried alongside the ref rather than re-fetched because F4-S6's empty
   * state has to *name the binding constraint* — "nothing within 0.25 mi",
   * "every listing fell outside 06-15–06-30" — and those are the user's own
   * inputs echoed back, not anything derived (D5). It is also what the widen
   * shortcuts re-open the form with.
   *
   * `SearchValues` (the form's strings) rather than the wire `SearchParams`
   * on purpose: this is what the empty state prints and what the form re-opens
   * with, and round-tripping through the wire shape would lose `sqft`, which
   * `SearchParams` does not carry (it is a subject attribute, not a pull
   * parameter).
   */
  readonly search: SearchValues;

  /**
   * What `POST /api/search` said about this pull, or `null` when the pull was
   * opened without running one (a recents row, F1-S1).
   *
   * Deliberately NOT the source of the gap banner: `DerivedState.meta.
   * partial_pull` is, because the banner sits next to numbers and must describe
   * the evidence *those numbers were computed from*. The two can legitimately
   * differ — a resume can land between the search and the derive — and the one
   * a user is reading a comp list under is the derive's.
   *
   * What this is for is the state where there is no derived payload at all: a
   * pull so short of evidence that `POST /api/derive` 404s has still got
   * something true to say about itself ("2019 Inactive never arrived"), and
   * §5a's "the gap is named loudly" applies hardest exactly there. Without it
   * that screen degrades to a bare HTTP error.
   */
  readonly outcome: SearchResult | null;
}
