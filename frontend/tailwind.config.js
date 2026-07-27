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

export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        // Surfaces. `paper` is the card, `parchment` the page behind it — the
        // page must stay the darker of the two or cards stop reading as raised.
        paper: "#faf6ef",
        parchment: "#efe9dd",

        // Warm neutral ink + fills (replaces slate).
        sand: {
          50: "#fcfaf6",
          100: "#f8f4ef",
          200: "#ede7dd",
          300: "#dcd3c4",
          400: "#aea08b", // borders/decoration only — 2.38:1, never text
          500: "#706556", // quiet text; AA on both paper and parchment
          600: "#554e44",
          700: "#443f38",
          800: "#2b2824",
          900: "#191715",
        },

        // Accent (replaces indigo). Achromatic-warm on purpose: it sits beside
        // the six category hues constantly, and a brown that reads as "chrome"
        // can never be mistaken for a category the way a chromatic accent
        // could. Same reasoning as CAREER_PATH_COLOR in src/categories.js.
        espresso: {
          50: "#f7f1eb",
          100: "#f0e6db",
          200: "#e1d1c1",
          300: "#c9b49f",
          400: "#ab9176",
          500: "#8f7254",
          600: "#765b40", // 5.84:1 as text on paper, 6.29:1 white-on for fills
          700: "#654d34",
        },
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
