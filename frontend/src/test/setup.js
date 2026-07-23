// Vitest global setup (Phase 9). Loaded once per test file via
// vite.config.js `setupFiles`.
//
// - jest-dom adds the `toBeDisabled` / `toHaveTextContent` matchers the
//   component tests read against.
// - global.fetch is left undefined by jsdom, so every test that touches the
//   API layer stubs it explicitly with `vi.fn()`. We only *reset* mocks here so
//   one test's fetch stub can't leak into the next.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
