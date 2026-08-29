import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DemoNotice from "./DemoNotice";
import * as client from "../api/client";

// This button exists because the demo persists in the visitor's dataset — once
// seeded, the empty state never comes back on its own. The contract worth
// pinning is the confirm gate (it destroys rows) and the refetch afterwards.

describe("DemoNotice", () => {
  it("requires a second click before it clears", async () => {
    const clear = vi.spyOn(client, "clearDemo").mockResolvedValue({ cleared: 10 });
    const onCleared = vi.fn().mockResolvedValue(undefined);

    render(<DemoNotice onCleared={onCleared} />);
    await userEvent.click(screen.getByRole("button", { name: /clear demo/i }));

    expect(clear).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: /^clear$/i }));

    await waitFor(() => expect(clear).toHaveBeenCalledTimes(1));
    // Order matters: the host refetches only after the delete resolves.
    expect(clear.mock.invocationCallOrder[0]).toBeLessThan(
      onCleared.mock.invocationCallOrder[0],
    );
  });

  it("cancel backs out without clearing", async () => {
    const clear = vi.spyOn(client, "clearDemo");

    render(<DemoNotice onCleared={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /clear demo/i }));
    await userEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(clear).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /clear demo/i })).toBeInTheDocument();
  });

  it("shows the error and does not refetch when the delete fails", async () => {
    vi.spyOn(client, "clearDemo").mockRejectedValue(new Error("Clear failed (500)"));
    const onCleared = vi.fn();

    render(<DemoNotice onCleared={onCleared} />);
    await userEvent.click(screen.getByRole("button", { name: /clear demo/i }));
    await userEvent.click(screen.getByRole("button", { name: /^clear$/i }));

    expect(await screen.findByText("Clear failed (500)")).toBeInTheDocument();
    expect(onCleared).not.toHaveBeenCalled();
    // Still on the confirm step, so the user can retry.
    expect(screen.getByRole("button", { name: /^clear$/i })).toBeEnabled();
  });
});
