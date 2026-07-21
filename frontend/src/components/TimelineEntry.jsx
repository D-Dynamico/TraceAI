import { useState } from "react";
import { getDocument } from "../api/client";
import { categoryColor } from "../categories";
import {
  CategoryBadge,
  Chips,
  formatMonth,
  FormatBadge,
  OriginalAction,
} from "./cardParts";

// One dot on the journey. The dot sits on the shared spine drawn by Timeline;
// this component owns the marker, the headline, and the expand-to-detail.
//
// The assumed-date case (plan.md § Risk Mitigation) is flagged with a NON-color
// encoding — a hollow ring instead of a filled dot, plus an amber label —
// because on the timeline an assumed date otherwise "just looks like a document
// from today" (see cardParts.AssumedDateNotice). Color alone is never the flag.
export default function TimelineEntry({ doc }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const assumed = doc.date_source === "assumed";
  const color = categoryColor(doc.category);

  async function toggle() {
    const next = !open;
    setOpen(next);
    // Skills/tags live only on the detail record; fetch once, on first expand.
    if (next && !detail) {
      try {
        setDetail(await getDocument(doc.id));
      } catch {
        setDetail({ error: true });
      }
    }
  }

  return (
    <li className="relative mb-5 ml-6">
      {/* Marker on the spine. Filled = real date; ring = assumed. */}
      <span
        aria-hidden="true"
        className="absolute -left-[30px] top-1.5 h-3 w-3 rounded-full border-2 bg-white"
        style={{
          borderColor: color,
          backgroundColor: assumed ? "#fff" : color,
        }}
      />

      <div className="rounded-lg border border-slate-200 bg-white shadow-sm">
        <button
          onClick={toggle}
          className="flex w-full items-start justify-between gap-3 p-3 text-left"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <p className="truncate font-medium text-slate-900">
                {doc.title || doc.filename || "Untitled"}
              </p>
              <FormatBadge fileType={doc.file_type} />
            </div>
            <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
              {doc.date_source === "extracted" ? (
                <span>{formatMonth(doc.effective_date)}</span>
              ) : (
                <span className="text-amber-600">⚠ date assumed</span>
              )}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <CategoryBadge category={doc.category} />
            <span
              aria-hidden="true"
              className={`text-slate-400 transition ${open ? "rotate-90" : ""}`}
            >
              ▸
            </span>
          </div>
        </button>

        {open && (
          <div className="border-t border-slate-100 px-3 py-3">
            {doc.summary && (
              <p className="text-sm leading-relaxed text-slate-600">{doc.summary}</p>
            )}
            {assumed && (
              <p className="mt-2 text-xs text-amber-700">
                No date was found in this document, so it is placed at its upload
                date. Sorting reflects that guess.
              </p>
            )}
            {detail && !detail.error && (
              <div className="mt-3 space-y-1.5">
                <Chips label="Skills" items={detail.skills} />
                <Chips label="Orgs" items={detail.organizations} />
                <Chips label="Tags" items={detail.tags} />
              </div>
            )}
            <div className="mt-3">
              <OriginalAction
                id={doc.id}
                hasOriginal={doc.has_original}
                sourceUrl={doc.source_url}
              />
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
