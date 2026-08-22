// Lint config, added because the codebase was already relying on one that was
// not installed: KnowledgeGraph.jsx carried an `eslint-disable-next-line
// react-hooks/exhaustive-deps` for a rule nothing enforced — on the file with
// the largest effect in the app, which is exactly where a missing dependency
// bites.
//
// Deliberately small. The point is the two rule sets that catch real bugs
// (hooks) and real typos (no-undef), not a style opinion; formatting is left
// alone so this can be adopted without a reformat commit.
import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: { ...globals.browser, ...globals.node },
    },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      // Without this, every component imported only for JSX reads as unused.
      "react/jsx-uses-vars": "error",
      // The new JSX transform means React need not be in scope, and props are
      // validated by the backend contract rather than PropTypes here.
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // Vitest globals (`describe`, `it`, `expect`, `vi`) come from
    // `test.globals: true` in vite.config.js, not from an import.
    files: ["**/*.test.{js,jsx}", "src/test/**"],
    languageOptions: { globals: { ...globals.vitest } },
  },
];
