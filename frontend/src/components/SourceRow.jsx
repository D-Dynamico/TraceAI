// One search result (plan.md §6 View 4): category identity, title, provenance,
// format badge, and the download/open action. Used for both filter-mode grids
// and semantic-mode ranked lists — the only difference there is the container.

import { categoryColor } from "../categories";
import { FormatBadge, formatMonth, OriginalAction } from "./cardParts";

export default function SourceRow({ result, cited = false }) {
  const { category, date_source, effective_date, source_url } = result;
  const heading = result.title || source_url || "Untitled";

  // Respect the assumed-date contract: an extracted date is shown as fact; an
  // assumed one is labelled, never printed as if it were real (plan.md § Risk
  // Mitigation, same rule the upload cards follow via knownDate).
  const dateText =
    date_source === "extracted" ? formatMonth(effective_date) : null;

  return (
    <div
      className={`flex items-start gap-3 rounded-lg border bg-white p-3 shadow-sm ${
        cited ? "border-indigo-300 ring-1 ring-indigo-200" : "border-slate-200"
      }`}
    >
      <span
        aria-hidden="true"
        className="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full"
        style={{ backgroundColor: categoryColor(category) }}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-slate-900">{heading}</p>
          <FormatBadge fileType={result.file_type} />
          {cited && (
            <span className="shrink-0 rounded-full border border-indigo-200 bg-indigo-50 px-1.5 py-0.5 text-[10px] font-medium text-indigo-700">
              cited
            </span>
          )}
        </div>
        <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-slate-500">
          {category && <span>{category}</span>}
          {dateText && <span aria-hidden="true">·</span>}
          {dateText && <span>{dateText}</span>}
          {date_source === "assumed" && (
            <span className="text-amber-600">· date assumed</span>
          )}
          {typeof result.score === "number" && (
            <span className="tabular-nums text-slate-400">
              · {Math.round(result.score * 100)}% match
            </span>
          )}
        </p>
        {result.summary && (
          <p className="mt-1 line-clamp-2 text-sm text-slate-600">{result.summary}</p>
        )}
      </div>
      <OriginalAction
        id={result.id}
        hasOriginal={result.has_original}
        sourceUrl={source_url}
      />
    </div>
  );
}
