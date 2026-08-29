import { useState } from "react";
import { seedDemo } from "../api/client";

// "Load Demo Profile" CTA for the empty views (plan.md §6 design principle:
// "Empty states seed the demo"). Seeds the 10-document journey server-side, then
// calls `onLoaded` so the host view refetches and the timeline/graph fill in —
// a visible change, not a silent no-op. Idempotent on the backend, but the
// button disables while in flight so an impatient double-click can't race.
//
// Defaults to the *secondary* style on purpose. The demo is a fallback for a
// visitor with nothing to upload, not the thing the product is for; rendering
// it as the only filled button on an empty screen made the sample data read as
// the intended path. `variant="primary"` stays available for a surface where
// seeding genuinely is the main action.
const VARIANTS = {
  primary:
    "bg-espresso-600 text-white shadow-sm hover:bg-espresso-700",
  secondary:
    "border border-sand-300 bg-paper text-sand-600 hover:border-sand-400 hover:text-sand-900",
};

export default function LoadDemoButton({
  onLoaded,
  className = "",
  variant = "secondary",
}) {
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
        className={`inline-flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-60 ${
          VARIANTS[variant] ?? VARIANTS.secondary
        }`}
      >
        {loading && (
          // currentColor throughout, so the spinner follows whichever variant
          // is in use rather than being hard-coded white.
          <svg
            className="h-4 w-4 animate-spin"
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
