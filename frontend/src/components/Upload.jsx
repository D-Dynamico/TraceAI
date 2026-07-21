import { useCallback, useRef, useState } from "react";
import { uploadFile, ingestUrl, ingestText } from "../api/client";
import GitHubCard from "./GitHubCard";
import ResultCard from "./ResultCard";
import { CardShell } from "./cardParts";

const ACCEPT = ".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.tiff,.bmp,.webp";

/** Pick the card for a result.
 *
 * `source_type` is set by the backend's URL router, so this asks what the
 * thing *is* rather than sniffing the URL string a second time. A GitHub
 * result with no `details` (an API failure mid-scrape) still routes here —
 * GitHubCard renders the empty shape, which is why the backend always sends a
 * complete one.
 */
function Result({ result }) {
  if (result.kind === "url" && result.source_type === "github") {
    return <GitHubCard result={result} />;
  }
  return <ResultCard result={result} />;
}

/** A pending item, shown at the top of the results list while its request is
 * in flight (deferred item A). The wait is dominated by the Gemini call behind
 * a 6.5s rate limiter, not by bytes — so this is an honest indeterminate
 * skeleton, never a percentage that would be a lie. */
function PendingCard({ label }) {
  return (
    <CardShell>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-slate-500">{label}</p>
          <div className="mt-2 space-y-1.5">
            <div className="h-2 w-2/3 animate-pulse rounded bg-slate-200" />
            <div className="h-2 w-1/3 animate-pulse rounded bg-slate-200" />
          </div>
        </div>
        <span className="shrink-0 text-xs text-slate-400">categorizing…</span>
      </div>
    </CardShell>
  );
}

let pendingSeq = 0;

export default function Upload() {
  const [results, setResults] = useState([]);
  const [pending, setPending] = useState([]); // [{ id, label }]
  // Per-input busy, not one shared boolean: uploading files must not disable the
  // URL and text inputs (deferred item A — each input says what *it* is doing).
  const [busy, setBusy] = useState({ files: false, url: false, text: false });
  const [batch, setBatch] = useState(null); // { done, total } during multi-file
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const [entry, setEntry] = useState("");
  const inputRef = useRef(null);

  const addPending = useCallback((label) => {
    const id = ++pendingSeq;
    setPending((p) => [{ id, label }, ...p]);
    return id;
  }, []);
  const clearPending = useCallback((id) => {
    setPending((p) => p.filter((x) => x.id !== id));
  }, []);

  const handleFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList);
      if (files.length === 0) return;
      setBusy((b) => ({ ...b, files: true }));
      setBatch({ done: 0, total: files.length });
      setError("");
      for (const file of files) {
        const pid = addPending(file.name);
        try {
          const data = await uploadFile(file);
          setResults((prev) => [{ kind: "file", ...data }, ...prev]);
        } catch (e) {
          setError(`${file.name}: ${e.message}`);
        } finally {
          clearPending(pid);
          setBatch((b) => (b ? { ...b, done: b.done + 1 } : b));
        }
      }
      setBusy((b) => ({ ...b, files: false }));
      setBatch(null);
    },
    [addPending, clearPending]
  );

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
    setBusy((b) => ({ ...b, url: true }));
    setError("");
    const pid = addPending(trimmed);
    try {
      const data = await ingestUrl(trimmed);
      setResults((prev) => [{ kind: "url", ...data }, ...prev]);
      setUrl("");
    } catch (e) {
      setError(e.message);
    } finally {
      clearPending(pid);
      setBusy((b) => ({ ...b, url: false }));
    }
  }, [url, addPending, clearPending]);

  const submitEntry = useCallback(async () => {
    const trimmed = entry.trim();
    if (!trimmed) return;
    setBusy((b) => ({ ...b, text: true }));
    setError("");
    const pid = addPending(trimmed.split("\n")[0].slice(0, 60));
    try {
      const data = await ingestText(trimmed);
      setResults((prev) => [{ kind: "text", ...data }, ...prev]);
      setEntry("");
    } catch (e) {
      setError(e.message);
    } finally {
      clearPending(pid);
      setBusy((b) => ({ ...b, text: false }));
    }
  }, [entry, addPending, clearPending]);

  const dropLabel = busy.files
    ? batch && batch.total > 1
      ? `Ingesting ${Math.min(batch.done + 1, batch.total)} of ${batch.total}…`
      : "Processing…"
    : "Drop files here or click to browse";

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
        onClick={() => !busy.files && inputRef.current?.click()}
        className={`rounded-xl border-2 border-dashed p-10 text-center transition ${
          busy.files ? "cursor-wait" : "cursor-pointer"
        } ${
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
        <p className="text-sm font-medium text-slate-700">{dropLabel}</p>
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
          disabled={busy.url || !url.trim()}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy.url ? "Ingesting…" : "Ingest"}
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
            disabled={busy.text || !entry.trim()}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy.text ? "Adding…" : "Add entry"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Results — pending skeletons slot in at the top as each item resolves. */}
      {(pending.length > 0 || results.length > 0) && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-slate-600">
            Ingested ({results.length})
          </h2>
          {pending.map((p) => (
            <PendingCard key={p.id} label={p.label} />
          ))}
          {results.map((r) => (
            <Result key={r.id} result={r} />
          ))}
        </div>
      )}
    </div>
  );
}
