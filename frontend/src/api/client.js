// Thin fetch wrapper around the TraceAI backend API.
// Requests go to /api/* and are proxied to FastAPI by Vite in dev.

async function handle(res) {
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = data?.detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

export async function uploadFile(file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/upload", { method: "POST", body: form });
  return handle(res);
}

export async function ingestUrl(url) {
  const res = await fetch("/api/ingest-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle(res);
}

export async function ingestText(text) {
  const res = await fetch("/api/ingest-text", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return handle(res);
}

export async function search(query, k = 5) {
  const res = await fetch("/api/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, k }),
  });
  return handle(res);
}

// RAG answer synthesis (Phase 7). Fired only for question-shaped queries
// (search response `answerable`), over the doc ids search already returned, so
// the answer is grounded in exactly the visible sources. Carries the item-B
// degradation contract (degraded_reason / retryable).
export async function answer(query, docIds) {
  const res = await fetch("/api/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, doc_ids: docIds }),
  });
  return handle(res);
}

export async function listDocuments() {
  const res = await fetch("/api/documents");
  return handle(res);
}

export async function getDocument(id) {
  const res = await fetch(`/api/documents/${id}`);
  return handle(res);
}

export async function recategorize(id) {
  const res = await fetch(`/api/documents/${id}/recategorize`, { method: "POST" });
  return handle(res);
}

export async function getGraph() {
  const res = await fetch("/api/graph");
  return handle(res);
}

// Career-path inference is a Gemini call, so it is manual-trigger (a button on
// the graph), not run on every graph read. The response carries the item-B
// degradation contract (degraded_reason / retryable) so the UI can offer a retry.
export async function inferCareerPaths() {
  const res = await fetch("/api/career-paths", { method: "POST" });
  return handle(res);
}

// Load the demo profile (plan.md §14) — a 10-document student journey, seeded
// server-side with no Gemini call. Idempotent: re-loading replaces the prior
// demo docs rather than duplicating them.
export async function seedDemo() {
  const res = await fetch("/api/seed-demo", { method: "POST" });
  return handle(res);
}

export async function health() {
  const res = await fetch("/api/health");
  return handle(res);
}
