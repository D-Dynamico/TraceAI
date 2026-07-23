// The RAG answer card (plan.md §6 View 4): Gemini's synthesized answer in a
// tinted card above the source rows. Shown only for question-shaped queries;
// filter queries go straight to a grid with no card.
//
// Three honest states, never a faked one:
//   - synthesizing → a pulsing placeholder while the Gemini call runs
//   - answered     → the answer, with a note of how many sources it drew on
//   - degraded     → an amber/muted notice (reason-aware, retryable → Try again),
//                    with the sources still shown below. A quota wall never
//                    produces an invented answer — that is item B's whole point.

import { DEGRADED_COPY } from "./cardParts";

function Shell({ tone = "indigo", children }) {
  const tones = {
    indigo: "border-indigo-200 bg-indigo-50/70",
    amber: "border-amber-200 bg-amber-50",
    slate: "border-slate-200 bg-slate-50",
  };
  return (
    <div className={`rounded-xl border px-4 py-3 ${tones[tone]}`}>
      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
        Answer
      </p>
      {children}
    </div>
  );
}

export default function AnswerCard({ loading, data, onRetry }) {
  if (loading) {
    return (
      <Shell>
        <div className="animate-pulse space-y-2">
          <div className="h-3 w-11/12 rounded bg-indigo-200/70" />
          <div className="h-3 w-4/5 rounded bg-indigo-200/70" />
          <div className="h-3 w-2/3 rounded bg-indigo-200/70" />
        </div>
        <p className="mt-2 text-xs text-slate-400">Synthesizing an answer…</p>
      </Shell>
    );
  }

  if (!data) return null;

  // Degraded: no answer was synthesized. Show why, offer a retry when one helps,
  // and let the sources below carry the response.
  if (data.degraded_reason) {
    const retryable = Boolean(data.retryable);
    return (
      <Shell tone={retryable ? "amber" : "slate"}>
        <p
          className={`flex flex-wrap items-center gap-1.5 text-sm ${
            retryable ? "text-amber-800" : "text-slate-600"
          }`}
        >
          <span aria-hidden="true">{retryable ? "⚠" : "○"}</span>
          <span>
            Couldn’t synthesize an answer —{" "}
            {DEGRADED_COPY[data.degraded_reason] || "the AI service was unavailable."}{" "}
            {retryable ? "This usually clears." : ""} The matching sources are below.
          </span>
          {retryable && onRetry && (
            <button
              onClick={onRetry}
              className="ml-1 rounded border border-amber-300 px-1.5 py-0.5 text-[11px] font-medium text-amber-800 transition hover:bg-amber-100"
            >
              Try again
            </button>
          )}
        </p>
      </Shell>
    );
  }

  if (!data.answer) return null;

  const n = data.cited_doc_ids?.length || 0;
  return (
    <Shell>
      <p className="whitespace-pre-line text-sm leading-relaxed text-slate-800">
        {data.answer}
      </p>
      {n > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          Based on {n} source{n === 1 ? "" : "s"} · shown below
        </p>
      )}
    </Shell>
  );
}
