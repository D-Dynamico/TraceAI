import { describe, expect, it } from "vitest";
import { formatLabel, formatMonth, knownDate } from "./cardParts";

// formatMonth must not invent a day: the backend stores year or year-month
// precision on purpose, so anything it can't parse as YYYY-MM passes through
// untouched rather than getting handed to Date().
describe("formatMonth", () => {
  it("renders a YYYY-MM as 'Mon YYYY'", () => {
    expect(formatMonth("2011-02")).toBe("Feb 2011");
    expect(formatMonth("2026-07")).toBe("Jul 2026");
    expect(formatMonth("2026-12")).toBe("Dec 2026");
  });

  it("passes a year-only value straight through", () => {
    expect(formatMonth("2011")).toBe("2011");
  });

  it("passes through anything it can't parse instead of inventing a date", () => {
    expect(formatMonth("not-a-date")).toBe("not-a-date");
    expect(formatMonth("2026-13")).toBe("2026-13"); // no 13th month
    expect(formatMonth("2026-00")).toBe("2026-00");
  });

  it("returns '' for non-strings", () => {
    expect(formatMonth(null)).toBe("");
    expect(formatMonth(undefined)).toBe("");
    expect(formatMonth(202607)).toBe("");
  });
});

// An assumed date must be flagged, not stated as fact in the meta line
// (plan.md § Risk Mitigation): knownDate returns null unless the date was
// actually extracted, so the caveat notice — not the meta line — carries it.
describe("knownDate", () => {
  it("formats an extracted date", () => {
    expect(knownDate({ date_source: "extracted", effective_date: "2024-03" })).toBe(
      "Mar 2024",
    );
  });

  it("returns null for an assumed date so the meta line stays silent", () => {
    expect(knownDate({ date_source: "assumed", effective_date: "2026-07" })).toBeNull();
  });

  it("returns null when there is no date info at all", () => {
    expect(knownDate({})).toBeNull();
    expect(knownDate(undefined)).toBeNull();
    expect(knownDate(null)).toBeNull();
  });
});

// Every row gets a format badge, including url/text_entry which have no file —
// so the mapping must be total.
describe("formatLabel", () => {
  it("maps known file types to their short badge", () => {
    expect(formatLabel("pdf")).toBe("PDF");
    expect(formatLabel("docx")).toBe("DOCX");
    expect(formatLabel("image")).toBe("IMG");
    expect(formatLabel("url")).toBe("URL");
    expect(formatLabel("text_entry")).toBe("TEXT");
  });

  it("uppercases an unmapped type", () => {
    expect(formatLabel("csv")).toBe("CSV");
  });

  it("falls back to DOC when the type is missing", () => {
    expect(formatLabel("")).toBe("DOC");
    expect(formatLabel(undefined)).toBe("DOC");
    expect(formatLabel(null)).toBe("DOC");
  });
});
