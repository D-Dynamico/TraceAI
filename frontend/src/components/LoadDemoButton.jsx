import { useState } from "react";
import { seedDemo } from "../api/client";

// "Load Demo Profile" CTA for the empty views (plan.md §6 design principle:
// "Empty states seed the demo"). Seeds the 10-document journey server-side, then
// calls `onLoaded` so the host view refetches and the timeline/graph fill in —
// a visible change, not a silent no-op. Idempotent on the backend, but the
// button disables while in flight so an impatient double-click can't race.
export default function LoadDemoButton({ onLoaded, className = "" }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleClick() {
    setLoading(true);
    setError("");
    try {
      await seedDemo();
      await onLoaded?.();
    } catch (e) {
      setError(e.message || "Could not load the demo profile.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={className}>
      <button
        onClick={handleClick}
        disabled={loading}
        className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {loading && (
          <svg
            className="h-4 w-4 animate-spin text-white"
            viewBox="0 0 24 24"
            fill="none"
            aria-hidden="true"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 0 1 8-8v4a4 4 0 0 0-4 4H4z"
            />
          </svg>
        )}
        {loading ? "Loading demo…" : "Load Demo Profile"}
      </button>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
    </div>
  );
}
