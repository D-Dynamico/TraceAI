import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Timeline from "./Timeline";
import * as client from "../api/client";

// Timeline sorts on effective_date only and groups by year, with unknown dates
// always last regardless of direction (CLAUDE.md: never read extracted_date
// raw). These render tests exercise that grouping through the real component so
// the date-fallback rule is covered end to end, not just in a helper.

function doc(id, overrides = {}) {
  return {
    id,
    title: id,
    category: "Projects",
    file_type: "text_entry",
    date_source: "extracted",
    has_original: false,
    ...overrides,
  };
}

function yearHeadings() {
  return screen
    .getAllByRole("heading", { level: 3 })
    .map((h) => h.textContent);
}

describe("Timeline grouping", () => {
  it("groups by year, newest first by default", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("a", { effective_date: "2023-05" }),
      doc("b", { effective_date: "2021-01" }),
      doc("c", { effective_date: "2023-11" }),
    ]);

    render(<Timeline />);
    await screen.findByText("a");
    expect(yearHeadings()).toEqual(["2023", "2021"]);
  });

  it("flips to oldest-first when the toggle is clicked", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("a", { effective_date: "2023-05" }),
      doc("b", { effective_date: "2021-01" }),
    ]);

    render(<Timeline />);
    await screen.findByText("a");
    await userEvent.click(screen.getByRole("button", { name: /newest first/i }));
    expect(yearHeadings()).toEqual(["2021", "2023"]);
  });

  it("puts undated documents in an 'Undated' group that sorts last both ways", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("dated", { effective_date: "2022-06" }),
      doc("unknown", { effective_date: null, date_source: "assumed" }),
    ]);

    render(<Timeline />);
    await screen.findByText("dated");
    // Newest-first: real year, then Undated.
    expect(yearHeadings()).toEqual(["2022", "Undated"]);

    await userEvent.click(screen.getByRole("button", { name: /newest first/i }));
    // Oldest-first: still Undated last, not first.
    expect(yearHeadings()).toEqual(["2022", "Undated"]);
  });

  it("only shows category chips for categories actually present", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("a", { effective_date: "2023-05", category: "Projects" }),
      doc("b", { effective_date: "2022-01", category: "Certifications" }),
    ]);

    render(<Timeline />);
    await screen.findByText("a");
    // Present categories get a filter chip; absent ones (e.g. Academics) do not.
    expect(screen.getByRole("button", { name: /^Projects$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Certifications$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Academics$/ })).toBeNull();
  });
});

describe("Timeline empty state", () => {
  it("offers the Load Demo Profile button when there are no documents", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([]);
    render(<Timeline />);
    expect(
      await screen.findByRole("button", { name: /load demo profile/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/your timeline is empty/i)).toBeInTheDocument();
  });

  it("filters to a single category when its chip is clicked", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("proj", { effective_date: "2023-05", category: "Projects" }),
      doc("cert", { effective_date: "2022-01", category: "Certifications" }),
    ]);

    render(<Timeline />);
    await screen.findByText("proj");
    await userEvent.click(screen.getByRole("button", { name: /^Certifications$/ }));

    await waitFor(() => expect(screen.queryByText("proj")).toBeNull());
    expect(screen.getByText("cert")).toBeInTheDocument();
  });
});
