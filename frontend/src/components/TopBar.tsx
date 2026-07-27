export type ViewName = "home" | "results" | "analysis";

interface TopBarProps {
  active: ViewName;
  onNavigate: (view: ViewName) => void;
}

/**
 * Persistent top bar (F0-S1b AC) — stays mounted across all three views.
 * Plain React state drives navigation (D13 already commits this project to
 * plain state over a routing library for curation state; view-switching is
 * a similarly small piece of state, so no react-router-dom dependency —
 * it's outside the frontend runtime dependency budget, ARCHITECTURE.md
 * §1a). See handoff note for this [DEFAULT] choice.
 */
export default function TopBar({ active, onNavigate }: TopBarProps) {
  const tabs: { view: ViewName; label: string; testId: string }[] = [
    { view: "home", label: "Home", testId: "nav-home" },
    { view: "results", label: "Results", testId: "nav-results" },
    { view: "analysis", label: "Analysis", testId: "nav-analysis" },
  ];

  return (
    <header
      data-testid="top-bar"
      className="border-b border-grey px-4 py-3 flex items-center gap-6"
    >
      <span className="text-amber font-bold">RentComp</span>
      <nav className="flex gap-4">
        {tabs.map(({ view, label, testId }) => (
          <button
            key={view}
            type="button"
            data-testid={testId}
            onClick={() => onNavigate(view)}
            aria-current={active === view ? "page" : undefined}
            className={
              active === view
                ? "text-green underline underline-offset-4"
                : "text-white hover:text-amber"
            }
          >
            {label}
          </button>
        ))}
      </nav>
    </header>
  );
}
