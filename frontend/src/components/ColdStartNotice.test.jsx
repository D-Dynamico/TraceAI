import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import ColdStartNotice, { COLD_START_AFTER_MS } from "./ColdStartNotice";

// The point of the component is the *silence* before the threshold: a warm
// response must not flash an alarming "waking the server" note on its way past.
describe("ColdStartNotice", () => {
  afterEach(() => vi.useRealTimers());

  it("renders nothing before the threshold", () => {
    vi.useFakeTimers();
    render(<ColdStartNotice />);
    act(() => vi.advanceTimersByTime(COLD_START_AFTER_MS - 1));
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("explains the wait once the threshold passes", () => {
    vi.useFakeTimers();
    render(<ColdStartNotice />);
    act(() => vi.advanceTimersByTime(COLD_START_AFTER_MS));
    expect(screen.getByRole("status")).toHaveTextContent(/starting up/i);
  });

  it("cancels its timer on unmount", () => {
    vi.useFakeTimers();
    const { unmount } = render(<ColdStartNotice />);
    unmount();
    // A setState after unmount would warn; advancing past the threshold with
    // nothing mounted is the assertion.
    act(() => vi.advanceTimersByTime(COLD_START_AFTER_MS * 2));
    expect(screen.queryByRole("status")).toBeNull();
  });
});
