// The single top nav (plan.md §6): four views, one bar, no nesting.
//
// Graph is Phase 5 and unbuilt, so it renders as a disabled tab with a "soon"
// hint rather than being omitted — the four-view shape is part of the spec and
// signals what is coming without pretending it works.

const TABS = [
  { id: "timeline", label: "Timeline" },
  { id: "search", label: "Search" },
  { id: "upload", label: "Upload" },
  { id: "graph", label: "Graph", disabled: true },
];

export default function NavBar({ view, onChange }) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
        <h1 className="text-lg font-bold tracking-tight text-slate-900">
          Trace<span className="text-indigo-600">AI</span>
        </h1>
        <nav className="flex items-center gap-1">
          {TABS.map((tab) =>
            tab.disabled ? (
              <span
                key={tab.id}
                title="Coming in Phase 5"
                className="cursor-not-allowed rounded-md px-3 py-1.5 text-sm font-medium text-slate-300"
              >
                {tab.label}
                <span className="ml-1 text-[10px] uppercase tracking-wide">soon</span>
              </span>
            ) : (
              <button
                key={tab.id}
                onClick={() => onChange(tab.id)}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition ${
                  view === tab.id
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                }`}
              >
                {tab.label}
              </button>
            )
          )}
        </nav>
      </div>
    </header>
  );
}
