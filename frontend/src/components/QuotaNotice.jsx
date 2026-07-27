// A standing, honest disclosure that the AI here is the free tier.
//
// Rendered once in App.jsx below the active view, so it is present on all four
// screens without any of them having to carry it. This is the ONE place the app
// mentions quota: per-action warnings ("· costs quota" on the career-path
// button) were removed because a reviewer does not need to be reminded at every
// click — they need to know the ceiling once, up front, and to understand that
// hitting it degrades rather than breaks.
//
// The numbers are not a guess and not from Google's docs, which no longer
// publish per-model free-tier limits. Both were measured from live 429 payloads
// on 2026-07-25 (`GenerateRequestsPerMinutePerProjectPerModel-FreeTier` →
// limit 5; `GenerateRequestsPerDayPerProjectPerModel-FreeTier` → limit 20, model
// gemini-3-flash). If they ever change, plan.md §2, README, and CLAUDE.md carry
// the same pair and must move together.

const RPM = 5;
const RPD = 20;

export default function QuotaNotice() {
  return (
    <aside
      aria-label="AI usage limits"
      className="mx-auto mb-8 max-w-3xl px-6"
    >
      <div className="flex gap-2.5 rounded-lg border border-sand-300 bg-sand-200 px-3.5 py-2.5 text-xs leading-relaxed text-sand-600">
        <span
          aria-hidden="true"
          className="mt-px shrink-0 font-semibold text-espresso-600"
        >
          ⓘ
        </span>
        <p>
          Runs on the <strong className="font-semibold text-sand-700">free
          Gemini tier</strong> —{" "}
          <strong className="font-semibold text-sand-700">
            {RPM} requests per minute
          </strong>{" "}
          and{" "}
          <strong className="font-semibold text-sand-700">
            {RPD} per day
          </strong>
          . Categorization, career paths, and AI answers degrade gracefully
          rather than fail once that is spent, and nothing you have already
          uploaded is lost. Loading the demo profile uses no AI calls at all.
        </p>
      </div>
    </aside>
  );
}
