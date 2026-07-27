import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import QuotaNotice from "./QuotaNotice";

// The notice exists to be *accurate*, so the numbers are what these tests pin.
// Both were measured from live 429 payloads rather than read from Google's
// docs; a well-meaning edit to "10 RPM / 1500 per day" (the figures this repo
// wrongly assumed until 2026-07-25) would be a false claim about the deployed
// app, and would turn one of these red.

describe("QuotaNotice", () => {
  it("names the free tier and both real limits", () => {
    render(<QuotaNotice />);
    const notice = screen.getByRole("complementary", { name: /ai usage limits/i });

    expect(notice).toHaveTextContent(/free\s+Gemini tier/i);
    expect(notice).toHaveTextContent(/5 requests per minute/i);
    expect(notice).toHaveTextContent(/20 per day/i);
  });

  it("says the limit degrades rather than breaks, and that the demo is free", () => {
    render(<QuotaNotice />);
    const notice = screen.getByRole("complementary", { name: /ai usage limits/i });

    // The degradation promise is a real contract in the backend (every Gemini
    // caller returns a structured reason instead of raising), so the UI is
    // allowed to state it.
    expect(notice).toHaveTextContent(/degrade/i);
    // Phase 8's seed makes no Gemini call, which is what makes a live demo
    // possible at 20/day at all — worth saying where a reviewer will read it.
    expect(notice).toHaveTextContent(/demo profile uses no AI calls/i);
  });
});
