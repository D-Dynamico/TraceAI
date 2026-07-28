import { beforeEach, describe, expect, it, vi } from "vitest";
import { getUserId, resetUserId } from "./userId";
import * as client from "./client";

// Per-browser identity. The deployed app is one URL with one dataset, so before
// this every visitor shared the first visitor's documents — the demo profile
// arrived pre-loaded, and uploads were readable by strangers.

describe("getUserId", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetUserId();
  });

  it("returns the same id across calls, so a reload keeps your documents", () => {
    // A per-session id would strand every upload the moment the tab closed —
    // worse than the shared state it replaces.
    expect(getUserId()).toBe(getUserId());
  });

  it("persists the id to localStorage rather than regenerating it", () => {
    const id = getUserId();
    expect(window.localStorage.getItem("traceai.userId")).toBe(id);
  });

  it("produces an id the backend's allowlist accepts", () => {
    // backend/identity.py rejects anything else and silently falls back to the
    // shared dataset, so a mismatch here would undo the whole feature quietly.
    expect(getUserId()).toMatch(/^[0-9a-f][0-9a-f-]{6,62}[0-9a-f]$/);
  });

  it("replaces a corrupted stored value instead of sending it", () => {
    window.localStorage.setItem("traceai.userId", "../../etc/passwd");
    const id = getUserId();
    expect(id).not.toBe("../../etc/passwd");
    expect(id).toMatch(/^[0-9a-f][0-9a-f-]{6,62}[0-9a-f]$/);
  });

  it("still yields a usable id when localStorage throws", () => {
    // Safari private mode, disabled storage, a sandboxed iframe. The visitor
    // gets a private dataset that does not survive a reload, which beats
    // throwing on page load.
    const spy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("storage disabled");
      });
    expect(getUserId()).toMatch(/^[0-9a-f]/);
    spy.mockRestore();
  });

  it("gives two browsers different ids", () => {
    const first = getUserId();
    window.localStorage.clear();
    resetUserId();
    expect(getUserId()).not.toBe(first);
  });
});

describe("every API call carries the identity header", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetUserId();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ nodes: [], edges: [], results: [], seeded: 0 }),
    });
  });

  // The failure mode this guards: one endpoint that forgets the header reads
  // and writes the *shared* dataset. It looks like working software until two
  // people use the app at once — exactly the bug being fixed.
  const calls = [
    ["listDocuments", () => client.listDocuments()],
    ["getDocument", () => client.getDocument("d1")],
    ["getGraph", () => client.getGraph()],
    ["search", () => client.search("certificates")],
    ["answer", () => client.answer("what?", ["d1"])],
    ["seedDemo", () => client.seedDemo()],
    ["inferCareerPaths", () => client.inferCareerPaths()],
    ["ingestUrl", () => client.ingestUrl("https://example.com")],
    ["ingestText", () => client.ingestText("led the club")],
    ["recategorize", () => client.recategorize("d1")],
    ["setCategory", () => client.setCategory("d1", "Projects")],
    ["deleteDocument", () => client.deleteDocument("d1")],
    ["uploadFile", () => client.uploadFile(new File(["x"], "a.txt"))],
  ];

  it.each(calls)("%s sends X-User-Id", async (_name, invoke) => {
    await invoke();
    const [, opts] = global.fetch.mock.calls[0];
    expect(opts.headers["X-User-Id"]).toBe(getUserId());
  });

  it("puts the id in the download href, which cannot send a header", () => {
    // A plain <a href> is a browser navigation; without ?u= every download
    // 404s for every visitor except the default one.
    expect(client.downloadUrl("d1")).toBe(
      `/api/documents/d1/download?u=${getUserId()}`,
    );
  });
});
