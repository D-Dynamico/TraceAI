// The generic result card: uploaded files, written responses, and web URLs.
//
// GitHub results have their own card (GitHubCard.jsx) because a repo carries
// fields — stars, languages, a repo list — that have no analogue on a
// certificate. Everything those two cards genuinely share lives in
// cardParts.jsx, so what differs here is arrangement, not logic.

import { useState } from "react";
import { recategorize } from "../api/client";
import {
  AssumedDateNotice,
  CardShell,
  CategoryBadge,
  Confidence,
  DegradedNotice,
  EntityChips,
  ExtractedText,
  knownDate,
  Warnings,
} from "./cardParts";

export default function ResultCard({ result }) {
  const isUrl = result.kind === "url";
  const isText = result.kind === "text";
  const isFile = !isUrl && !isText;

  // A successful retry replaces the shown categorization in place (item B). The
  // parent's session list stays as-is; the card the user is looking at updates.
  const [override, setOverride] = useState(null);
  const [retrying, setRetrying] = useState(false);
  const cat = override || result.categorization;

  const handleRetry = async () => {
    setRetrying(true);
    try {
      setOverride(await recategorize(result.id));
    } catch {
      // categorize() never raises server-side; a transport error here just
      // leaves the degraded card as it was.
    } finally {
      setRetrying(false);
    }
  };

  // After a successful retry the confidence meter tells the story, so the stale
  // "unverified — review suggested" warning from the original degraded ingest
  // must not linger. Only that warning is dropped; extraction warnings stay.
  const retried = override && !cat.degraded_reason && (cat.confidence ?? 0) > 0;
  const warnings = retried
    ? (result.warnings || []).filter((w) => !/review suggested/i.test(w))
    : result.warnings;

  // Gemini's title is the useful name; the filename/URL becomes provenance.
  const heading = cat?.title || (isUrl ? result.title || result.url : result.filename);
  const source = isUrl ? result.url : result.filename;

  const meta = [
    isUrl ? result.source_type : result.file_type,
    cat?.document_type,
    knownDate(cat),
  ].filter(Boolean);

  // Diagnostics, moved off the headline meta line and onto the disclosure that
  // reveals the text they describe.
  const textMeta = [
    `${result.char_count} chars`,
    isFile ? result.method : null,
    result.used_ocr ? "OCR" : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <CardShell>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">{heading}</p>
          {/* Wraps rather than truncates: at phone width truncation ate the
              date, which is the one field here the timeline depends on. */}
          <p className="mt-0.5 text-xs text-slate-500">{meta.join(" · ")}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {isFile && (
            <a
              href={`/api/documents/${result.id}/download`}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:border-indigo-400 hover:text-indigo-600"
            >
              Download original
            </a>
          )}
          <CategoryBadge category={cat?.category} />
        </div>
      </div>

      {cat && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {cat.degraded_reason ? (
            <DegradedNotice
              cat={cat}
              onRetry={result.id ? handleRetry : undefined}
              retrying={retrying}
            />
          ) : (
            <Confidence value={cat.confidence} />
          )}
          <AssumedDateNotice cat={cat} />
        </div>
      )}

      {cat?.summary && (
        <p className="mt-2 text-sm leading-relaxed text-slate-600">{cat.summary}</p>
      )}

      <EntityChips cat={cat} />

      {/* Provenance: the heading is now Gemini's title, so show what it came from.
          Skipped for text entries — their "filename" is just the first line of
          the entry, so it would restate the text shown directly below. */}
      {source && cat?.title && !isText && (
        <p className="mt-3 truncate text-[11px] text-slate-400" title={source}>
          from {source}
        </p>
      )}

      {result.checksum && isFile && (
        <p
          className="mt-1 font-mono text-[11px] text-slate-400"
          title={`SHA-256: ${result.checksum}`}
        >
          sha256:{result.checksum.slice(0, 16)}… · original preserved
        </p>
      )}

      <Warnings items={warnings} />
      <ExtractedText text={result.text_preview} meta={textMeta} />
    </CardShell>
  );
}
