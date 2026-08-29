import { useState } from "react";
import NavBar from "./components/NavBar";
import Upload from "./components/Upload";
import Timeline from "./components/Timeline";
import Search from "./components/Search";
import KnowledgeGraph from "./components/KnowledgeGraph";
import QuotaNotice from "./components/QuotaNotice";

// Four views, one nav, no router (plan.md §6). A single piece of view state is
// enough — there are no nested routes and no URLs to deep-link yet.
export default function App() {
  const [view, setView] = useState("timeline");

  return (
    <div className="min-h-screen bg-parchment">
      <NavBar view={view} onChange={setView} />
      {/* The graph is the judge-facing view (plan.md §6 View 3) and wants room —
          it gets a wider container than the reading-width views. */}
      <main
        className={`mx-auto px-6 py-8 ${
          view === "graph" ? "max-w-5xl" : "max-w-3xl"
        }`}
      >
        {view === "timeline" && <Timeline onNavigate={setView} />}
        {view === "search" && <Search />}
        {view === "upload" && <Upload />}
        {view === "graph" && <KnowledgeGraph onNavigate={setView} />}
      </main>
      {/* Below the view, not above it: the ceiling is context a reviewer should
          have, but it is not what any screen is about. Kept at reading width
          even on the wider graph view so it reads as a footnote to the app
          rather than as part of the graph. */}
      <QuotaNotice />
    </div>
  );
}
