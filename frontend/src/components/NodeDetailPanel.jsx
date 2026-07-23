// The side panel that opens when a graph node is clicked (plan.md §6 View 3).
//
// One panel serves all three node kinds because they answer the same question —
// "what is this and what does it connect to?" — with different fields:
//   - document    → summary + the format-preserving download/open action
//   - career_path → match %, skill gaps, and the documents that evidence it
//   - skill       → the documents that certify or use it
//
// It reuses the shared card pieces (CategoryBadge, FormatBadge, OriginalAction,
// the assumed-date rule) so a category, a date, and a download behave exactly as
// they do on the timeline and search views — never re-derived here.

import {
  AssumedDateNotice,
  CategoryBadge,
  FormatBadge,
  knownDate,
  OriginalAction,
} from "./cardParts";
import { CAREER_PATH_COLOR, categoryColor } from "../categories";

// A connected node, as a clickable row that re-selects it — so the panel is also
// a way to walk the chain (cert → skill → project → …) one hop at a time.
function ConnectedRow({ node, relation, onSelect }) {
  const dotColor =
    node.type === "career_path"
      ? CAREER_PATH_COLOR
      : node.type === "skill"
      ? categoryColor("Skills")
      : categoryColor(node.category);
  return (
    <button
      onClick={() => onSelect(node.id)}
      className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition hover:bg-slate-50"
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: dotColor }}
      />
      <span className="min-w-0 flex-1 truncate text-slate-700">{node.label}</span>
      {relation && (
        <span className="shrink-0 text-[11px] text-slate-400">{relation}</span>
      )}
    </button>
  );
}

// Human wording for an edge, from the point of view of the node in focus.
const RELATION_LABEL = {
  certifies_skill: "certifies",
  skill_used_in: "used in",
  similar_to: "similar",
  leads_to: "leads to",
};

export default function NodeDetailPanel({ node, connections, onSelect, onClose }) {
  if (!node) return null;

  return (
    <aside className="absolute right-3 top-3 z-10 w-72 max-w-[calc(100%-1.5rem)] rounded-xl border border-slate-200 bg-white p-4 shadow-lg">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {node.type === "document" && <CategoryBadge category={node.category} />}
          {node.type === "skill" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs font-medium text-slate-700">
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: categoryColor("Skills") }}
              />
              Skill
            </span>
          )}
          {node.type === "career_path" && (
            <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs font-medium text-slate-700">
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ backgroundColor: CAREER_PATH_COLOR }}
              />
              Career path
            </span>
          )}
        </div>
        <button
          onClick={onClose}
          aria-label="Close panel"
          className="shrink-0 rounded-md px-1.5 text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
        >
          ✕
        </button>
      </div>

      <h3 className="mt-2 text-base font-semibold leading-snug text-slate-900">
        {node.label}
      </h3>

      {/* Document body */}
      {node.type === "document" && (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <FormatBadge fileType={node.file_type} />
            {knownDate(node) && <span>{knownDate(node)}</span>}
          </div>
          <AssumedDateNotice cat={node} />
          {node.summary && (
            <p className="text-sm leading-relaxed text-slate-600">{node.summary}</p>
          )}
          <OriginalAction
            id={node.id}
            hasOriginal={node.has_original}
            sourceUrl={node.source_url}
          />
        </div>
      )}

      {/* Career-path body */}
      {node.type === "career_path" && (
        <div className="mt-2 space-y-2">
          {typeof node.match_score === "number" && (
            <p className="text-sm font-medium text-slate-700">
              {Math.round(node.match_score * 100)}% match
            </p>
          )}
          {node.skill_gaps && (
            <div>
              <p className="text-[11px] uppercase tracking-wide text-slate-400">
                Skills to build
              </p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {node.skill_gaps.split(",").map((gap) => {
                  const g = gap.trim();
                  return g ? (
                    <span
                      key={g}
                      className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700"
                    >
                      {g}
                    </span>
                  ) : null;
                })}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Connections — present for every node kind. */}
      {connections?.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <p className="mb-1 text-[11px] uppercase tracking-wide text-slate-400">
            {node.type === "career_path" ? "Evidence" : "Connected"}
          </p>
          <div className="-mx-2 max-h-52 overflow-auto">
            {connections.map(({ node: n, relation }) => (
              <ConnectedRow
                key={n.id}
                node={n}
                relation={RELATION_LABEL[relation] || relation}
                onSelect={onSelect}
              />
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}
