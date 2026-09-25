import { describe, expect, it } from "vitest";
import { stepsFor } from "./ProcessingQueue";

// The strip may only claim what the browser observed. These pin the branches
// the Upload tests do not walk: degraded results, failures, and the kinds that
// have no upload step.

const states = (item) => Object.fromEntries(stepsFor(item).map((s) => [s.key, s.state]));
const labels = (item) => stepsFor(item).map((s) => s.label);

const done = (result, extra = {}) => ({
  kind: "file",
  phase: "done",
  result: { id: "d1", char_count: 120, categorization: {}, ...result },
  ...extra,
});

describe("stepsFor", () => {
  it("lights both server steps together — the client cannot see their boundary", () => {
    expect(states({ kind: "file", phase: "server" })).toEqual({
      upload: "done",
      extract: "active",
      categorize: "active",
      index: "waiting",
    });
  });

  it("flags a filename-fallback categorization instead of ticking it", () => {
    const s = stepsFor(done({ categorization: { degraded_reason: "quota_exhausted" } }));
    expect(s.find((x) => x.key === "categorize")).toMatchObject({
      state: "warn",
      label: "guessed from filename",
    });
  });

  it("flags an extraction that found no text, and skips indexing it", () => {
    expect(states(done({ char_count: 0 }))).toMatchObject({
      extract: "warn",
      index: "skipped",
    });
  });

  it("names OCR when the text came from it", () => {
    expect(labels(done({ used_ocr: true }))).toContain("extracted · OCR");
  });

  it("keeps indexing active until /status answers, then settles either way", () => {
    expect(states(done({})).index).toBe("active");
    expect(states(done({}, { indexed: true })).index).toBe("done");
    expect(states(done({}, { indexed: false })).index).toBe("warn");
  });

  it("marks the step that was running when the request failed", () => {
    expect(states({ kind: "file", phase: "failed", failedAt: "sending" })).toMatchObject({
      upload: "failed",
      extract: "waiting",
    });
    expect(states({ kind: "file", phase: "failed", failedAt: "server" })).toMatchObject({
      upload: "done",
      extract: "failed",
      categorize: "failed",
      index: "waiting",
    });
  });

  it("gives URLs a fetch step and text entries neither upload nor extract", () => {
    expect(Object.keys(states({ kind: "url", phase: "queued" }))).toEqual([
      "fetch",
      "categorize",
      "index",
    ]);
    expect(Object.keys(states({ kind: "text", phase: "queued" }))).toEqual([
      "categorize",
      "index",
    ]);
  });
});
