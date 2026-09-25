import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, effectiveTheme, readSaved, setTheme } from "./theme";

afterEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});

describe("theme", () => {
  it("follows the OS until a choice is saved", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    expect(effectiveTheme()).toBe("dark");
    setTheme("light");
    expect(effectiveTheme()).toBe("light");
    vi.unstubAllGlobals();
  });

  it("stamps <html> and persists, so the CSS and the next visit both follow", () => {
    setTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
  });

  it("ignores a saved value that is not a theme", () => {
    localStorage.setItem(THEME_KEY, "sepia");
    expect(readSaved()).toBeNull();
  });
});
