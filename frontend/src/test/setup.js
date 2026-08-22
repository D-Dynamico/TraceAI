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

// Node 22+ defines its own `localStorage` global, gated behind the
// --localstorage-file flag, and without that flag it reads back as `undefined`.
// It is defined on globalThis *before* the jsdom environment populates it, so
// jsdom's real Storage never lands and `window.localStorage` is undefined —
// which took out every test in api/userId.test.js the moment the local Node was
// new enough, on a suite nobody had changed. (Confirmed against vitest 2 and 4,
// and jsdom 26 and 29: it is Node's global, not either of theirs.)
//
// So install a Storage here, unconditionally, matching the Web Storage API the
// app actually uses: getItem / setItem / removeItem / clear. `api/userId.js`
// wraps every access in try/catch precisely because a browser can refuse
// storage, so the surface this has to cover is small and well defined.
if (!globalThis.localStorage) {
  const store = new Map();
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: {
      getItem: (key) => (store.has(String(key)) ? store.get(String(key)) : null),
      setItem: (key, value) => store.set(String(key), String(value)),
      removeItem: (key) => store.delete(String(key)),
      clear: () => store.clear(),
      key: (i) => [...store.keys()][i] ?? null,
      get length() {
        return store.size;
      },
    },
  });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
