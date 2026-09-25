// Category identity colors — the single source of truth for the whole app.
//
// Lives outside any component because the timeline (Module 4) and the knowledge
// graph (Module 3) must color a category identically to the way an upload card
// does. A category that is blue here and green there is a bug the user notices
// immediately.
//
// The hues follow plan.md §4 Module 4, which prescribes blue/green/yellow/
// violet/red for Certifications/Projects/Internships/Achievements/Academics.
// Skills is not listed there and takes the remaining slot.
//
// The exact steps are not eyeballed. They come from a validated categorical
// palette and were checked with the dataviz palette validator against this
// app's white card surface, all pairs:
//
//   Lightness band  PASS   Chroma floor  PASS   Normal-vision floor  PASS (15.6)
//   CVD separation  WARN — worst pair Skills↔Academics ΔE 6.9 (deutan)
//   Contrast        WARN — Internships (2.17) and Skills (2.82) sit below 3:1
//
// Both WARNs are conditional passes, and the conditions are met by design:
//
//   - A CVD ΔE in the 6–8 band is only legal with secondary encoding. Every
//     badge renders the category *name* as text, so color never carries
//     identity alone.
//   - Sub-3:1 contrast requires "relief" — visible labels. Same mechanism: the
//     hue appears only as a dot beside dark ink, never as the text color and
//     never as a fill the reader must decode.
//
// Dark mode is SELECTED, not flipped: each hue has its own dark step, found by
// a constrained search (same hue ±15° in OKLCH, the validator's dark L band)
// and then checked with the validator, --mode dark --pairs all, against both
// dark surfaces (card #211e1b, page #171513):
//
//   Lightness band  PASS   Chroma floor  PASS   Normal-vision floor  PASS (16.2)
//   CVD separation  WARN — worst pair Internships↔Projects ΔE 7.4 (protan)
//   Contrast        PASS — all six ≥ 3:1, so dark needs less relief than light
//
// The light steps' own dark column (#3987e5 blue, #9085e9 violet, …) was tried
// first and FAILED here: with only six slots in play all-pairs, blue↔violet fell
// to normal-vision ΔE 9.8 and aqua↔green to 11.9.
//
// If you change a hue, re-run the validator rather than trusting that it looks
// fine. Two candidate orderings failed outright: magenta for Skills collides
// with Academics red (normal-vision ΔE 13.2, a hard fail), and orange collides
// with both green and red (ΔE 3.2 / 7.1).

export const UNCATEGORIZED = "Uncategorized";

// Every mark color, as [light, dark]. The rest of the app never sees these hex
// values: it gets `var(--…)` references (below), and `themeCss()` defines the
// variables once for each mode, so a theme switch repaints every dot, node and
// meter without a React re-render and without any component knowing about it.
const MARKS = {
  certifications: ["#2a78d6", "#6a8cce"], // blue
  projects: ["#008300", "#337b15"], // green
  internships: ["#eda100", "#b8830c"], // yellow
  achievements: ["#4a3aa7", "#884dec"], // violet
  academics: ["#e34948", "#bf4d5c"], // red
  skills: ["#1baf7a", "#16aa8b"], // aqua
  // Muted ink, not a palette slot — "no category yet" is an absence of
  // identity, so it deliberately does not get a hue.
  neutral: ["#898781", "#8f887e"],
  // See CAREER_PATH_COLOR. Dark is a light warm grey at the same job: 9.81:1 on
  // the dark card, achromatic, and clearly not one of the six.
  career: ["#453f38", "#cfc6b8"],
};

const mark = (name) => `var(--mark-${name})`;

/** The CSS that defines every `--mark-*` variable, light and dark.
 *
 * Injected once at startup (main.jsx). Dark applies under the OS setting unless
 * the page is stamped `data-theme="light"`, and always when stamped "dark" —
 * so the in-app toggle wins in both directions.
 */
