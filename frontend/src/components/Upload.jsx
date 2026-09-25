import { useCallback, useEffect, useRef, useState } from "react";
import { getStatus, uploadFile, ingestUrl, ingestText } from "../api/client";
import GitHubCard from "./GitHubCard";
import { PipelineSteps } from "./ProcessingQueue";
import ResultCard from "./ResultCard";
import { CardShell, ErrorBanner } from "./cardParts";

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

/** An item whose request has not answered yet: its label, the live pipeline
 * strip, and — while it is still working — a skeleton where the card will go.
 * A failed item keeps its strip (the ✕ says which step) but not the message,
 * which the error banner already carries. */
function PendingCard({ item }) {
  const working = item.phase !== "failed";
  return (
    <CardShell>
      <p className="truncate text-sm font-medium text-sand-500">{item.label}</p>
      <div className="mt-2">
        <PipelineSteps item={item} />
      </div>
      {working && (
        <div className="mt-3 space-y-1.5">
          <div className="h-2 w-2/3 animate-pulse rounded bg-sand-300" />
          <div className="h-2 w-1/3 animate-pulse rounded bg-sand-300" />
        </div>
      )}
    </CardShell>
  );
}

let itemSeq = 0;

// How long the index step is polled before it settles on "not indexed yet".
// Generous: on a cold free instance the first embed also loads the model.
const POLL_MS = 1500;
const POLL_TRIES = 40;

