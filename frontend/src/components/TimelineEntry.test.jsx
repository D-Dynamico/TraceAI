import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TimelineEntry from "./TimelineEntry";
import * as client from "../api/client";

// Deleting a document is destructive, so it is gated behind an inline confirm
// and, on success, tells the parent to refetch (which unmounts the entry).
// These tests pin the confirm gate and the notify-parent contract.
//
// The category badge is asserted *within* its own group, never against the
// whole entry: the picker renders a chip per category, so a bare
// getAllByText("Projects") passes on the chip even when the badge has wrongly
// moved. That made the failed-save test hollow — an optimistic update that
// showed a category the server rejected left the suite green.

const badge = () => within(screen.getByRole("group", { name: "Category" }));

function doc(overrides = {}) {
  return {
    id: "doc1",
    title: "Test Doc",
    category: "Projects",
    file_type: "pdf",
    has_original: true,
    date_source: "extracted",
    effective_date: "2024-03",
    summary: "A summary.",
    ...overrides,
  };
}

async function expand() {
  // Expanding fetches the detail record (skills/tags); stub it.
  vi.spyOn(client, "getDocument").mockResolvedValue({
    skills: [],
    organizations: [],
    tags: [],
  });
  await userEvent.click(screen.getByText("Test Doc"));
}

describe("TimelineEntry category override", () => {
  it("relabels the entry and tells the parent to refetch", async () => {
    const patch = vi
      .spyOn(client, "setCategory")
      .mockResolvedValue({ id: "doc1", category: "Achievements", category_source: "manual" });
    const onUpdated = vi.fn();
    render(<TimelineEntry doc={doc()} onUpdated={onUpdated} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /change/i }));
    await userEvent.click(screen.getByRole("button", { name: /achievements/i }));

    await waitFor(() => expect(patch).toHaveBeenCalledWith("doc1", "Achievements"));
    // The badge must move immediately, not wait on the parent's refetch.
    await waitFor(() => expect(badge().getByText("Achievements")).toBeInTheDocument());
    expect(badge().queryByText("Projects")).not.toBeInTheDocument();
    // The parent regroups and rebuilds its filter chips from the listing.
    expect(onUpdated).toHaveBeenCalledWith("doc1");
  });

  it("marks a manual category as the user's, not the AI's", async () => {
    render(<TimelineEntry doc={doc({ category_source: "manual" })} />);
    await expand();

    expect(await screen.findByText(/set by you/i)).toBeInTheDocument();
  });

  it("does not claim the user chose an AI category", async () => {
    render(<TimelineEntry doc={doc({ category_source: "ai" })} />);
    await expand();

    expect(await screen.findByRole("button", { name: /change/i })).toBeInTheDocument();
    expect(screen.queryByText(/set by you/i)).not.toBeInTheDocument();
  });

  it("never offers Uncategorized as a choice", async () => {
    // It is the categorizer's couldn't-tell fallback, and the backend rejects
    // it — a picker that offered it would produce a guaranteed 400.
    render(<TimelineEntry doc={doc()} />);
    await expand();
    await userEvent.click(await screen.findByRole("button", { name: /change/i }));

    expect(screen.queryByRole("button", { name: /uncategorized/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /certifications/i })).toBeInTheDocument();
  });

  it("keeps the old category and shows the error when the save fails", async () => {
    vi.spyOn(client, "setCategory").mockRejectedValue(new Error("Request failed (500)"));
    const onUpdated = vi.fn();
    render(<TimelineEntry doc={doc()} onUpdated={onUpdated} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /change/i }));
    await userEvent.click(screen.getByRole("button", { name: /achievements/i }));

    expect(await screen.findByText("Request failed (500)")).toBeInTheDocument();
    // The badge must not show a category the server never accepted — asserted
    // inside the badge group, because the open picker still renders a chip for
    // every category and would satisfy a document-wide query either way.
    expect(badge().getByText("Projects")).toBeInTheDocument();
    expect(badge().queryByText("Achievements")).not.toBeInTheDocument();
    expect(onUpdated).not.toHaveBeenCalled();
  });

  it("picking the category it already has does not call the API", async () => {
    const patch = vi.spyOn(client, "setCategory").mockResolvedValue({});
    render(<TimelineEntry doc={doc()} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /change/i }));
    await userEvent.click(screen.getByRole("button", { name: /^projects$/i }));

    expect(patch).not.toHaveBeenCalled();
  });
});

describe("TimelineEntry delete", () => {
  it("requires a confirm before it deletes", async () => {
    const del = vi.spyOn(client, "deleteDocument").mockResolvedValue({ deleted: true });
    render(<TimelineEntry doc={doc()} onDeleted={vi.fn()} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));

    // The first click only asks — it must not have deleted anything yet.
    expect(del).not.toHaveBeenCalled();
    expect(screen.getByText(/delete this\?/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /cancel/i })).toBeInTheDocument();
  });

  it("cancel aborts without deleting", async () => {
    const del = vi.spyOn(client, "deleteDocument").mockResolvedValue({ deleted: true });
    render(<TimelineEntry doc={doc()} onDeleted={vi.fn()} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(del).not.toHaveBeenCalled();
    expect(screen.queryByText(/delete this\?/i)).not.toBeInTheDocument();
  });

  it("confirming deletes and notifies the parent to refetch", async () => {
    const del = vi.spyOn(client, "deleteDocument").mockResolvedValue({ deleted: true });
    const onDeleted = vi.fn();
    render(<TimelineEntry doc={doc()} onDeleted={onDeleted} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i })); // confirm

    await waitFor(() => expect(del).toHaveBeenCalledWith("doc1"));
    await waitFor(() => expect(onDeleted).toHaveBeenCalledWith("doc1"));
  });

  it("keeps the item and shows an error when the delete fails", async () => {
    vi.spyOn(client, "deleteDocument").mockRejectedValue(new Error("Delete failed (500)"));
    const onDeleted = vi.fn();
    render(<TimelineEntry doc={doc()} onDeleted={onDeleted} />);
    await expand();

    await userEvent.click(await screen.findByRole("button", { name: /^delete$/i }));
    await userEvent.click(screen.getByRole("button", { name: /^delete$/i })); // confirm

    expect(await screen.findByText("Delete failed (500)")).toBeInTheDocument();
    // A failed delete must not tell the parent the item is gone.
    expect(onDeleted).not.toHaveBeenCalled();
  });
});