export function themeCss() {
  const block = (i) =>
    Object.entries(MARKS)
      .map(([name, pair]) => `--mark-${name}:${pair[i]};`)
      .join("");
  return (
    `:root{${block(0)}}` +
    `@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){${block(1)}}}` +
    `:root[data-theme="dark"]{${block(1)}}`
  );
}

const NEUTRAL = mark("neutral");

export const CATEGORY_COLORS = {
  Certifications: mark("certifications"),
  Projects: mark("projects"),
  Internships: mark("internships"),
  Achievements: mark("achievements"),
  Academics: mark("academics"),
  Skills: mark("skills"),
  [UNCATEGORIZED]: NEUTRAL,
};

export function categoryColor(category) {
  return CATEGORY_COLORS[category] || NEUTRAL;
}

// The six-category taxonomy (plan.md §4 Module 2) in palette order — what a
// user may pick when overriding the AI's choice. UNCATEGORIZED is deliberately
// absent: it is the categorizer's "couldn't tell" fallback, not a category
// anyone means to file something under. The backend enforces the same set, so a
// picker built from this list can never offer something a PATCH would reject.
export const CATEGORY_CHOICES = Object.keys(CATEGORY_COLORS).filter(
  (c) => c !== UNCATEGORIZED,
);

// Career Path (knowledge graph, plan.md §6 View 3) is a node *type*, not a
// category, and it deliberately does NOT get a 7th categorical hue. The palette
// validator was run with the six category hues plus every plausible candidate
// (rose, magenta, teal, orange, deep purple) under --pairs all: each FAILED —
// either normal-vision ΔE < 15 against red/blue, or a CVD collision with violet
// (#a21caf↔violet ΔE 2.1) or green. The six categories saturate the usable hue
// space on white, so a 7th categorical hue cannot pass. Per the dataviz rule for
// exactly this case, Career Path is encoded compositely instead: a reserved dark
// slate (achromatic — reads as "a different kind of thing", not a category),
// plus a larger node, right-side placement, and a mandatory title + match-%
// label. Identity never rests on this color alone.
//
// Ported warm with the rest of the theme: the requirement is that it be
// *achromatic* (so it cannot read as a category), not that it be cool, and a
// cool slate was the one dark tone left looking blue-grey against warm paper.
// Solved to the same contrast the old #334155 had on this surface — 9.64:1
// against 9.61:1 — so a career-path node kept its exact visual weight, and the
// near-black selection ring still separates from it by the prior margin.
export const CAREER_PATH_COLOR = mark("career");

// Structural colors that have to exist as JS values: SVG strokes/fills and
// inline `style` cannot take a Tailwind class. They are the theme's own
// variables (src/index.css), not copies of them, so they follow the mode.
// Apply them through `style`, not SVG presentation attributes — var() is not
// reliably resolved in an attribute.
const token = (name) => `rgb(var(--${name}))`;
export const SURFACE_PAPER = token("paper"); // the card surface; also the ring that separates overlapping graph nodes
export const GRAPH_EDGE = token("sand-300"); // recessive by design
export const GRAPH_EDGE_ACTIVE = token("sand-600");
export const GRAPH_NODE_SELECTED = token("sand-900");

// The confidence meter. This is a track plus a fill, NOT a two-step sequential
// ramp — the distinction matters because the ordinal validator judges a ramp of
// data *marks*, where the lightest step must itself read as a mark, and it
// therefore FAILs a groove by design. (It failed the old cool track on white
// too, at 1.32:1, so that verdict long predated the warm theme.)
//
// The track is the groove and takes a surface color; the fill is the datum and
// keeps the Certifications blue, which clears 3:1 on paper. The track moved
// from a cool #cde2fb to sand-300 because a blue-white groove read as a cold
// patch on warm paper — the one place the theme change altered a measured
// value rather than just its hue.
export const METER_TRACK = token("sand-300");
export const METER_FILL = mark("certifications");
