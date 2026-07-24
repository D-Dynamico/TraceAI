import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Search from "./Search";
import * as client from "../api/client";

// The search flow carries the plan.md §6 View 4 routing contract and the 40%
// retrieval criterion: filter queries go straight to a grid, question queries
// additionally get a RAG answer card grounded in exactly the sources search
// returned. The answer is a second, slower request — the sources must never
// wait on it, and a failed answer must never blank them (item B).

function filterResponse(overrides = {}) {
  return {
    mode: "filter",
    query: "show all my certificates",
    answerable: false,
    count: 1,
    category: "Certifications",
    results: [
      { id: "d1", title: "Python Cert", category: "Certifications", file_type: "url", has_original: false, source_url: "https://x", date_source: "extracted", effective_date: "2023-05" },
    ],
    ...overrides,
  };
}

function questionResponse(overrides = {}) {
  return {
    mode: "semantic",
    query: "how does my cert connect to my internship?",
    answerable: true,
    count: 2,
    results: [
      { id: "d1", title: "Python Cert", category: "Certifications", file_type: "url", has_original: false, source_url: "https://x", date_source: "extracted", effective_date: "2023-05" },
      { id: "d2", title: "XYZ Internship", category: "Internships", file_type: "pdf", has_original: true, date_source: "extracted", effective_date: "2025-06" },
    ],
    ...overrides,
  };
}

async function searchFor(text) {
  await userEvent.type(screen.getByRole("searchbox"), text);
  await userEvent.click(screen.getByRole("button", { name: /^search$/i }));
}

describe("Search routing", () => {
  it("filter query shows a grid and never asks for an answer", async () => {
    const search = vi.spyOn(client, "search").mockResolvedValue(filterResponse());
    const answer = vi.spyOn(client, "answer").mockResolvedValue({ answer: "nope" });

    render(<Search />);
    await searchFor("show all my certificates");

    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(await screen.findByText("Python Cert")).toBeInTheDocument();
    // A filter query is not answerable — no synthesis, no answer card.
    expect(answer).not.toHaveBeenCalled();
    expect(screen.queryByText(/^Answer$/)).not.toBeInTheDocument();
  });

  it("question query synthesizes over exactly the returned source ids", async () => {
    vi.spyOn(client, "search").mockResolvedValue(questionResponse());
    const answer = vi
      .spyOn(client, "answer")
      .mockResolvedValue({ answer: "The cert grounds the internship.", cited_doc_ids: ["d1"] });

    render(<Search />);
    await searchFor("how does my cert connect to my internship?");

    // Grounding: the answer is asked over the ids search returned, nothing else.
    await waitFor(() =>
      expect(answer).toHaveBeenCalledWith(
        "how does my cert connect to my internship?",
        ["d1", "d2"],
      ),
    );
    expect(await screen.findByText(/grounds the internship/i)).toBeInTheDocument();
  });

  it("paints the sources without waiting on the slower answer", async () => {
    vi.spyOn(client, "search").mockResolvedValue(questionResponse());
    // Answer stays pending — sources must already be on screen.
    vi.spyOn(client, "answer").mockReturnValue(new Promise(() => {}));

    render(<Search />);
    await searchFor("how does my cert connect to my internship?");

    expect(await screen.findByText("Python Cert")).toBeInTheDocument();
    expect(screen.getByText("XYZ Internship")).toBeInTheDocument();
  });

  it("degrades to sources-only when the answer request fails", async () => {
    vi.spyOn(client, "search").mockResolvedValue(questionResponse());
    vi.spyOn(client, "answer").mockRejectedValue(new Error("quota"));

    render(<Search />);
    await searchFor("how does my cert connect to my internship?");

    // The sources survive a failed synthesis — no blank, no crash.
    expect(await screen.findByText("Python Cert")).toBeInTheDocument();
    expect(screen.getByText("XYZ Internship")).toBeInTheDocument();
  });

  it("badges the source rows the answer cited", async () => {
    vi.spyOn(client, "search").mockResolvedValue(questionResponse());
    vi.spyOn(client, "answer").mockResolvedValue({ answer: "A.", cited_doc_ids: ["d2"] });

    render(<Search />);
    await searchFor("how does my cert connect to my internship?");

    // Exactly one row (d2) carries the cited badge.
    expect(await screen.findByText("cited")).toBeInTheDocument();
    expect(screen.getAllByText("cited")).toHaveLength(1);
  });
});

describe("Search suggested chips", () => {
  it("runs a suggested query on click and hides the chips after", async () => {
    const search = vi.spyOn(client, "search").mockResolvedValue(filterResponse());
    vi.spyOn(client, "answer").mockResolvedValue({});

    render(<Search />);
    // The chips are visible before any search.
    const chip = screen.getByRole("button", { name: "Show all my certificates" });
    await userEvent.click(chip);

    await waitFor(() =>
      expect(search).toHaveBeenCalledWith("Show all my certificates"),
    );
    // Once a response is in, the suggestion chips give way to the results.
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Show my latest resume" }),
      ).not.toBeInTheDocument(),
    );
  });
});
