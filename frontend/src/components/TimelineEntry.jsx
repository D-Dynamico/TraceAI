import { useState } from "react";
import { deleteDocument, getDocument, setCategory } from "../api/client";
import { CATEGORY_CHOICES, categoryColor } from "../categories";
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
export default function TimelineEntry({ doc, onDeleted, onUpdated }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  // Delete is a two-step inline confirm — a destructive action gated behind a
  // second click, not a browser confirm() dialog. On success the parent
  // refetches (onDeleted), which unmounts this entry.
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [delError, setDelError] = useState("");
  // The manual category override (plan.md § Risk Mitigation). `saved` holds the
  // server's answer so the badge changes the instant the PATCH resolves rather
  // than waiting on the parent's refetch — the reclassification is the whole
  // point of the click, and a badge that lags it reads as a failed action.
  const [picking, setPicking] = useState(false);
  const [saving, setSaving] = useState("");
  const [catError, setCatError] = useState("");
  const [saved, setSaved] = useState(null);
  const assumed = doc.date_source === "assumed";
  const category = saved?.category ?? doc.category;
  const manual = (saved?.category_source ?? doc.category_source) === "manual";
  const color = categoryColor(category);

  async function chooseCategory(next) {
    if (next === category) {
      setPicking(false);
      return;
    }
    setSaving(next);
    setCatError("");
    try {
      setSaved(await setCategory(doc.id, next));
      setPicking(false);
      // The parent regroups and rebuilds its filter chips from the listing, so
      // it has to hear about this — the entry is not the only thing the
      // category drives.
      onUpdated?.(doc.id);
    } catch (e) {
      setCatError(e.message);
    } finally {
      setSaving("");
    }
  }

  async function handleDelete() {
    setDeleting(true);
    setDelError("");
    try {
      await deleteDocument(doc.id);
      onDeleted?.(doc.id);
      // No state reset on success — the refetch unmounts this component.
    } catch (e) {
      setDelError(e.message);
      setDeleting(false);
    }
  }

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
            <CategoryBadge category={category} />
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
            {/* Manual category override. The six-category taxonomy cannot fit
                everything — a whole GitHub profile is not a project — so the
                user gets the last word (plan.md § Risk Mitigation). It relabels
                only: the original, the extracted text, and the skills below all
                stay exactly as they were. */}
            <div
              role="group"
              aria-label="Category"
              className="mt-3 flex flex-wrap items-center gap-2"
            >
              <span className="text-[11px] uppercase tracking-wide text-slate-400">
                Category
              </span>
              <CategoryBadge category={category} />
              {manual && (
                <span className="text-[11px] text-slate-400">set by you</span>
              )}
              {!picking && (
                <button
                  onClick={() => setPicking(true)}
                  className="rounded-md px-1.5 py-0.5 text-[11px] font-medium text-slate-400 transition hover:bg-slate-100 hover:text-slate-700"
                >
                  Change
                </button>
              )}
            </div>
            {picking && (
              <div
                role="group"
                aria-label="Choose a category"
                className="mt-2 flex flex-wrap items-center gap-1.5"
              >
                {CATEGORY_CHOICES.map((choice) => (
                  <button
                    key={choice}
                    onClick={() => chooseCategory(choice)}
                    disabled={Boolean(saving)}
                    aria-pressed={choice === category}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium transition disabled:opacity-50 ${
                      choice === category
                        ? "border-indigo-400 bg-indigo-50 text-indigo-700"
                        : "border-slate-300 bg-white text-slate-600 hover:border-slate-400"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: categoryColor(choice) }}
                    />
                    {saving === choice ? "Saving…" : choice}
                  </button>
                ))}
                <button
                  onClick={() => setPicking(false)}
                  disabled={Boolean(saving)}
                  className="rounded-md px-2 py-0.5 text-xs font-medium text-slate-500 transition hover:text-slate-700 disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            )}
            {catError && <p className="mt-2 text-xs text-red-600">{catError}</p>}

            <div className="mt-3 flex items-center justify-between gap-2">
              <OriginalAction
                id={doc.id}
                hasOriginal={doc.has_original}
                sourceUrl={doc.source_url}
              />
              {!confirming ? (
                <button
                  onClick={() => setConfirming(true)}
                  className="shrink-0 rounded-md px-2 py-1 text-xs font-medium text-slate-400 transition hover:bg-red-50 hover:text-red-600"
                >
                  Delete
                </button>
              ) : (
                <span className="inline-flex items-center gap-2 text-xs">
                  <span className="text-slate-500">Delete this?</span>
                  <button
                    onClick={handleDelete}
                    disabled={deleting}
                    className="rounded-md border border-red-300 bg-red-50 px-2 py-1 font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-50"
                  >
                    {deleting ? "Deleting…" : "Delete"}
                  </button>
                  <button
                    onClick={() => setConfirming(false)}
                    disabled={deleting}
                    className="rounded-md px-2 py-1 font-medium text-slate-500 transition hover:text-slate-700 disabled:opacity-50"
                  >
                    Cancel
                  </button>
                </span>
              )}
            </div>
            {delError && (
              <p className="mt-2 text-xs text-red-600">{delError}</p>
            )}
          </div>
        )}
      </div>
    </li>
  );
}
