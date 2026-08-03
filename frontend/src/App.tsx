import { useState } from "react";
import TopBar, { type ViewName } from "./components/TopBar";
import Home from "./views/Home";
import Results from "./views/Results";
import Analysis from "./views/Analysis";
import Settings from "./views/Settings";
import type { OpenPull } from "./pull";
import type { SearchValues } from "./views/search/searchValues";

/**
 * Three-view routing via plain React state (D13's precedent — no
 * react-router-dom, see TopBar.tsx for the rationale). One state variable
 * is the whole "router."
 *
 * F4-S6 AC8 adds the second piece of app-level state: **which pull is open**.
 * It lives here rather than inside `Results` because two different views open
 * one (a search today, a recents row when F1-S1 lands) and because `Results`
 * must not be able to invent one — see `pull.ts` for the full rationale. There
 * is no initial value and no default: `null` means "no evidence is open", which
 * is a real state with its own render, not a hole.
 */
export default function App() {
  const [view, setView] = useState<ViewName>("home");

  /**
   * The evidence on screen. Starts `null` on every cold start, deliberately —
   * a remembered ref that survived into a fresh session would put numbers in
   * front of the user that they did not ask for in this session and cannot
   * attribute, which is the same defect as the hardcoded ref it replaces.
   */
  const [pull, setPull] = useState<OpenPull | null>(null);

  /**
   * Values to pre-fill the search form with on its next open, or `null` for a
   * blank form (which falls back to `searchMemory`).
   *
   * This is how the empty state's widen shortcuts work: they do NOT re-submit a
   * search on the user's behalf. F2 step 3 ("user understands cost before
   * spending calls") and F3's "no API call ever happens without the user having
   * chosen it" both mean a button that silently spends the month's calls is the
   * wrong shape, however convenient. So a widen re-opens the form with the
   * constraint already loosened and the live cost preview attached, and the
   * user submits it.
   */
  const [prefill, setPrefill] = useState<SearchValues | null>(null);

  /**
   * The ONE way a pull becomes the open one — F4-S6 AC8.
   *
   * Both doors into Results call this: `SearchForm` with the ref its
   * `POST /api/search` returned, and (when F1-S1 lands) a recents row with the
   * ref that row opened. Landing on Results is F4 flow step 4, "system lands
   * user on Results", so it happens here rather than being left to the caller —
   * that way neither door can wire up the evidence and forget to show it.
   */
  const openPull = (next: OpenPull) => {
    setPull(next);
    setPrefill(null);
    setView("results");
  };

  /** A widen shortcut: back to the form, pre-filled with a loosened search. */
  const reopenSearch = (values: SearchValues) => {
    setPrefill(values);
    setView("home");
  };

  return (
    <div>
      <TopBar active={view} onNavigate={setView} />
      <main>
        {view === "home" && (
          <Home
            onOpenPull={openPull}
            prefill={prefill}
            onPrefillConsumed={() => setPrefill(null)}
          />
        )}
        {view === "results" && <Results pull={pull} onWiden={reopenSearch} />}
        {view === "analysis" && <Analysis />}
        {view === "settings" && <Settings />}
      </main>
    </div>
  );
}
