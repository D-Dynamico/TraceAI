import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  answer,
  ingestText,
  search,
  seedDemo,
  uploadFile,
} from "./client";

// The API layer is a thin fetch wrapper; the logic worth testing is `handle`:
// how it surfaces backend errors and what it sends. Every test stubs
// global.fetch — nothing here touches the network.

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    json: async () => body,
  };
}

beforeEach(() => {
  global.fetch = vi.fn();
});

describe("handle (error contract)", () => {
  it("resolves to the parsed JSON on a 2xx", async () => {
    fetch.mockResolvedValue(jsonResponse({ answerable: true, results: [] }));
    await expect(search("hi")).resolves.toEqual({ answerable: true, results: [] });
  });

  it("throws the backend `detail` message on a non-2xx", async () => {
    fetch.mockResolvedValue(
      jsonResponse({ detail: "That URL resolves to a private address" }, {
        ok: false,
        status: 400,
      }),
    );
    await expect(ingestText("x")).rejects.toThrow(
      "That URL resolves to a private address",
    );
  });

  it("falls back to a status message when there is no `detail`", async () => {
    fetch.mockResolvedValue(jsonResponse({}, { ok: false, status: 503 }));
    await expect(search("hi")).rejects.toThrow("Request failed (503)");
  });

  it("survives a non-JSON body without throwing a parse error", async () => {
    // handle() swallows a json() rejection into {}, so a 500 with an HTML body
    // still surfaces as the clean status message, not a SyntaxError.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON");
      },
    });
    await expect(search("hi")).rejects.toThrow("Request failed (500)");
  });
});

describe("request shapes", () => {
  it("search posts the query and default k", async () => {
    fetch.mockResolvedValue(jsonResponse({ results: [] }));
    await search("python");
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/api/search");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ query: "python", k: 5 });
  });

  it("answer posts the query and the exact doc ids search returned", async () => {
    fetch.mockResolvedValue(jsonResponse({ answer: "…" }));
    await answer("when did I learn SQL?", ["demo-3", "demo-7"]);
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/api/answer");
    expect(JSON.parse(opts.body)).toEqual({
      query: "when did I learn SQL?",
      doc_ids: ["demo-3", "demo-7"],
    });
  });

  it("seedDemo POSTs with no body", async () => {
    fetch.mockResolvedValue(jsonResponse({ inserted: 10 }));
    await seedDemo();
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/api/seed-demo");
    expect(opts.method).toBe("POST");
    expect(opts.body).toBeUndefined();
  });

  it("uploadFile sends multipart form data, not JSON", async () => {
    fetch.mockResolvedValue(jsonResponse({ id: "abc" }));
    const file = new File(["hi"], "resume.pdf", { type: "application/pdf" });
    await uploadFile(file);
    const [url, opts] = fetch.mock.calls[0];
    expect(url).toBe("/api/upload");
    expect(opts.body).toBeInstanceOf(FormData);
    expect(opts.body.get("file")).toBe(file);
    // Still no Content-Type — the browser sets the multipart boundary itself,
    // and setting it by hand produces a body the server cannot parse. The
    // identity header rides along, so `headers` is no longer absent entirely;
    // the rule that matters is that Content-Type is not among them.
    expect(opts.headers["Content-Type"]).toBeUndefined();
    expect(opts.headers["X-User-Id"]).toBeTruthy();
  });
});
