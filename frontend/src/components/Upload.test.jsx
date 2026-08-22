import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Upload from "./Upload";
import * as client from "../api/client";

// The upload view has three independent inputs (file / URL / text), each routing
// to its own ingest call and reporting its own busy state — the deferred item-A
// contract: uploading files must not disable the URL or text input. These tests
// pin the routing and that independence; the per-card rendering is covered in
// cardParts / ResultCard.

const fileInput = () => document.querySelector('input[type="file"]');
const aFile = (name = "resume.pdf") =>
  new File(["résumé text"], name, { type: "application/pdf" });

function fileResult(overrides = {}) {
  return {
    id: "f1",
    filename: "resume.pdf",
    file_type: "pdf",
    char_count: 12,
    categorization: { category: "Academics", confidence: 0.9, title: "Final Resume" },
    ...overrides,
  };
}

describe("Upload routing", () => {
  it("sends a dropped/selected file through uploadFile and shows its card", async () => {
    const uploadFile = vi.spyOn(client, "uploadFile").mockResolvedValue(fileResult());
    render(<Upload />);

    await userEvent.upload(fileInput(), aFile());

    await waitFor(() => expect(uploadFile).toHaveBeenCalledTimes(1));
    expect(await screen.findByText("Final Resume")).toBeInTheDocument();
    expect(screen.getByText(/ingested \(1\)/i)).toBeInTheDocument();
  });

  it("sends the URL through ingestUrl and clears the field", async () => {
    const ingestUrl = vi
      .spyOn(client, "ingestUrl")
      .mockResolvedValue({ id: "u1", source_type: "web", categorization: { category: "Projects", confidence: 0.8, title: "Portfolio" } });
    render(<Upload />);

    const input = screen.getByPlaceholderText(/paste a github/i);
    await userEvent.type(input, "https://example.com");
    await userEvent.click(screen.getByRole("button", { name: /^ingest$/i }));

    await waitFor(() => expect(ingestUrl).toHaveBeenCalledWith("https://example.com"));
    expect(await screen.findByText("Portfolio")).toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("sends the written entry through ingestText", async () => {
    const ingestText = vi
      .spyOn(client, "ingestText")
      .mockResolvedValue({ id: "t1", categorization: { category: "Achievements", confidence: 0.7, title: "Club Lead" } });
    render(<Upload />);

    await userEvent.type(
      screen.getByPlaceholderText(/or just type it/i),
      "Led the Data Science Club in 2024",
    );
    await userEvent.click(screen.getByRole("button", { name: /add entry/i }));

    await waitFor(() =>
      expect(ingestText).toHaveBeenCalledWith("Led the Data Science Club in 2024"),
    );
    expect(await screen.findByText("Club Lead")).toBeInTheDocument();
  });

  it("keeps an empty URL submit a no-op (the Ingest button stays disabled)", async () => {
    const ingestUrl = vi.spyOn(client, "ingestUrl").mockResolvedValue({});
    render(<Upload />);

    const input = screen.getByPlaceholderText(/paste a github/i);
    await userEvent.type(input, "   "); // whitespace only
    expect(screen.getByRole("button", { name: /^ingest$/i })).toBeDisabled();
    expect(ingestUrl).not.toHaveBeenCalled();
  });
});

describe("Upload per-input independence (item A)", () => {
  it("does not disable the URL input while files are uploading", async () => {
    // uploadFile stays pending so the file upload is mid-flight.
    vi.spyOn(client, "uploadFile").mockReturnValue(new Promise(() => {}));
    render(<Upload />);

    // Type a URL first so the Ingest button's only remaining gate would be busy.
    await userEvent.type(screen.getByPlaceholderText(/paste a github/i), "https://example.com");
    await userEvent.upload(fileInput(), aFile());

    // Files are busy...
    await waitFor(() => expect(screen.getByText(/processing/i)).toBeInTheDocument());
    // ...but the URL input's own button is still enabled — no cross-disable.
    expect(screen.getByRole("button", { name: /^ingest$/i })).toBeEnabled();
  });
});

describe("Upload progress + errors", () => {
  it("shows a pending skeleton while in flight, then replaces it with the card", async () => {
    let release;
    vi.spyOn(client, "uploadFile").mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve(fileResult({ categorization: { category: "Academics", confidence: 0.9, title: "Final Resume" } }));
      }),
    );
    render(<Upload />);

    await userEvent.upload(fileInput(), aFile());
    expect(await screen.findByText(/categorizing…/i)).toBeInTheDocument();

    release();
    expect(await screen.findByText("Final Resume")).toBeInTheDocument();
    expect(screen.queryByText(/categorizing…/i)).not.toBeInTheDocument();
  });

  it("surfaces a failed upload in the error banner", async () => {
    vi.spyOn(client, "uploadFile").mockRejectedValue(new Error("Upload failed (500)"));
    render(<Upload />);

    await userEvent.upload(fileInput(), aFile("broken.pdf"));

    expect(await screen.findByText(/broken\.pdf: Upload failed \(500\)/i)).toBeInTheDocument();
  });
});

describe("reaching the upload controls without a pointer", () => {
  it("makes the drop zone a real button", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    render(<Upload />);

    const zone = screen.getByRole("button", { name: /choose files/i });
    expect(zone.getAttribute("tabindex")).toBe("0");

    // Enter must reach the hidden <input type="file"> the div stands in for.
    const input = document.querySelector('input[type="file"]');
    const click = vi.spyOn(input, "click").mockImplementation(() => {});
    zone.focus();
    await userEvent.keyboard("{Enter}");

    expect(click).toHaveBeenCalled();
  });

  it("names the URL and text inputs, which had only placeholders", () => {
    render(<Upload />);

    expect(screen.getByLabelText(/portfolio URL/i)).toBeTruthy();
    expect(screen.getByLabelText(/achievement/i)).toBeTruthy();
  });
})
