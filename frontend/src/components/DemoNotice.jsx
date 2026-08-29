import { useState } from "react";
import { clearDemo } from "../api/client";

// "You are looking at the demo profile" — plus the way back out.
//
// Both halves matter. The seed persists in the visitor's own dataset (see
// backend/identity.py), so once someone clicks "Load Demo Profile" every later
// visit lands on a full timeline with no hint that these ten documents are
// sample data rather than their own. This says so, and clears them on request.
//
// Only `demo-*` documents are removed — the scoping lives in the backend's
// `clear_demo`, so a reviewer who tried the demo and then uploaded something
// real keeps the upload. The confirm copy says as much.
//
// Layout: the label is anchored left and the actions right, so the two-step
// confirm expands into the strip's own slack. Nothing outside it moves, and
// nothing inside it moves except the controls being clicked.
export default function DemoNotice({ onCleared, className = "" }) {
  const [confirming, setConfirming] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState("");

  async function handleClear() {
    setClearing(true);
    setError("");
    try {
      await clearDemo();
      await onCleared?.();
      setConfirming(false);
    } catch (e) {
      setError(e.message || "Could not clear the demo profile.");
    } finally {
      setClearing(false);
    }
  }

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-lg border border-sand-300 bg-sand-200 px-3.5 py-2 text-xs text-sand-600">
        <p>
          Showing the{" "}
          <strong className="font-semibold text-sand-700">demo profile</strong> —
          sample documents, not your own.
        </p>
        {!confirming ? (
          <button
            onClick={() => setConfirming(true)}
            className="shrink-0 rounded-md border border-sand-300 bg-paper px-2.5 py-1 font-medium text-sand-600 transition hover:border-sand-400 hover:text-sand-900"
          >
            Clear demo
          </button>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-2">
            {/* Names what survives, so the click is not a guess about scope. */}
            <span className="text-sand-500">Remove them? Your uploads stay.</span>
            <button
              onClick={handleClear}
              disabled={clearing}
              className="rounded-md border border-red-300 bg-red-50 px-2.5 py-1 font-medium text-red-700 transition hover:bg-red-100 disabled:opacity-50"
            >
              {clearing ? "Clearing…" : "Clear"}
            </button>
            <button
              onClick={() => setConfirming(false)}
              disabled={clearing}
              className="rounded-md px-2 py-1 font-medium text-sand-500 transition hover:text-sand-700 disabled:opacity-50"
            >
              Cancel
            </button>
          </span>
        )}
      </div>
      {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
    </div>
  );
}
