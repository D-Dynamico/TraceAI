import { useEffect, useState } from "react";

// Why this exists: the API is a Render free instance, which spins down after
// ~15 minutes idle (render.yaml explains why there is deliberately no keep-warm
// ping). The first request of a visit therefore blocks on a container cold
// start — tens of seconds — and a bare "Loading…" during that reads as a hung
// app, especially on the timeline, where the Load Demo CTA everyone is looking
// for used to appear only once that request resolved.
//
// Nothing here makes the backend faster. Both exports stay silent for a
// normal-speed response and only speak up once the wait has clearly left that
// range, so the warm case is untouched and the cold case is explained.
export const COLD_START_AFTER_MS = 3000;

/** True once a request has been in flight long enough to be a cold start.
 *
 * Separate from the notice because the timeline does more than annotate: it
 * swaps in the Load Demo CTA, so it needs the flag, not the copy.
 */
export function useColdStart(afterMs = COLD_START_AFTER_MS) {
  const [slow, setSlow] = useState(false);

  useEffect(() => {
    const timer = setTimeout(() => setSlow(true), afterMs);
    return () => clearTimeout(timer);
  }, [afterMs]);

  return slow;
}

export default function ColdStartNotice({ afterMs = COLD_START_AFTER_MS }) {
  const slow = useColdStart(afterMs);
  if (!slow) return null;

  return (
    <p
      role="status"
      className="mx-auto max-w-sm text-center text-xs leading-relaxed text-sand-500"
    >
      Starting up — the first load can take a minute. Thanks for waiting.
    </p>
  );
}
