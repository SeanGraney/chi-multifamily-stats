import { useState } from "react";
import TopBar, { type ViewName } from "./components/TopBar";
import Home from "./views/Home";
import Results from "./views/Results";
import Analysis from "./views/Analysis";
import Settings from "./views/Settings";

/**
 * Three-view routing via plain React state (D13's precedent — no
 * react-router-dom, see TopBar.tsx for the rationale). One state variable
 * is the whole "router."
 */
export default function App() {
  const [view, setView] = useState<ViewName>("home");

  return (
    <div>
      <TopBar active={view} onNavigate={setView} />
      <main>
        {view === "home" && <Home />}
        {view === "results" && <Results />}
        {view === "analysis" && <Analysis />}
        {view === "settings" && <Settings />}
      </main>
    </div>
  );
}
