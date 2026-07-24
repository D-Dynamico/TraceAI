import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GitHubCard from "./GitHubCard";
import Upload from "./Upload";
import * as client from "../api/client";

// GitHubCard is one component with two shapes — a repo (stars, languages,
// homepage) and a profile (bio, repo list) — because a repo and a profile carry
// fields a certificate does not (that's why it's separate from ResultCard). The
// shared badge/confidence/date pieces come from cardParts (tested there); this
// file pins the two arrangements, the repo-list cap disclosure, and the dispatch
// that picks this card over ResultCard.

function repoResult(overrides = {}) {
  return {
    id: "g1",
    url: "https://github.com/octocat/Hello-World",
    categorization: { category: "Projects", confidence: 0.85, title: "Hello-World" },
    details: {
      full_name: "octocat/Hello-World",
      stars: 1280,
      forks: 342,
      license: "MIT",
      pushed: "2025-04",
      languages: [
        { name: "JavaScript", percent: 62 },
        { name: "CSS", percent: 20 },
      ],
      homepage: "https://example.com/demo",
    },
    ...overrides,
  };
}

function profileResult(overrides = {}) {
  return {
    id: "g2",
    url: "https://github.com/octocat",
    categorization: { category: "Projects", confidence: 0.8, title: "octocat" },
    details: {
      kind: "profile",
      login: "octocat",
      bio: "Building things in the open.",
      public_repos: 12,
      followers: 4200,
      created: "2011-01",
      repos: [
        { name: "Hello-World", description: "My first repo", language: "JS", stars: 3 },
        { name: "spoon-knife", description: "", language: "HTML", stars: 0 },
      ],
    },
    ...overrides,
  };
}

describe("GitHubCard repo shape", () => {
  it("renders as a repository with its full name", () => {
    render(<GitHubCard result={repoResult()} />);
    expect(screen.getByText(/GitHub repository/)).toBeInTheDocument();
    expect(screen.getByText("octocat/Hello-World")).toBeInTheDocument();
  });

  it("formats star and fork counts with locale separators", () => {
    render(<GitHubCard result={repoResult()} />);
    // Computed against the same runtime so this can't be locale-flaky.
    expect(screen.getByText((1280).toLocaleString())).toBeInTheDocument();
    expect(screen.getByText((342).toLocaleString())).toBeInTheDocument();
  });

  it("lists the language mix as text and links the homepage safely", () => {
    render(<GitHubCard result={repoResult()} />);
    expect(screen.getByText("JavaScript")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /example\.com\/demo/i });
    expect(link).toHaveAttribute("href", "https://example.com/demo");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("flags an archived repo", () => {
    render(<GitHubCard result={repoResult({ details: { ...repoResult().details, archived: true } })} />);
    expect(screen.getByText(/archived/i)).toBeInTheDocument();
  });
});

describe("GitHubCard profile shape", () => {
  it("renders as a profile with the @handle and bio", () => {
    render(<GitHubCard result={profileResult()} />);
    expect(screen.getByText(/GitHub profile/)).toBeInTheDocument();
    expect(screen.getByText("@octocat")).toBeInTheDocument();
    expect(screen.getByText(/building things in the open/i)).toBeInTheDocument();
  });

  it("lists the repos and pluralizes the repo count", () => {
    render(<GitHubCard result={profileResult()} />);
    expect(screen.getByText("Hello-World")).toBeInTheDocument();
    expect(screen.getByText("spoon-knife")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();
    expect(screen.getByText("repos")).toBeInTheDocument();
  });

  it("discloses that the repo list is capped when more exist than shown", () => {
    // public_repos 12, only 2 listed — saying "12" without this would imply the
    // list is complete.
    render(<GitHubCard result={profileResult()} />);
    expect(screen.getByText(/showing 2 of 12/i)).toBeInTheDocument();
  });

  it("does not disclose a cap when the list is complete", () => {
    render(<GitHubCard result={profileResult({ details: { ...profileResult().details, public_repos: 2 } })} />);
    expect(screen.queryByText(/showing/i)).not.toBeInTheDocument();
  });
});

describe("GitHubCard retry (item B)", () => {
  it("re-categorizes in place and drops the stale warning", async () => {
    const recategorize = vi.spyOn(client, "recategorize").mockResolvedValue({
      category: "Projects",
      confidence: 0.93,
      title: "Hello-World",
    });
    render(
      <GitHubCard
        result={repoResult({
          categorization: { degraded_reason: "quota", retryable: true, confidence: 0 },
          warnings: ["categorization unverified — review suggested"],
        })}
      />,
    );

    expect(screen.getByText(/review suggested/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /try again/i }));

    await waitFor(() => expect(recategorize).toHaveBeenCalledWith("g1"));
    expect(await screen.findByText(/93% confident/i)).toBeInTheDocument();
    expect(screen.queryByText(/review suggested/i)).not.toBeInTheDocument();
  });
});

// The dispatch deferred from block C: Upload's Result picker sends a github
// source to this card and everything else to ResultCard.
describe("Upload dispatches GitHub results to GitHubCard", () => {
  it("renders a github-typed URL result as a GitHubCard", async () => {
    vi.spyOn(client, "ingestUrl").mockResolvedValue({
      id: "g9",
      source_type: "github",
      url: "https://github.com/octocat/Hello-World",
      details: { full_name: "octocat/Hello-World", stars: 5 },
      categorization: { category: "Projects", confidence: 0.8, title: "Hello-World" },
    });
    render(<Upload />);

    await userEvent.type(screen.getByPlaceholderText(/paste a github/i), "https://github.com/octocat/Hello-World");
    await userEvent.click(screen.getByRole("button", { name: /^ingest$/i }));

    // The GitHub-only meta proves GitHubCard, not ResultCard, was chosen.
    expect(await screen.findByText(/GitHub repository/)).toBeInTheDocument();
  });

  it("renders a generic URL result as a plain ResultCard", async () => {
    vi.spyOn(client, "ingestUrl").mockResolvedValue({
      id: "w9",
      source_type: "web",
      url: "https://example.com/blog",
      categorization: { category: "Projects", confidence: 0.8, title: "A Blog Post" },
    });
    render(<Upload />);

    await userEvent.type(screen.getByPlaceholderText(/paste a github/i), "https://example.com/blog");
    await userEvent.click(screen.getByRole("button", { name: /^ingest$/i }));

    expect(await screen.findByText("A Blog Post")).toBeInTheDocument();
    expect(screen.queryByText(/GitHub repository/)).not.toBeInTheDocument();
  });
});
