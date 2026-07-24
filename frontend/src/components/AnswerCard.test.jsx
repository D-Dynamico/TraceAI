import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AnswerCard from "./AnswerCard";

// The RAG answer card has three honest states and never a faked one (plan.md
// §6 View 4 / item B): a quota wall shows a notice and lets the sources carry
// the response — it must never invent an answer. These tests pin that.

describe("AnswerCard", () => {
  it("shows a synthesizing placeholder while loading", () => {
    render(<AnswerCard loading data={null} />);
    expect(screen.getByText(/synthesizing an answer/i)).toBeInTheDocument();
  });

  it("renders nothing before a query runs", () => {
    const { container } = render(<AnswerCard loading={false} data={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the answer with a source count", () => {
    render(
      <AnswerCard
        data={{ answer: "Your Python cert underpins the ML project.", cited_doc_ids: ["a", "b"] }}
      />,
    );
    expect(screen.getByText(/underpins the ML project/i)).toBeInTheDocument();
    expect(screen.getByText(/based on 2 sources/i)).toBeInTheDocument();
  });

  it("uses the singular for a single cited source", () => {
    render(<AnswerCard data={{ answer: "One source only.", cited_doc_ids: ["a"] }} />);
    expect(screen.getByText(/based on 1 source\b/i)).toBeInTheDocument();
  });

  it("omits the source line when nothing was cited", () => {
    render(<AnswerCard data={{ answer: "No citations.", cited_doc_ids: [] }} />);
    expect(screen.queryByText(/based on/i)).not.toBeInTheDocument();
  });

  it("never fabricates an answer on a degraded payload", () => {
    // Even if a bad payload carried answer text alongside a degraded reason, the
    // card must show the notice and NOT the answer — item B's whole point.
    render(
      <AnswerCard
        data={{ degraded_reason: "quota", retryable: true, answer: "FABRICATED ANSWER" }}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.queryByText(/FABRICATED ANSWER/)).not.toBeInTheDocument();
    expect(screen.getByText(/couldn.t synthesize an answer/i)).toBeInTheDocument();
    expect(screen.getByText(/matching sources are below/i)).toBeInTheDocument();
  });

  it("offers a working retry for a retryable degrade", async () => {
    const onRetry = vi.fn();
    render(
      <AnswerCard data={{ degraded_reason: "timeout", retryable: true }} onRetry={onRetry} />,
    );
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("offers no retry for a terminal degrade", () => {
    render(<AnswerCard data={{ degraded_reason: "no_api_key", retryable: false }} onRetry={vi.fn()} />);
    expect(screen.getByText(/no API key is configured/i)).toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("renders nothing when there is neither an answer nor a degrade", () => {
    const { container } = render(<AnswerCard data={{ answer: "" }} />);
    expect(container).toBeEmptyDOMElement();
  });
});
