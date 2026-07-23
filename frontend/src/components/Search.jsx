import { useEffect, useRef, useState } from "react";
import { answer as fetchAnswer, search } from "../api/client";
import AnswerCard from "./AnswerCard";
import SourceRow from "./SourceRow";

// plan.md §16: the queries the demo must answer. Shown as chips so a reviewer
// knows what to try without guessing.
const SUGGESTED = [
  "Show all my certificates",
  "Show my latest resume",
  "Show internship documents",
  "What skills did I gain in 2024?",
  "How does my Python certification connect to my internship?",
];

export default function Search() {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The RAG answer is a second, slower request fired only for question queries,
  // so it has its own state: the sources render the instant search returns.
  const [answerBusy, setAnswerBusy] = useState(false);
  const [answerData, setAnswerData] = useState(null);
  const inputRef = useRef(null);

  // The search bar is focused on arrival (plan.md §6 View 4).
  useEffect(() => inputRef.current?.focus(), []);

  // Synthesize an answer over the sources search returned. Kept separate from
  // run() so a retry can re-fire it without re-running the search.
  async function loadAnswer(q, results) {
    setAnswerBusy(true);
    setAnswerData(null);
    try {
      setAnswerData(await fetchAnswer(q, results.map((r) => r.id)));
    } catch (e) {
      // A failed answer must never blank the sources — degrade to sources-only.
      setAnswerData(null);
      setError(e.message);
    }
    setAnswerBusy(false);
  }

  async function run(q) {
    const trimmed = (q ?? query).trim();
    if (!trimmed) return;
    setQuery(trimmed);
    setBusy(true);
    setError("");
    setAnswerData(null);
    setAnswerBusy(false);
    try {
      const res = await search(trimmed);
      setResponse(res);
      // A question over at least one source gets a synthesized answer card.
      if (res.answerable && res.results.length > 0) {
        loadAnswer(trimmed, res.results);
      }
    } catch (e) {
      setError(e.message);
      setResponse(null);
    }
    setBusy(false);
  }

  const isSemantic = response?.mode === "semantic";
  // Which source rows the answer actually cited — badged so a reviewer can see
  // exactly what informed the synthesized answer.
  const citedIds = new Set(answerData?.cited_doc_ids || []);

  return (
    <div className="space-y-5">
      <div className="flex gap-2">
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="Ask anything — “show my certificates”, “what did I build in 2024?”"
          className="flex-1 rounded-lg border border-slate-300 px-4 py-2.5 text-sm outline-none focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400"
        />
        <button
          onClick={() => run()}
          disabled={busy || !query.trim()}
          className="rounded-lg bg-indigo-600 px-5 py-2.5 text-sm font-medium text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy ? "Searching…" : "Search"}
        </button>
      </div>

      {/* Suggested queries — only while nothing has been searched yet. */}
      {!response && !busy && (
        <div className="flex flex-wrap gap-2">
          {SUGGESTED.map((q) => (
            <button
              key={q}
              onClick={() => run(q)}
              className="rounded-full border border-slate-300 bg-white px-3 py-1 text-xs text-slate-600 transition hover:border-indigo-400 hover:text-indigo-700"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-700">
          {error}
        </div>
      )}

      {response && (
        <div className="space-y-3">
          {/* Answer card (plan.md §6 View 4) — question queries only, above the
              sources. Its own loading/degraded state; the sources never wait on it. */}
          {response.answerable && (answerBusy || answerData) && (
            <AnswerCard
              loading={answerBusy}
              data={answerData}
              onRetry={() => loadAnswer(response.query, response.results)}
            />
          )}

          <p className="text-xs text-slate-500">
            {response.count === 0
              ? "No matches"
              : isSemantic
              ? `${response.count} result${response.count === 1 ? "" : "s"} · ranked by relevance`
              : `${response.count} result${response.count === 1 ? "" : "s"}${
                  response.category ? ` · ${response.category}` : ""
                }`}
          </p>

          {response.results.map((r) => (
            <SourceRow key={r.id} result={r} cited={citedIds.has(r.id)} />
          ))}

          {response.count === 0 && (
            <p className="rounded-lg border border-dashed border-slate-300 bg-white px-4 py-6 text-center text-sm text-slate-400">
              Nothing matched “{response.query}”. Try a category like “certificates”
              or “projects”.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
