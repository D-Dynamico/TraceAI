import { useEffect, useMemo, useState } from "react";
import { listDocuments } from "../api/client";
import { CATEGORY_COLORS, categoryColor } from "../categories";
import TimelineEntry from "./TimelineEntry";

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

export default function Timeline() {
  const [docs, setDocs] = useState(null); // null = loading
  const [error, setError] = useState("");
  const [newestFirst, setNewestFirst] = useState(true);
  const [filter, setFilter] = useState("All");

  useEffect(() => {
    listDocuments()
      .then(setDocs)
      .catch((e) => {
        setError(e.message);
        setDocs([]);
      });
  }, []);

  // Categories actually present, in the palette's canonical order — no dead
  // chips for categories nobody has uploaded.
  const categories = useMemo(() => {
    if (!docs) return [];
    const present = new Set(docs.map((d) => d.category).filter(Boolean));
    // Palette order (categories.js) is canonical, so chips read the same across
    // sessions regardless of upload order.
    return Object.keys(CATEGORY_COLORS).filter((c) => present.has(c));
  }, [docs]);

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

  if (docs === null) {
    return <p className="py-12 text-center text-sm text-slate-400">Loading…</p>;
  }

  if (error && docs.length === 0) {
    return (
      <p className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
        {error}
      </p>
    );
  }

  if (docs.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-slate-300 bg-white px-6 py-16 text-center">
        <p className="text-sm font-medium text-slate-600">Your timeline is empty</p>
        <p className="mt-1 text-xs text-slate-400">
          Head to <span className="font-medium">Upload</span> to add documents,
          URLs, or achievements — they’ll appear here in order.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {/* Filter chips + order toggle */}
      <div className="flex flex-wrap items-center gap-2">
        {["All", ...categories].map((cat) => (
          <button
            key={cat}
            onClick={() => setFilter(cat)}
            className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition ${
              filter === cat
                ? "border-indigo-400 bg-indigo-50 text-indigo-700"
                : "border-slate-300 bg-white text-slate-600 hover:border-slate-400"
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
          className="ml-auto rounded-md border border-slate-300 bg-white px-3 py-1 text-xs font-medium text-slate-600 transition hover:border-slate-400"
        >
          {newestFirst ? "Newest first ↓" : "Oldest first ↑"}
        </button>
      </div>

      {/* The spine: a single vertical rule the year groups and dots sit on. */}
      <div className="border-l border-slate-200 pl-2">
        {groups.map((group) => (
          <section key={group.year} className="mb-2">
            <h3 className="mb-3 ml-6 text-sm font-semibold text-slate-400">
              {group.year}
            </h3>
            <ol>
              {group.items.map((doc) => (
                <TimelineEntry key={doc.id} doc={doc} />
              ))}
            </ol>
          </section>
        ))}
      </div>
    </div>
  );
}
