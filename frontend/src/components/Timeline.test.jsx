import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Timeline from "./Timeline";
import { COLD_START_AFTER_MS } from "./ColdStartNotice";
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

// A cold start on the free instance can hold the first list request for tens of
// seconds. The timeline hands over the Load Demo CTA once the wait is clearly
// abnormal rather than making the user watch "Loading…" for it.
describe("Timeline cold start", () => {
  afterEach(() => vi.useRealTimers());

  it("shows Load Demo while the first request is still in flight", async () => {
    vi.useFakeTimers();
    vi.spyOn(client, "listDocuments").mockReturnValue(new Promise(() => {}));

    render(<Timeline />);
    expect(screen.queryByRole("button", { name: /load demo profile/i })).toBeNull();

    act(() => vi.advanceTimersByTime(COLD_START_AFTER_MS));

    expect(
      screen.getByRole("button", { name: /load demo profile/i }),
    ).toBeInTheDocument();
    // Never claims the timeline is empty — the request has not answered yet.
    expect(screen.getByText(/waking the server/i)).toBeInTheDocument();
    expect(screen.queryByText(/your timeline is empty/i)).toBeNull();
  });

  it("does not flash the CTA for a fast response", async () => {
    vi.spyOn(client, "listDocuments").mockResolvedValue([
      doc("a", { effective_date: "2023-05" }),
    ]);

    render(<Timeline />);
    expect(screen.queryByRole("button", { name: /load demo profile/i })).toBeNull();
    await screen.findByText("a");
  });

  it("ignores a stale list response that lands after a seed", async () => {
    // The cold-start list request resolves empty *after* the seed's refetch —
    // without the request-id guard it would blank the freshly seeded timeline.
    let resolveFirst;
    vi.spyOn(client, "listDocuments")
      .mockImplementationOnce(() => new Promise((r) => (resolveFirst = r)))
      .mockResolvedValue([doc("seeded", { effective_date: "2024-01" })]);
    vi.spyOn(client, "seedDemo").mockResolvedValue({ created: 10 });

    vi.useFakeTimers();
    render(<Timeline />);
    // Fake timers only to skip the cold-start delay; userEvent below wants real
    // ones back.
    act(() => vi.advanceTimersByTime(COLD_START_AFTER_MS));
    vi.useRealTimers();

    await userEvent.click(screen.getByRole("button", { name: /load demo profile/i }));
    await screen.findByText("seeded");

    resolveFirst([]);
    await waitFor(() => expect(screen.getByText("seeded")).toBeInTheDocument());
    expect(screen.queryByText(/your timeline is empty/i)).toBeNull();
  });
});
