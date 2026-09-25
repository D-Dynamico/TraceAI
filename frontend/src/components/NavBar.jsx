// The single top nav (plan.md §6): four views, one bar, no nesting — plus the
// light/dark toggle, which is chrome rather than a view.
import { useState } from "react";
import { effectiveTheme, setTheme } from "../theme";

const TABS = [
  { id: "timeline", label: "Timeline" },
  { id: "search", label: "Search" },
  { id: "upload", label: "Upload" },
  { id: "graph", label: "Graph" },
];

/** Two states, not three: the OS setting is the default until the first click,
 * and after that the user's choice is the answer. A "system" state would need
 * a third icon to explain itself for a choice few people revisit. */
function ThemeToggle() {
  const [theme, setLocal] = useState(effectiveTheme);
  const next = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => {
        setTheme(next);
        setLocal(next);
      }}
      aria-label={`Switch to ${next} theme`}
      title={`Switch to ${next} theme`}
      className="ml-1 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-sand-600 transition hover:bg-sand-200 hover:text-sand-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-espresso-400"
    >
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {theme === "dark" ? (
          // Sun: what a click switches to.
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </>
        ) : (
          <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
        )}
      </svg>
    </button>
  );
}

export default function NavBar({ view, onChange }) {
  return (
    <header className="border-b border-sand-200 bg-paper">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
        <h1 className="font-display text-xl font-semibold tracking-tight text-sand-900">
          Trace<span className="text-espresso-600">AI</span>
        </h1>
        <nav className="flex items-center gap-1">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => onChange(tab.id)}
              className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                view === tab.id
                  ? "bg-espresso-50 text-espresso-700"
                  : "text-sand-600 hover:bg-sand-200 hover:text-sand-900"
              }`}
            >
              {tab.label}
            </button>
          ))}
          <ThemeToggle />
        </nav>
      </div>
    </header>
  );
}
