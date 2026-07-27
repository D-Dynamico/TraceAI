// The single top nav (plan.md §6): four views, one bar, no nesting.

const TABS = [
  { id: "timeline", label: "Timeline" },
  { id: "search", label: "Search" },
  { id: "upload", label: "Upload" },
  { id: "graph", label: "Graph" },
];

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
        </nav>
      </div>
    </header>
  );
}
