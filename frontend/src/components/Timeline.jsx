import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listDocuments } from "../api/client";
import { CATEGORY_COLORS, categoryColor } from "../categories";
import { useColdStart } from "./ColdStartNotice";
import DemoNotice from "./DemoNotice";
import LoadDemoButton from "./LoadDemoButton";
import TimelineEntry from "./TimelineEntry";
import { ErrorBanner } from "./cardParts";

// The journey view (plan.md §6 View 2). This is also the persistent "load
// existing documents" surface — it reads GET /api/documents (already ordered
// and date-resolved server-side) rather than a bespoke /api/timeline, so the
// upload-date fallback and its flag are applied in exactly one place.
//
// Sorting is on `effective_date` ONLY — never the raw extracted_date column
// (see CLAUDE.md). Unknown dates sort last in either direction.
function yearOf(doc) {
  const d = doc.effective_date;
  return typeof d === "string" && d.length >= 4 ? d.slice(0, 4) : "Undated";
}

export default function Timeline({ onNavigate }) {
  const [docs, setDocs] = useState(null); // null = loading
  const [error, setError] = useState("");
  const [newestFirst, setNewestFirst] = useState(true);
  const [filter, setFilter] = useState("All");
  // On a cold start the first list request is still in flight when the user can
  // already click Load Demo, so two loads overlap and they can finish in either
  // order. Only the newest one may write — otherwise the stale empty list lands
  // after the seed and wipes the timeline the user just asked for.
  const requestId = useRef(0);
  const coldStart = useColdStart();

  const load = useCallback(async () => {
    const id = ++requestId.current;
    try {
      setError("");
      const next = await listDocuments();
      if (id !== requestId.current) return;
      setDocs(next);
    } catch (e) {
      if (id !== requestId.current) return;
      setError(e.message);
      setDocs([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Categories actually present, in the palette's canonical order — no dead
  // chips for categories nobody has uploaded.
  const categories = useMemo(() => {
    if (!docs) return [];
    const present = new Set(docs.map((d) => d.category).filter(Boolean));
    // Palette order (categories.js) is canonical, so chips read the same across
    // sessions regardless of upload order.
    return Object.keys(CATEGORY_COLORS).filter((c) => present.has(c));
  }, [docs]);

  const hasDemo = useMemo(
    () => (docs ?? []).some((d) => typeof d.id === "string" && d.id.startsWith("demo-")),
    [docs],
  );

  const groups = useMemo(() => {
    if (!docs) return [];
    const filtered =
      filter === "All" ? docs : docs.filter((d) => d.category === filter);

    const sorted = [...filtered].sort((a, b) => {
      const av = a.effective_date || "";
      const bv = b.effective_date || "";
      if (av === bv) return 0;
      // Empty (unknown) always last, regardless of direction.
      if (!av) return 1;
      if (!bv) return -1;
      return newestFirst ? bv.localeCompare(av) : av.localeCompare(bv);
    });

    const out = [];
    let current = null;
    for (const doc of sorted) {
      const year = yearOf(doc);
      if (!current || current.year !== year) {
        current = { year, items: [] };
        out.push(current);
      }
      current.items.push(doc);
    }
    return out;
  }, [docs, filter, newestFirst]);

  // A fast response never reaches the CTA below — it goes straight from this to
  // the real timeline.
  if (docs === null && !coldStart) {
    return <p className="py-12 text-center text-sm text-sand-500">Loading…</p>;
  }

  if (docs && error && docs.length === 0) {
    return (
      <ErrorBanner message={error} />
    );
  }

  // Empty *and* still-waking share one branch, and deliberately so: React keeps
  // the LoadDemoButton mounted across the transition between them, so a seed
  // started during the cold start keeps its in-flight spinner when the original
  // (empty) list request finally lands underneath it. Two branches would remount
  // the button mid-seed and it would look idle while still working.
  if (docs === null || docs.length === 0) {
    const waking = docs === null;
    return (
      <div className="rounded-xl border border-dashed border-sand-300 bg-paper px-6 py-16 text-center">
        <p className="text-sm font-medium text-sand-600">
          {waking ? "Starting up…" : "Your timeline is empty"}
        </p>
        <p className="mt-1 text-xs text-sand-500">
          {waking ? (
            <>
              The first load can take a minute. You can add your first document
              now — it will go through as soon as we’re ready.
            </>
          ) : (
            <>
              Add documents, URLs, or achievements — they’ll appear here in
              order.
            </>
          )}
        </p>
        {/* Hierarchy is deliberate: adding your own documents is the product,
            so it gets the one filled button. The demo profile sits below as a
            quiet alternative for a visitor with nothing to hand — when it was
            the only filled control here, the sample data read as the intended
            path. */}
        <div className="mt-6 flex flex-col items-center gap-3">
          <button
            onClick={() => onNavigate?.("upload")}
            className="rounded-lg bg-espresso-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-espresso-700"
          >
            Add your first document
          </button>
          <div className="flex flex-col items-center gap-1">
            <p className="text-xs text-sand-500">
              Nothing to hand? Explore a sample student journey instead.
            </p>
            <LoadDemoButton onLoaded={load} />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Above the controls, not inside them: it says what this whole timeline
          is, and its inline confirm needs room the filter row cannot give
          without shoving the sort toggle sideways. Rendered only when the demo
          is actually loaded — `demo-` is the id prefix the seed stamps, and the
          backend clears by that same prefix. */}
      {hasDemo && <DemoNotice onCleared={load} />}

      {/* Filter chips + order toggle */}
      <div className="flex flex-wrap items-center gap-2">
        {["All", ...categories].map((cat) => (
          <button
            key={cat}
            onClick={() => setFilter(cat)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              filter === cat
                ? "border-espresso-400 bg-espresso-50 text-espresso-700"
                : "border-sand-300 bg-paper text-sand-600 hover:border-sand-400"
            }`}
          >
            {cat !== "All" && (
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: categoryColor(cat) }}
              />
            )}
            {cat}
          </button>
        ))}
        <button
          onClick={() => setNewestFirst((v) => !v)}
          className="ml-auto rounded-md border border-sand-300 bg-paper px-3 py-1 text-xs font-medium text-sand-600 transition hover:border-sand-400"
        >
          {newestFirst ? "Newest first ↓" : "Oldest first ↑"}
        </button>
      </div>


      {/* The spine: a single vertical rule the year groups and dots sit on. */}
      <div className="border-l border-sand-200 pl-2">
        {groups.map((group) => (
          <section key={group.year} className="mb-2">
            {/* The year is the spine's landmark, so it gets the display face
                and tabular figures — proportional digits make a column of
                years visibly ragged. */}
            <h3 className="mb-3 ml-6 font-display text-base font-semibold tabular-nums text-sand-600">
              {group.year}
            </h3>
            <ol>
              {group.items.map((doc) => (
                <TimelineEntry
                  key={doc.id}
                  doc={doc}
                  onDeleted={load}
                  onUpdated={load}
                />
              ))}
            </ol>
          </section>
        ))}
      </div>
    </div>
  );
}
