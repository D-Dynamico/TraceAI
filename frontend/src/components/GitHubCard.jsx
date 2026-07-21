// The GitHub result card — one component, two shapes (repo and profile).
//
// Separate from ResultCard on purpose. A repo has a star count, a language
// mix, and a homepage; a profile has a bio and a repo list; a certificate has
// none of these. Squeezing all three through one layout is what made a GitHub
// ingest render identically to a scraped blog post. The shared pieces — badge,
// confidence, chips, the assumed-date flag — come from cardParts.jsx, so this
// file owns arrangement only.
//
// Two constraints from the palette (see categories.js) hold here as well:
// category identity is always the dot *plus* the name, and no hue is used for
// anything that is not a category. Stars, languages, and repo counts are
// rendered in neutral ink — inventing a second color scale for languages would
// collide with the validated category hues.

import {
  AssumedDateNotice,
  CardShell,
  CategoryBadge,
  Confidence,
  DegradedNotice,
  EntityChips,
  ExtractedText,
  formatMonth,
  knownDate,
  Warnings,
} from "./cardParts";

const num = (value) =>
  typeof value === "number" ? value.toLocaleString() : null;

/** A dot-separated row of small facts. Nulls drop out. */
function Facts({ items }) {
  const shown = items.filter(Boolean);
  if (!shown.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-600">
      {shown.map((item, i) => (
        <span key={i} className="inline-flex items-center gap-1">
          {item}
        </span>
      ))}
    </div>
  );
}

/** Language mix as text, not a bar.
 *
 * A stacked bar would need a categorical color per language, and every hue
 * that reads clearly on white is already spoken for by a category. Text costs
 * nothing and cannot be misread as category identity.
 *
 * Shares are of the whole, but only the top few are listed, so they are not
 * expected to total 100%.
 */
function Languages({ items }) {
  if (!items?.length) return null;
  return (
    <p className="text-xs text-slate-500">
      {items.map((lang, i) => (
        <span key={lang.name}>
          {i > 0 && " · "}
          <span className="text-slate-700">{lang.name}</span> {lang.percent}%
        </span>
      ))}
    </p>
  );
}

/** An external link the *source* supplied.
 *
 * `href` has already been scheme-checked server-side by
 * `url_guard.safe_display_url` — a repo's homepage is free text its owner
 * controls, and React renders a `javascript:` href rather than blocking it.
 * `noopener noreferrer` covers the window.opener half.
 */
function ExternalLink({ href, children }) {
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="truncate text-indigo-600 transition hover:text-indigo-700 hover:underline"
    >
      {children} ↗
    </a>
  );
}

function RepoFacts({ details }) {
  const stars = num(details.stars);
  const forks = num(details.forks);

  return (
    <div className="mt-3 space-y-1.5 rounded-md bg-slate-50 px-3 py-2.5">
      <Facts
        items={[
          stars && (
            <>
              <span aria-hidden="true">★</span>
              <span className="tabular-nums">{stars}</span>
              <span className="text-slate-400">stars</span>
            </>
          ),
          forks && (
            <>
              <span className="tabular-nums">{forks}</span>
              <span className="text-slate-400">forks</span>
            </>
          ),
          details.license,
          details.pushed && (
            <span className="text-slate-500">
              active {formatMonth(details.pushed)}
            </span>
          ),
          details.archived && (
            <span className="font-medium text-amber-700">archived</span>
          ),
        ]}
      />
      <Languages items={details.languages} />
      {details.homepage && (
        <p className="truncate text-xs">
          <ExternalLink href={details.homepage}>
            {details.homepage.replace(/^https?:\/\//, "")}
          </ExternalLink>
        </p>
      )}
    </div>
  );
}

function ProfileFacts({ details }) {
  const listed = details.repos?.length || 0;
  const total = details.public_repos;
  // The list is capped for display. Saying only "10" would imply they have 10.
  const more = typeof total === "number" && total > listed;

  return (
    <div className="mt-3 space-y-2 rounded-md bg-slate-50 px-3 py-2.5">
      <Facts
        items={[
          typeof total === "number" && (
            <>
              <span className="tabular-nums">{total}</span>
              <span className="text-slate-400">
                {total === 1 ? "repo" : "repos"}
              </span>
            </>
          ),
          num(details.followers) && (
            <>
              <span className="tabular-nums">{num(details.followers)}</span>
              <span className="text-slate-400">followers</span>
            </>
          ),
          details.location,
          details.created && (
            <span className="text-slate-500">
              joined {formatMonth(details.created)}
            </span>
          ),
        ]}
      />
      <Languages items={details.languages} />
      {details.homepage && (
        <p className="truncate text-xs">
          <ExternalLink href={details.homepage}>
            {details.homepage.replace(/^https?:\/\//, "")}
          </ExternalLink>
        </p>
      )}

      {listed > 0 && (
        <ul className="space-y-1 border-t border-slate-200 pt-2">
          {details.repos.map((repo) => (
            <li
              key={repo.name}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="min-w-0 flex-1 truncate">
                <span className="font-medium text-slate-700">{repo.name}</span>
                {/* Descriptions are often empty — plenty of real repos have
                    none — so this must degrade to just the name. */}
                {repo.description && (
                  <span className="text-slate-500"> — {repo.description}</span>
                )}
              </span>
              <span className="shrink-0 text-slate-400">
                {repo.language}
                {repo.stars > 0 && (
                  <span className="tabular-nums"> · ★{repo.stars}</span>
                )}
              </span>
            </li>
          ))}
          {more && (
            <li className="pt-0.5 text-[11px] text-slate-400">
              showing {listed} of {total}
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

export default function GitHubCard({ result }) {
  const cat = result.categorization;
  const details = result.details || {};
  const isProfile = details.kind === "profile";

  const heading = cat?.title || result.title || result.url;
  const subtitle = isProfile
    ? details.login && `@${details.login}`
    : details.full_name;

  const meta = [
    isProfile ? "GitHub profile" : "GitHub repository",
    cat?.document_type,
    knownDate(cat),
  ].filter(Boolean);

  return (
    <CardShell>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-900">{heading}</p>
          {subtitle && (
            <p className="truncate text-xs text-slate-500">{subtitle}</p>
          )}
          {/* Wraps rather than truncates: at phone width truncation ate the
              date, which is the one field here the timeline depends on. */}
          <p className="mt-0.5 text-xs text-slate-500">{meta.join(" · ")}</p>
        </div>
        <div className="shrink-0">
          <CategoryBadge category={cat?.category} />
        </div>
      </div>

      {cat && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1">
          {cat.degraded_reason ? (
            <DegradedNotice cat={cat} />
          ) : (
            <Confidence value={cat.confidence} />
          )}
          <AssumedDateNotice cat={cat} />
        </div>
      )}

      {/* A profile's bio is the person's own words; show it above Gemini's
          summary rather than letting the summary paraphrase it away. */}
      {isProfile && details.bio && (
        <p className="mt-2 text-sm italic leading-relaxed text-slate-500">
          {details.bio}
        </p>
      )}

      {cat?.summary && (
        <p className="mt-2 text-sm leading-relaxed text-slate-600">
          {cat.summary}
        </p>
      )}

      {isProfile ? (
        <ProfileFacts details={details} />
      ) : (
        <RepoFacts details={details} />
      )}

      <EntityChips cat={cat} />

      <p className="mt-3 truncate text-[11px] text-slate-400" title={result.url}>
        from {result.url}
      </p>

      <Warnings items={result.warnings} />
      <ExtractedText
        text={result.text_preview}
        meta={`${result.char_count} chars`}
      />
    </CardShell>
  );
}
