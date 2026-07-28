import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResultCard from "./ResultCard";
import * as client from "../api/client";

// ResultCard mostly composes cardParts primitives (tested there); what lives
// *here* is the wiring the primitives can't see — the item-B retry flow that
// re-categorizes preserved text in place, and the file-vs-fileless branch for
// the format-preservation download.

function degradedFile(overrides = {}) {
  return {
    id: "doc1",
    kind: "file",
    filename: "resume.pdf",
    file_type: "pdf",
    char_count: 1200,
    checksum: "abc123def456abc123def456",
    warnings: ["categorization unverified — review suggested"],
    categorization: {
      degraded_reason: "quota",
      retryable: true,
      category: null,
      confidence: 0,
    },
    ...overrides,
  };
}

describe("ResultCard retry (item B)", () => {
  it("re-categorizes preserved text in place and drops the stale warning", async () => {
    const recategorize = vi.spyOn(client, "recategorize").mockResolvedValue({
      category: "Academics",
      confidence: 0.92,
      title: "Final-year Resume",
      summary: "Resume with six projects.",
      document_type: "resume",
      date_source: "extracted",
      effective_date: "2026-01",
    });

    render(<ResultCard result={degradedFile()} />);

    // Before: degraded, and the "review suggested" warning is on the card.
    expect(screen.getByText(/free AI quota is used up/i)).toBeInTheDocument();
    expect(screen.getByText(/review suggested/i)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    // The retry re-runs categorization over the preserved text (no re-upload).
    await waitFor(() => expect(recategorize).toHaveBeenCalledWith("doc1"));

    // After: the new categorization replaces the shown one; the meter tells the
    // story, so the stale "unverified — review suggested" warning is gone.
    expect(await screen.findByText(/92% confident/i)).toBeInTheDocument();
    expect(screen.getByText("Academics")).toBeInTheDocument();
    expect(screen.queryByText(/review suggested/i)).not.toBeInTheDocument();
  });

  it("leaves the degraded card as-is when the retry transport fails", async () => {
    vi.spyOn(client, "recategorize").mockRejectedValue(new Error("network"));
    render(<ResultCard result={degradedFile()} />);

    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    // categorize() never raises server-side; a transport error here just leaves
    // the degraded notice standing rather than throwing.
    expect(await screen.findByText(/free AI quota is used up/i)).toBeInTheDocument();
  });
});

describe("ResultCard format preservation", () => {
  it("offers a download + checksum line for an uploaded file", () => {
    render(
      <ResultCard
        result={{
          id: "doc9",
          kind: "file",
          filename: "cert.pdf",
          file_type: "pdf",
          char_count: 300,
          checksum: "0123456789abcdef0123456789abcdef",
          categorization: { category: "Certifications", confidence: 0.9, title: "Cert" },
        }}
      />,
    );
    expect(
      screen.getByRole("link", { name: /download original/i }).getAttribute("href"),
    ).toMatch(/^\/api\/documents\/doc9\/download\?u=.+/);
    expect(screen.getByText(/original preserved/i)).toBeInTheDocument();
  });

  it("offers no file download for a written text entry", () => {
    render(
      <ResultCard
        result={{
          id: "doc10",
          kind: "text",
          filename: "Led the Data Science Club",
          char_count: 40,
          categorization: { category: "Achievements", confidence: 0.8, title: "Club lead" },
        }}
      />,
    );
    expect(screen.queryByRole("link", { name: /download original/i })).not.toBeInTheDocument();
  });
});
