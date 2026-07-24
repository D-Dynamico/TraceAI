import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AssumedDateNotice,
  Confidence,
  DegradedNotice,
  formatLabel,
  formatMonth,
  knownDate,
  OriginalAction,
} from "./cardParts";

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

// The confidence meter carries a rule, not just a number: confidence 0.0 is the
// categorizer's explicit couldn't-classify fallback (CLAUDE.md — categorize()
// never raises, degrades to 0.0), NOT "0% sure". So 0 must read as a warning,
// never as an empty meter that looks like a rendering bug.
describe("Confidence", () => {
  it("shows the unverified warning at 0, not an empty meter", () => {
    render(<Confidence value={0} />);
    expect(screen.getByText(/unverified — review suggested/i)).toBeInTheDocument();
    expect(screen.queryByRole("meter")).not.toBeInTheDocument();
  });

  it("renders a meter at the right percentage for a positive value", () => {
    render(<Confidence value={0.85} />);
    const meter = screen.getByRole("meter");
    expect(meter).toHaveAttribute("aria-valuenow", "85");
    expect(screen.getByText(/85% confident/i)).toBeInTheDocument();
  });

  it("renders nothing when there is no numeric confidence", () => {
    const { container } = render(<Confidence value={undefined} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// DegradedNotice behaves on the structured `retryable` flag, not the wording
// (deferred item B): a self-clearing failure is amber with a live retry; a
// terminal one is muted and points at the filename fallback, no retry.
describe("DegradedNotice", () => {
  it("offers a working retry for a retryable failure", async () => {
    const onRetry = vi.fn();
    render(
      <DegradedNotice cat={{ degraded_reason: "quota", retryable: true }} onRetry={onRetry} />,
    );
    expect(screen.getByText(/free AI quota is used up/i)).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /try again/i });
    await userEvent.click(button);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("offers no retry for a terminal failure and points at the filename", () => {
    render(<DegradedNotice cat={{ degraded_reason: "no_api_key", retryable: false }} onRetry={vi.fn()} />);
    expect(screen.getByText(/details below came from the filename/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("disables the retry while one is in flight", () => {
    render(
      <DegradedNotice
        cat={{ degraded_reason: "timeout", retryable: true }}
        onRetry={vi.fn()}
        retrying
      />,
    );
    expect(screen.getByRole("button", { name: /retrying/i })).toBeDisabled();
  });

  it("renders nothing when the categorization did not degrade", () => {
    const { container } = render(<DegradedNotice cat={{ confidence: 0.9 }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

// The assumed-date flag (plan.md § Risk Mitigation) shows only for an assumed
// date; an extracted date is fact and needs no caveat.
describe("AssumedDateNotice", () => {
  it("flags an assumed date", () => {
    render(<AssumedDateNotice cat={{ date_source: "assumed" }} />);
    expect(screen.getByText(/no date found/i)).toBeInTheDocument();
  });

  it("stays silent for an extracted or missing date_source", () => {
    const { container: a } = render(<AssumedDateNotice cat={{ date_source: "extracted" }} />);
    expect(a).toBeEmptyDOMElement();
    const { container: b } = render(<AssumedDateNotice cat={{}} />);
    expect(b).toBeEmptyDOMElement();
  });
});

// The format-preservation link (plan.md §1). Branches on `has_original`
// (authoritative from the backend), never re-derived from file_type here.
describe("OriginalAction", () => {
  it("downloads a stored original", () => {
    render(<OriginalAction id="doc1" hasOriginal sourceUrl={null} />);
    const link = screen.getByRole("link", { name: /download original/i });
    expect(link).toHaveAttribute("href", "/api/documents/doc1/download");
  });

  it("opens the live source when there is no stored file", () => {
    render(<OriginalAction id="doc2" hasOriginal={false} sourceUrl="https://ex.com/x" />);
    const link = screen.getByRole("link", { name: /open source/i });
    expect(link).toHaveAttribute("href", "https://ex.com/x");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("offers nothing for a text entry with neither file nor source", () => {
    const { container } = render(<OriginalAction id="doc3" hasOriginal={false} sourceUrl={null} />);
    expect(container).toBeEmptyDOMElement();
  });
});
