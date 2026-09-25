import { describe, expect, it } from "vitest";
import {
  CATEGORY_COLORS,
  CAREER_PATH_COLOR,
  UNCATEGORIZED,
  categoryColor,
  themeCss,
} from "./categories";

// The palette is the single source of truth for category identity across the
// timeline, graph, and cards. These tests pin the contract that layer relies
// on — not the exact hex values (those come from the validated palette and are
// documented in categories.js), just that the mapping is total and stable.
describe("categoryColor", () => {
  it("returns the palette hue for each known category", () => {
    for (const [category, hue] of Object.entries(CATEGORY_COLORS)) {
      expect(categoryColor(category)).toBe(hue);
    }
  });

  it("falls back to the neutral ink for an unknown category", () => {
    // Same ink Uncategorized uses — an absence of identity, not a new hue.
    const neutral = categoryColor(UNCATEGORIZED);
    expect(categoryColor("NotACategory")).toBe(neutral);
    expect(categoryColor(undefined)).toBe(neutral);
    expect(categoryColor(null)).toBe(neutral);
  });

  it("does not hand out the reserved career-path slate as a category hue", () => {
    // Career Path is a node *type*, not a category (categories.js): no category
    // may resolve to its achromatic slate.
    for (const category of Object.keys(CATEGORY_COLORS)) {
      expect(categoryColor(category)).not.toBe(CAREER_PATH_COLOR);
    }
  });
});

describe("themeCss", () => {
  // Every mark color is a var() reference; a variable missing from either mode
  // paints nothing at all, silently, in that mode only.
  it("defines every variable the palette hands out, in both modes", () => {
    const css = themeCss();
    const [light, ...dark] = css.split(/@media|:root\[data-theme="dark"\]/);
    const names = [...Object.values(CATEGORY_COLORS), CAREER_PATH_COLOR].map(
      (v) => v.match(/var\((--[\w-]+)\)/)[1],
    );
    for (const name of names) {
      expect(light).toContain(`${name}:#`);
      for (const block of dark) expect(block).toContain(`${name}:#`);
    }
  });

  it("gives dark mode its own steps rather than reusing the light ones", () => {
    const css = themeCss();
    const light = css.match(/--mark-certifications:(#[0-9a-f]{6})/)[1];
    const dark = css.match(/data-theme="dark"\]\{[^}]*--mark-certifications:(#[0-9a-f]{6})/)[1];
    expect(dark).not.toBe(light);
  });
});
