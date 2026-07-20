// Pieces every result card shares, whatever it is a card *of*.
//
// ResultCard and GitHubCard deliberately do not share a layout — a repo has
// fields a certificate does not, and forcing one arrangement to serve both was
// the thing that made GitHub ingestion feel like an afterthought. What they do
// share is everything below: the category badge, the confidence meter, the
// entity chips, and the date. Those carry rules (palette constraints, the
// plan.md §10 assumed-date flag) that must not be re-derived per card.

import { categoryColor, METER_FILL, METER_TRACK } from "../categories";

/** Category identity: a colored dot plus the name.
 *
 * The name is always rendered, never implied by the dot alone — the palette's
 * CVD separation is only valid alongside this label (see categories.js), and a
 * legend-free badge would otherwise be color-only identity.
 */
export function CategoryBadge({ category }) {
  if (!category) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-white px-2.5 py-0.5 text-xs font-medium text-slate-700">
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ backgroundColor: categoryColor(category) }}
      />
      {category}
    </span>
  );
}

/** A single ratio against a limit — a meter, not a chart.
 *
 * Confidence 0.0 is not "0% sure", it is the categorizer's explicit
 * couldn't-classify fallback, so it gets a labelled warning instead of an
 * empty track that reads as a rendering bug.
 */
export function Confidence({ value }) {
  if (typeof value !== "number") return null;

  if (value === 0) {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-amber-700">
        <span aria-hidden="true">⚠</span> Unverified — review suggested
      </span>
    );
  }

  const pct = Math.round(value * 100);
  return (
    <span className="inline-flex items-center gap-2">
      <span
        role="meter"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Categorization confidence"
        className="block h-1.5 w-16 overflow-hidden rounded-full"
        style={{ backgroundColor: METER_TRACK }}
      >
        <span
          className="block h-full rounded-full"
          style={{ width: `${pct}%`, backgroundColor: METER_FILL }}
        />
      </span>
      <span className="text-xs tabular-nums text-slate-500">{pct}% confident</span>
    </span>
  );
}

/** Extracted entities. Muted ink so they never compete with the category. */
export function Chips({ label, items }) {
  if (!items?.length) return null;
  return (
    <div className="flex flex-wrap items-baseline gap-1.5">
      <span className="text-[11px] uppercase tracking-wide text-slate-400">
        {label}
      </span>
      {items.map((item) => (
        <span
          key={item}
          className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-700"
        >
          {item}
        </span>
      ))}
    </div>
  );
}

export function EntityChips({ cat }) {
  if (!cat) return null;
  return (
    <div className="mt-3 space-y-1.5">
      <Chips label="Skills" items={cat.skills} />
      <Chips label="Orgs" items={cat.organizations} />
      <Chips label="People" items={cat.people} />
      <Chips label="Tags" items={cat.tags} />
    </div>
  );
}

/** The assumed-date flag (plan.md §10).
 *
 * Reads `date_source` from the backend rather than inferring it from a missing
 * `date`. The two agree today, but the fallback is resolved in exactly one
 * place server-side (`database.resolve_date`) precisely so a second consumer
 * cannot apply it while forgetting the "flag it" half. Recomputing it here is
 * how the timeline re-acquires the bug it was built to avoid.
 *
 * Only worth saying while the document is fresh in mind — on the timeline it
 * will just look like a document from today.
 */
export function AssumedDateNotice({ cat }) {
  if (cat?.date_source !== "assumed") return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-amber-700">
      <span aria-hidden="true">⚠</span> No date found — will show as today on
      your timeline
    </span>
  );
}

/** Warnings from scraping, extraction, or categorization. */
export function Warnings({ items }) {
  if (!items?.length) return null;
  return (
    <ul className="mt-2 space-y-1">
      {items.map((w, i) => (
        <li key={i} className="text-xs text-amber-600">
          ⚠ {w}
        </li>
      ))}
    </ul>
  );
}

/** The raw extraction, collapsed.
 *
 * This used to be an always-open <pre> and was the card's visual conclusion,
 * which made every ingest look like debug output — for a GitHub repo it is
 * literally the scraper's field dump. It stays reachable because it is the
 * only way to tell "Gemini misread this" from "the scraper got nothing", but
 * it is no longer the last thing you read.
 *
 * Note this is `text_preview` (800 chars), not the full `raw_text`.
 */
export function ExtractedText({ text, meta }) {
  if (!text) return null;
  return (
    <details className="mt-3 group">
      <summary className="cursor-pointer list-none text-[11px] text-slate-400 transition hover:text-slate-600">
        <span className="inline-block transition group-open:rotate-90">▸</span>{" "}
        Extracted text{meta ? ` · ${meta}` : ""}
      </summary>
      <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-3 text-xs text-slate-700">
        {text}
      </pre>
    </details>
  );
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2011-02" -> "Feb 2011"; "2011" -> "2011". Passes anything else through.
 *
 * The backend stores year or year-month precision deliberately (a repo's
 * creation date does not know which day the work started), so this must not
 * invent a day by handing the string to `Date`.
 */
export function formatMonth(value) {
  if (typeof value !== "string") return "";
  const match = /^(\d{4})-(\d{2})$/.exec(value);
  if (!match) return value;
  const month = MONTHS[Number(match[2]) - 1];
  return month ? `${month} ${match[1]}` : value;
}

/** The date for a card's meta line, or null if we only assumed it.
 *
 * plan.md §10 requires an assumed date be *flagged*, not merely filled.
 * Printing "Jul 2026" in the meta line states it as fact; the caveat sitting
 * in a separate amber line does not undo that, it just contradicts it. So an
 * assumed date is left out here entirely and AssumedDateNotice carries the
 * whole story.
 */
export function knownDate(cat) {
  if (cat?.date_source !== "extracted") return null;
  return formatMonth(cat.effective_date);
}

/** Card shell — the border/padding every result shares. */
export function CardShell({ children }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      {children}
    </div>
  );
}
