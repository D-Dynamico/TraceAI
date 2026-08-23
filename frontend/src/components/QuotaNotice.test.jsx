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
    expect(notice).toHaveTextContent(/5 AI requests per minute/i);
    expect(notice).toHaveTextContent(/20 per day/i);
  });

  it("says the app keeps working past the limit, and that the demo is free", () => {
    render(<QuotaNotice />);
    const notice = screen.getByRole("complementary", { name: /ai usage limits/i });

    // Two separate promises, both real contracts in the backend, so the UI is
    // allowed to state them: every Gemini caller returns a structured reason
    // instead of raising, and an upload is stored before any AI step runs — so
    // "still works" and "your information remains safe" are distinct claims and
    // are pinned separately.
    expect(notice).toHaveTextContent(/the app still works/i);
    expect(notice).toHaveTextContent(/uploaded information remains safe/i);
    expect(notice).toHaveTextContent(/temporarily unavailable/i);
    // Phase 8's seed makes no Gemini call, which is what makes a live demo
    // possible at 20/day at all — worth saying where a reviewer will read it.
    expect(notice).toHaveTextContent(/demo profile anytime without using any AI requests/i);
  });
});
