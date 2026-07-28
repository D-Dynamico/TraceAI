import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import SourceRow from "./SourceRow";

// A search result row. The rule it carries is the § Risk Mitigation assumed-date
// contract at the row level: an extracted date is shown as fact, an assumed one
// is labelled and never printed as if it were real. Plus the cited state and the
// download/open branch it wires to OriginalAction.

function baseResult(overrides = {}) {
  return {
    id: "doc1",
    title: "Machine Learning Pipeline",
    category: "Projects",
    file_type: "pdf",
    has_original: true,
    date_source: "extracted",
    effective_date: "2024-03",
    ...overrides,
  };
}

describe("SourceRow date handling", () => {
  it("prints an extracted date as fact", () => {
    render(<SourceRow result={baseResult()} />);
    expect(screen.getByText("Mar 2024")).toBeInTheDocument();
    expect(screen.queryByText(/date assumed/i)).not.toBeInTheDocument();
  });

  it("labels an assumed date instead of printing it", () => {
    render(
      <SourceRow result={baseResult({ date_source: "assumed", effective_date: "2026-07" })} />,
    );
    expect(screen.getByText(/date assumed/i)).toBeInTheDocument();
    // The assumed value itself must not be stated as if real.
    expect(screen.queryByText("Jul 2026")).not.toBeInTheDocument();
  });
});

describe("SourceRow cited state", () => {
  it("badges a cited source", () => {
    render(<SourceRow result={baseResult()} cited />);
    expect(screen.getByText("cited")).toBeInTheDocument();
  });

  it("shows no cited badge otherwise", () => {
    render(<SourceRow result={baseResult()} />);
    expect(screen.queryByText("cited")).not.toBeInTheDocument();
  });
});

describe("SourceRow original action", () => {
  it("downloads a stored original", () => {
    render(<SourceRow result={baseResult({ has_original: true })} />);
    expect(
      screen.getByRole("link", { name: /download original/i }).getAttribute("href"),
    ).toMatch(/^\/api\/documents\/doc1\/download\?u=.+/);
  });

  it("opens the source when there is no stored file", () => {
    render(
      <SourceRow
        result={baseResult({ has_original: false, source_url: "https://ex.com/p", file_type: "url" })}
      />,
    );
    expect(screen.getByRole("link", { name: /open source/i })).toHaveAttribute(
      "href",
      "https://ex.com/p",
    );
  });
});

describe("SourceRow score", () => {
  it("renders a relevance percentage when scored", () => {
    render(<SourceRow result={baseResult({ score: 0.87 })} />);
    expect(screen.getByText(/87% match/i)).toBeInTheDocument();
  });
});
