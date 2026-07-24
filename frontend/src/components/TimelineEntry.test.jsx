import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TimelineEntry from "./TimelineEntry";
import * as client from "../api/client";

// Deleting a document is destructive, so it is gated behind an inline confirm
// and, on success, tells the parent to refetch (which unmounts the entry).
// These tests pin the confirm gate and the notify-parent contract.

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
