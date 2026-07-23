import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoadDemoButton from "./LoadDemoButton";
import * as client from "../api/client";

// The button's whole reason for existing is the *visible change*: seed, then
// call the host's refetch so the empty view fills in. These tests pin that
// contract and the double-click guard, so a refactor can't quietly turn it back
// into a silent no-op.

describe("LoadDemoButton", () => {
  it("seeds, then calls onLoaded so the host view refetches", async () => {
    const seed = vi.spyOn(client, "seedDemo").mockResolvedValue({ inserted: 10 });
    const onLoaded = vi.fn().mockResolvedValue(undefined);

    render(<LoadDemoButton onLoaded={onLoaded} />);
    await userEvent.click(screen.getByRole("button", { name: /load demo profile/i }));

    await waitFor(() => expect(onLoaded).toHaveBeenCalledTimes(1));
    expect(seed).toHaveBeenCalledTimes(1);
    // Order matters: the refetch must run after the seed resolves.
    expect(seed.mock.invocationCallOrder[0]).toBeLessThan(
      onLoaded.mock.invocationCallOrder[0],
    );
  });

  it("disables while in flight and re-enables after", async () => {
    let release;
    vi.spyOn(client, "seedDemo").mockReturnValue(
      new Promise((resolve) => {
        release = () => resolve({ inserted: 10 });
      }),
    );
    const onLoaded = vi.fn().mockResolvedValue(undefined);

    render(<LoadDemoButton onLoaded={onLoaded} />);
    const button = screen.getByRole("button");
    await userEvent.click(button);

    // Mid-flight: disabled and showing the loading label.
    expect(button).toBeDisabled();
    expect(button).toHaveTextContent(/loading demo/i);

    release();
    await waitFor(() => expect(button).toBeEnabled());
    expect(button).toHaveTextContent(/load demo profile/i);
  });

  it("shows the backend error and does not refetch when the seed fails", async () => {
    vi.spyOn(client, "seedDemo").mockRejectedValue(new Error("Seed failed (500)"));
    const onLoaded = vi.fn();

    render(<LoadDemoButton onLoaded={onLoaded} />);
    await userEvent.click(screen.getByRole("button"));

    expect(await screen.findByText("Seed failed (500)")).toBeInTheDocument();
    expect(onLoaded).not.toHaveBeenCalled();
    // Re-enabled so the user can retry.
    expect(screen.getByRole("button")).toBeEnabled();
  });
});
