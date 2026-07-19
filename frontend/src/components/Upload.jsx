import { useCallback, useRef, useState } from "react";
import { uploadFile, ingestUrl } from "../api/client";

const ACCEPT = ".pdf,.docx,.pptx,.txt,.md,.png,.jpg,.jpeg,.tiff,.bmp,.webp";

function ResultCard({ result }) {
  const isUrl = result.kind === "url";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">
            {isUrl ? result.title || result.url : result.filename}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {isUrl ? result.source_type : result.file_type} ·{" "}
            {isUrl ? "web" : result.method}
            {result.used_ocr ? " · OCR" : ""} · {result.char_count} chars
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {!isUrl && (
            <a
              href={`/api/documents/${result.id}/download`}
              className="rounded-md border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-700 transition hover:border-indigo-400 hover:text-indigo-600"
            >
              Download original
            </a>
          )}
          <span className="rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
            extracted
          </span>
        </div>
      </div>

      {result.checksum && (
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
          PDF, DOCX, PPTX, TXT, images — extracted automatically
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
