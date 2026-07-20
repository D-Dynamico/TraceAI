import { useCallback, useRef, useState } from "react";
import { uploadFile, ingestUrl, ingestText } from "../api/client";
import GitHubCard from "./GitHubCard";
import ResultCard from "./ResultCard";

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
            <Result key={r.id} result={r} />
          ))}
        </div>
      )}
    </div>
  );
}
