/** @type {import('tailwindcss').Config} */

// Theme tokens — warm paper surfaces, espresso accent, warm neutral ink.
//
// The two scales below are NOT eyeballed. Each step was solved so its WCAG
// contrast against the surface matches the Tailwind slate / indigo step it
// replaced, so the retheme changes hue without changing how heavy any piece of
// text or any border reads. See the header comment in src/categories.js for the
// rule that governs the *category* hues, which are deliberately untouched here:
// they were validated against a white card, and re-running the validator on
// these warm surfaces keeps every check at its prior verdict.
//
// Two steps are deliberately NOT faithful ports:
//   - `sand.500` is solved against the card (5.29:1) and the page (4.71:1)
//     rather than against white, because it absorbs the old `slate-400`, which
//     was already failing the 4.5:1 AA floor at 2.56:1 and would have gone to
//     2.38:1 on a warm surface. It carries real text — "stars", "followers",
//     uppercase section labels — so it has to clear AA.
//   - `sand.400` therefore survives only as a border/decoration step. Do not
//     put text on it.

const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name, steps) =>
  Object.fromEntries(steps.map((step) => [step, v(`${name}-${step}`)]));

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      // Every value is a CSS variable from src/index.css, where the light and
      // dark steps live — so `bg-paper`, `text-sand-700` etc. switch mode with
      // no `dark:` variant anywhere in the components. The light values, and
      // the WCAG reasoning behind each step, are unchanged from before the
      // variables; see the comments in index.css for the dark ones.
      colors: {
        paper: v("paper"),
        parchment: v("parchment"),
        sand: scale("sand", [50, 100, 200, 300, 400, 500, 600, 700, 800, 900]),
        espresso: scale("espresso", [50, 100, 200, 300, 400, 500, 600, 700]),
        // Only the steps the notices use. Tailwind's own values in light mode,
        // re-stepped for the dark surface (see index.css).
        amber: scale("amber", [50, 100, 200, 300, 600, 700, 800]),
        red: scale("red", [50, 100, 200, 300, 600, 700]),
        // `text-white` is only ever ink on a strong fill (an espresso button,
        // the sand-900 tooltip). In dark mode those fills turn light, so the
        // ink has to turn dark with them.
        white: v("on-accent"),
      },
      fontFamily: {
        // Inter Tight for UI, Fraunces for the brand + headings. Both are
        // bundled by Vite via @fontsource-variable (see src/index.css) rather
        // than linked from Google's CDN, so the deployed app makes no
        // third-party request and cannot lose its type if that CDN is blocked.
        sans: [
          '"Inter Tight Variable"',
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
        display: ['"Fraunces Variable"', "ui-serif", "Georgia", "serif"],
      },
    },
  },
  plugins: [],
};