export default function Upload() {
  // One list for everything ingested this session, newest first. Each item
  // carries its own pipeline phase (see ProcessingQueue.jsx) and, once the
  // request answers, its result.
  const [items, setItems] = useState([]);
  // Per-input busy, not one shared boolean: uploading files must not disable the
  // URL and text inputs (deferred item A — each input says what *it* is doing).
  const [busy, setBusy] = useState({ files: false, url: false, text: false });
  const [batch, setBatch] = useState(null); // { done, total } during multi-file
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const [entry, setEntry] = useState("");
  const inputRef = useRef(null);

  // Polls outlive the view if the user navigates away mid-index; they check
  // this before writing, and stop.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const patch = useCallback((id, changes) => {
    setItems((all) => all.map((it) => (it.id === id ? { ...it, ...changes } : it)));
  }, []);

  const addItems = useCallback((specs) => {
    const created = specs.map((spec) => ({ id: ++itemSeq, phase: "queued", ...spec }));
    setItems((all) => [...created.slice().reverse(), ...all]);
    return created.map((c) => c.id);
  }, []);

  // Ingest answers before its background indexing runs, so the last step is
  // read from /status rather than assumed. A transport error ends the poll as
  // "not indexed yet" — the honest reading of "could not tell".
  const pollIndex = useCallback(
    async (id, docId) => {
      for (let i = 0; i < POLL_TRIES; i++) {
        if (!mounted.current) return;
        try {
          const { indexed } = await getStatus(docId);
          if (indexed) {
            if (mounted.current) patch(id, { indexed: true });
            return;
          }
        } catch {
          break;
        }
        await new Promise((r) => setTimeout(r, POLL_MS));
      }
      if (mounted.current) patch(id, { indexed: false });
    },
    [patch]
  );

  // Shared tail of all three inputs: run the request, move the item through its
  // phases, and start the index poll once there is a document to ask about.
  const run = useCallback(
    async (id, kind, request) => {
      let at = kind === "file" ? "sending" : "server";
      patch(id, { phase: at, progress: 0 });
      try {
        const data = await request((p) => {
          // Bytes are all sent once progress reaches 1; what follows is the
          // server's own work.
          if (p >= 1) at = "server";
          patch(id, at === "server" ? { phase: "server" } : { progress: p });
        });
        patch(id, { phase: "done", result: { kind, ...data } });
        if (data?.id && data.char_count) pollIndex(id, data.id);
      } catch (e) {
        patch(id, { phase: "failed", failedAt: at });
        throw e;
      }
    },
    [patch, pollIndex]
  );

  const handleFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList);
      if (files.length === 0) return;
      setBusy((b) => ({ ...b, files: true }));
      setBatch({ done: 0, total: files.length });
      setError("");
      // Every file is listed as queued up front, so a ten-file drop shows the
      // whole queue at once rather than one card appearing at a time.
      const ids = addItems(files.map((f) => ({ kind: "file", label: f.name })));
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        try {
          await run(ids[i], "file", (onProgress) => uploadFile(file, onProgress));
        } catch (e) {
          setError(`${file.name}: ${e.message}`);
        } finally {
          setBatch((b) => (b ? { ...b, done: b.done + 1 } : b));
        }
      }
      setBusy((b) => ({ ...b, files: false }));
      setBatch(null);
    },
    [addItems, run]
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
    const [id] = addItems([{ kind: "url", label: trimmed }]);
    try {
      await run(id, "url", () => ingestUrl(trimmed));
      setUrl("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, url: false }));
    }
  }, [url, addItems, run]);

  const submitEntry = useCallback(async () => {
    const trimmed = entry.trim();
    if (!trimmed) return;
    setBusy((b) => ({ ...b, text: true }));
    setError("");
    const [id] = addItems([{ kind: "text", label: trimmed.split("\n")[0].slice(0, 60) }]);
    try {
      await run(id, "text", () => ingestText(trimmed));
      setEntry("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, text: false }));
    }
  }, [entry, addItems, run]);

  const doneCount = items.filter((it) => it.result).length;

  const dropLabel = busy.files
    ? batch && batch.total > 1
      ? `Ingesting ${Math.min(batch.done + 1, batch.total)} of ${batch.total}…`
      : "Processing…"
    : "Drop files here or click to browse";

  return (
    <div className="space-y-6">
      {/* Drop zone */}
      <div
        /* The drop zone is a <div> with a click handler, so it was invisible to
           the keyboard: no tab stop, no role, and the real <input type="file">
           is hidden. Stating the role and handling Enter/Space makes it the
           button it already looked like. */
        role="button"
        tabIndex={0}
        aria-label="Choose files to upload"
        aria-busy={busy.files}
        onKeyDown={(e) => {
          if (e.key !== "Enter" && e.key !== " ") return;
          e.preventDefault();
          if (!busy.files) inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !busy.files && inputRef.current?.click()}
        className={`rounded-xl border-2 border-dashed p-10 text-center transition focus:outline-none focus-visible:ring-2 focus-visible:ring-espresso-400 ${
          busy.files ? "cursor-wait" : "cursor-pointer"
        } ${
          dragging
            ? "border-espresso-400 bg-espresso-50"
            : "border-sand-300 bg-paper hover:border-espresso-300 hover:bg-sand-200"
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
        <p className="text-sm font-medium text-sand-700">{dropLabel}</p>
        <p className="mt-1 text-xs text-sand-500">
          PDF, DOCX, PPTX, TXT, images — categorized automatically
        </p>
      </div>

      {/* URL ingest */}
      <div className="flex gap-2">
        <label htmlFor="ingest-url" className="sr-only">
          Repository or portfolio URL
        </label>
        <input
          id="ingest-url"
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submitUrl()}
          placeholder="Paste a GitHub repo or portfolio URL"
          className="flex-1 rounded-lg border border-sand-300 px-3 py-2 text-sm outline-none focus:border-espresso-400 focus:ring-1 focus:ring-espresso-400"
        />
        <button
          onClick={submitUrl}
          disabled={busy.url || !url.trim()}
          className="rounded-lg bg-espresso-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-espresso-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy.url ? "Ingesting…" : "Ingest"}
        </button>
      </div>

      {/* Written response — for achievements with no document behind them. */}
      <div className="space-y-2">
        <label htmlFor="written-entry" className="sr-only">
          Write an achievement that has no document
        </label>
        <textarea
          id="written-entry"
          value={entry}
          onChange={(e) => setEntry(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitEntry();
          }}
          rows={3}
          placeholder="Or just type it — “Led the Data Science Club in 2024, organized 5 workshops”"
          className="w-full resize-y rounded-lg border border-sand-300 px-3 py-2 text-sm outline-none focus:border-espresso-400 focus:ring-1 focus:ring-espresso-400"
        />
        <div className="flex items-center justify-between">
          <p className="text-xs text-sand-500">
            No certificate needed — club roles, hackathon wins, volunteer work.
          </p>
          <button
            onClick={submitEntry}
            disabled={busy.text || !entry.trim()}
            className="rounded-lg bg-espresso-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-espresso-700 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy.text ? "Adding…" : "Add entry"}
          </button>
        </div>
      </div>

      <ErrorBanner message={error} />

      {/* Everything ingested this session, newest first. An item shows its
          pipeline strip throughout; once its request answers, the strip sits
          above the result card so the index step can finish in place. */}
      {items.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-sm font-semibold text-sand-600">
            Ingested ({doneCount})
          </h2>
          {items.map((it) =>
            it.result ? (
              <div key={it.id} className="space-y-1.5">
                <div className="px-1">
                  <PipelineSteps item={it} />
                </div>
                <Result result={it.result} />
              </div>
            ) : (
              <PendingCard key={it.id} item={it} />
            )
          )}
        </div>
      )}
    </div>
  );
}
