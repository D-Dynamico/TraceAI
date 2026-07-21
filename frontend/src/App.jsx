import { useState } from "react";
import NavBar from "./components/NavBar";
import Upload from "./components/Upload";
import Timeline from "./components/Timeline";
import Search from "./components/Search";

// Four views, one nav, no router (plan.md §6). A single piece of view state is
// enough — there are no nested routes and no URLs to deep-link yet.
export default function App() {
  const [view, setView] = useState("timeline");

  return (
    <div className="min-h-screen bg-slate-100">
      <NavBar view={view} onChange={setView} />
      <main className="mx-auto max-w-3xl px-6 py-8">
        {view === "timeline" && <Timeline />}
        {view === "search" && <Search />}
        {view === "upload" && <Upload />}
      </main>
    </div>
  );
}
