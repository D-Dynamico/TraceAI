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
// If you change a hue, re-run the validator rather than trusting that it looks
// fine. Two candidate orderings failed outright: magenta for Skills collides
// with Academics red (normal-vision ΔE 13.2, a hard fail), and orange collides
// with both green and red (ΔE 3.2 / 7.1).

export const UNCATEGORIZED = "Uncategorized";

// Muted ink, not a palette slot — "no category yet" is an absence of identity,
// so it deliberately does not get a hue.
const NEUTRAL = "#898781";

export const CATEGORY_COLORS = {
  Certifications: "#2a78d6", // blue
  Projects: "#008300", // green
  Internships: "#eda100", // yellow
  Achievements: "#4a3aa7", // violet
  Academics: "#e34948", // red
  Skills: "#1baf7a", // aqua
  [UNCATEGORIZED]: NEUTRAL,
};

export function categoryColor(category) {
  return CATEGORY_COLORS[category] || NEUTRAL;
}

// Sequential blue ramp, used for the confidence meter. Track is the near-zero
// step; fill is the same hue stepped dark enough to read on white.
export const METER_TRACK = "#cde2fb";
export const METER_FILL = "#2a78d6";
