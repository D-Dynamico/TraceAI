// The live ingest pipeline (plan.md §6 View 1): one strip of steps per item,
// "✓ uploaded → ✓ extracted → ⟳ categorizing → ○ index".
//
// Every state shown is one the browser actually observed — nothing is ticked on
// a timer. That constrains the middle of the strip: extraction and
// categorization happen inside a single server request, so from here they start
// together and finish together. Both read as active for that stretch rather
// than inventing a boundary the client cannot see. The ends are real: upload
// is XHR byte progress, and indexing is polled from /status after the
// response, because ingest answers before its background indexing runs.

// Which steps an item has, by kind. The server-side ones are marked so the
// "server" phase can light all of them at once.
const STEPS = {
  file: [
    { key: "upload", idle: "upload", active: "uploading", done: "uploaded" },
    { key: "extract", idle: "extract", active: "extracting", done: "extracted", server: true },
    { key: "categorize", idle: "categorize", active: "categorizing", done: "categorized", server: true },
    { key: "index", idle: "index", active: "indexing", done: "indexed" },
  ],
  url: [
    { key: "fetch", idle: "fetch", active: "fetching", done: "fetched", server: true },
    { key: "categorize", idle: "categorize", active: "categorizing", done: "categorized", server: true },
    { key: "index", idle: "index", active: "indexing", done: "indexed" },
  ],
  text: [
    { key: "categorize", idle: "categorize", active: "categorizing", done: "categorized", server: true },
    { key: "index", idle: "index", active: "indexing", done: "indexed" },
  ],
};

/** Derive each step's state and label from an item.
 *
 * item: { kind, phase: queued|sending|server|done|failed, progress (0–1),
 *         failedAt: sending|server, result, indexed: undefined|true|false }
 * Returns [{ key, state: waiting|active|done|warn|failed|skipped, label }].
 * Pure, so every branch is testable without rendering a pipeline.
 */
export function stepsFor(item) {
  const steps = STEPS[item.kind] ?? STEPS.file;
  const r = item.result;
  return steps.map((s) => {
    const at = (state, label = s.idle) => ({ key: s.key, state, label });

    if (item.phase === "queued") return at("waiting");

    if (item.phase === "sending" || (item.phase === "failed" && item.failedAt === "sending")) {
      if (s.key !== "upload") return at("waiting");
      if (item.phase === "failed") return at("failed", "upload failed");
      const pct = Math.round((item.progress ?? 0) * 100);
      return at("active", `${s.active} ${pct}%`);
    }

    if (item.phase === "server" || item.phase === "failed") {
      if (s.key === "upload") return at("done", s.done);
      if (s.server) return item.phase === "failed" ? at("failed", `${s.idle} failed`) : at("active", s.active);
      return at("waiting");
    }

    // done — the response is in hand.
    if (s.key === "upload" || s.key === "fetch") return at("done", s.done);
    if (s.key === "extract") {
      if (r?.extraction_degraded_reason || r?.char_count === 0) return at("warn", "no text found");
      return at("done", r?.used_ocr ? `${s.done} · OCR` : s.done);
    }
    if (s.key === "categorize") {
      return r?.categorization?.degraded_reason
        ? at("warn", "guessed from filename")
        : at("done", s.done);
    }
    // index
    if (!r?.char_count) return at("skipped", "nothing to index");
    if (item.indexed === true) return at("done", s.done);
    if (item.indexed === false) return at("warn", "not indexed yet");
    return at("active", s.active);
  });
}

const ICON = {
  waiting: "○",
  done: "✓",
  warn: "⚠",
  failed: "✕",
  skipped: "–",
};

const TONE = {
  waiting: "text-sand-500",
  active: "text-espresso-600",
  done: "text-sand-700",
  warn: "text-amber-700",
  failed: "text-red-700",
  skipped: "text-sand-500",
};

function StepIcon({ state }) {
  if (state === "active") {
    return (
      <span
        aria-hidden="true"
        className="inline-block h-2.5 w-2.5 animate-spin rounded-full border-[1.5px] border-current border-t-transparent"
      />
    );
  }
  return <span aria-hidden="true">{ICON[state]}</span>;
}

/** The strip itself. `aria-live` so a screen reader hears steps complete. */
export function PipelineSteps({ item }) {
  const steps = stepsFor(item);
  return (
    <ol
      aria-live="polite"
      aria-label="Processing steps"
      className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs"
    >
      {steps.map((s, i) => (
        <li key={s.key} className="inline-flex items-center gap-1.5">
          {i > 0 && (
            <span aria-hidden="true" className="text-sand-400">
              →
            </span>
          )}
          <span className={`inline-flex items-center gap-1 ${TONE[s.state]}`}>
            <StepIcon state={s.state} />
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}
