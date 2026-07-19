import { useCallback, useRef, useState } from "react";
import { uploadFile, ingestUrl, ingestText } from "../api/client";
import { categoryColor, METER_FILL, METER_TRACK } from "../categories";

const ACCEPT = ".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.tiff,.bmp,.webp";

/** Category identity: a colored dot plus the name.
 *
 * The name is always rendered, never implied by the dot alone — the palette's
 * CVD separation is only valid alongside this label (see categories.js), and a
 * legend-free badge would otherwise be color-only identity.
 */
function CategoryBadge({ category }) {
  if (!category) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs font-medium text-slate-700">
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: categoryColor(category) }}
      />
      {category}
    </span>
  );
}

/** A single ratio against a limit — a meter, not a chart.
 *
 * Confidence 0.0 is not "0% sure", it is the categorizer's explicit
 * couldn't-classify fallback, so it gets a labelled warning instead of an
 * empty track that reads as a rendering bug.
 */
function Confidence({ value }) {
  if (typeof value !== "number") return null;

  if (value === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700">
        <span aria-hidden="true">⚠</span> Unverified — review suggested
      </span>
    );
  }

  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Categorization confidence"
        className="block h-1.5 w-16 overflow-hidden rounded-full"
        style={{ backgroundColor: METER_TRACK }}
      >
        <span
          className="block h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: METER_FILL }}
        />
      </span>
      <span className="text-xs tabular-nums text-slate-500">{pct}% confident</span>
    </span>
  );
}

/** Extracted entities. Muted ink so they never compete with the category. */
function Chips({ label, items }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">
        {label}
      </span>
      {items.map((item) => (
        <span
          key={item}
          className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

function ResultCard({ result }) {
  const isUrl = result.kind === "url";
  const isText = result.kind === "text";
  const cat = result.categorization;

  // Gemini's title is the useful name; the filename/URL becomes provenance.
  const heading = cat?.title || (isUrl ? result.title || result.url : result.filename);
  const source = isUrl ? result.url : result.filename;

  const meta = [
    isUrl ? result.source_type : result.file_type,
    !isUrl && !isText ? result.method : null,
    result.used_ocr ? "OCR" : null,
    cat?.document_type,
    cat?.date,
    `${result.char_count} chars`,
  ].filter(Boolean);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">{heading}</p>
          <p className="mt-0.5 truncate text-xs text-slate-500">{meta.join(" · ")}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!isUrl && !isText && (
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
          <Confidence value={cat.confidence} />
        </div>
      )}

      {cat?.summary && (
        <p className="mt-2 text-sm leading-relaxed text-slate-600">{cat.summary}</p>
      )}

      {cat && (
        <div className="mt-3 space-y-1.5">
          <Chips label="Skills" items={cat.skills} />
          <Chips label="Orgs" items={cat.organizations} />
          <Chips label="People" items={cat.people} />
          <Chips label="Tags" items={cat.tags} />
        </div>
      )}

      {/* Provenance: the heading is now Gemini's title, so show what it came from.
          Skipped for text entries — their "filename" is just the first line of
          the entry, so it would restate the text shown directly below. */}
      {source && cat?.title && !isText && (
        <p className="mt-3 truncate text-[11px] text-slate-400" title={source}>
          from {source}
        </p>
      )}

      {result.checksum && !isUrl && !isText && (
        <p
          className="mt-1 font-mono text-[11px] text-slate-400"
          title={`SHA-256: ${result.checksum}`}
        >
          sha256:{result.checksum.slice(0, 16)}… · original preserved
        </p>
      )}

      {result.warnings?.length > 0 && (
        <ul className="mt-2 space-y-1">
          {result.warnings.map((w, i) => (
            <li key={i} className="text-xs text-amber-600">
              ⚠ {w}
            </li>
          ))}
        </ul>
      )}

      {result.text_preview && (
        <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-700">
          {result.text_preview}
        </pre>
      )}
    </div>
  );
}

export default function Upload() {
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const [entry, setEntry] = useState("");
  const inputRef = useRef(null);

  const handleFiles = useCallback(async (fileList) => {
    const files = Array.from(fileList);
    if (files.length === 0) return;
    setBusy(true);
    setError("");
    for (const file of files) {
      try {
        const data = await uploadFile(file);
        setResults((prev) => [{ kind: "file", ...data }, ...prev]);
      } catch (e) {
        setError(`${file.name}: ${e.message}`);
      }
    }
    setBusy(false);
  }, []);

  const onDrop = useCallback(
    (e) => {
      e.preventDefault();
      setDragging(false);
      handleFiles(e.dataTransfer.files);
    },
    [handleFiles]
  );

  const submitUrl = useCallback(async () => {
    const trimmed = url.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const data = await ingestUrl(trimmed);
      setResults((prev) => [{ kind: "url", ...data }, ...prev]);
      setUrl("");
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }, [url]);

  const submitEntry = useCallback(async () => {
    const trimmed = entry.trim();
    if (!trimmed) return;
    setBusy(true);
    setError("");
    try {
      const data = await ingestText(trimmed);
      setResults((prev) => [{ kind: "text", ...data }, ...prev]);
      setEntry("");
    } catch (e) {
      setError(e.message);
    }
    setBusy(false);
  }, [entry]);

  return (
    <div className="space-y-6">
      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`cursor-pointer rounded-xl border-2 border-dashed p-10 text-center transition ${
          dragging
            ? "border-indigo-400 bg-indigo-50"
            : "border-slate-300 bg-white hover:border-indigo-300 hover:bg-slate-50"
        }`}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />
        <p className="text-sm font-medium text-slate-700">
          {busy ? "Processing…" : "Drop files here or click to browse"}
        </p>
        <p className="mt-1 text-xs text-slate-400">
          PDF, DOCX, PPTX, TXT, images — categorized automatically
        </p>
      </div>

      {/* URL ingest */}
      <div className="flex gap-2">
        <input
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitUrl()}
          placeholder="Paste a GitHub repo or portfolio URL"
          className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
        />
        <button
          onClick={submitUrl}
          disabled={busy || !url.trim()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Ingest
        </button>
      </div>

      {/* Written response — for achievements with no document behind them. */}
      <div className="space-y-2">
        <textarea
          value={entry}
          onChange={(e) => setEntry(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitEntry();
          }}
          rows={3}
          placeholder="Or just type it — “Led the Data Science Club in 2024, organized 5 workshops”"
          className="w-full resize-y rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
        />
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-400">
            No certificate needed — club roles, hackathon wins, volunteer work.
          </p>
          <button
            onClick={submitEntry}
            disabled={busy || !entry.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Add entry
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results */}
      {results.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-600">
            Ingested ({results.length})
          </h2>
          {results.map((r) => (
            <ResultCard key={r.id} result={r} />
          ))}
        </div>
      )}
    </div>
  );
}
