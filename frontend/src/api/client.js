// Thin fetch wrapper around the TraceAI backend API.
// Requests go to /api/* and are proxied to FastAPI by Vite in dev.

// Where the API lives. Empty in dev and in tests, so every request stays a
// same-origin /api/* path and Vite's proxy (vite.config.js) forwards it to
// :8000 — unchanged behaviour. Deployed, the frontend is on Vercel and the API
// on Render, a different origin, so VITE_API_URL supplies it at build time.
// Vite inlines import.meta.env at build, so this cannot be changed after the
// fact: rebuild the frontend to repoint it.
const BASE = (import.meta.env?.VITE_API_URL ?? "").replace(/\/+$/, "");

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
  const res = await fetch(`${BASE}/api/upload`, { method: "POST", body: form });
  return handle(res);
}

export async function ingestUrl(url) {
  const res = await fetch(`${BASE}/api/ingest-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle(res);
}

export async function ingestText(text) {
  const res = await fetch(`${BASE}/api/ingest-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return handle(res);
}

export async function search(query, k = 5) {
  const res = await fetch(`${BASE}/api/search`, {
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
  const res = await fetch(`${BASE}/api/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, doc_ids: docIds }),
  });
  return handle(res);
}

export async function listDocuments() {
  const res = await fetch(`${BASE}/api/documents`);
  return handle(res);
}

export async function getDocument(id) {
  const res = await fetch(`${BASE}/api/documents/${id}`);
  return handle(res);
}

export async function recategorize(id) {
  const res = await fetch(`${BASE}/api/documents/${id}/recategorize`, {
    method: "POST",
  });
  return handle(res);
}

// Manually override a document's category (plan.md § Risk Mitigation). PATCH,
// because it updates one field of the document — it does not re-run Gemini
// (that is recategorize) and does not touch the original or the extracted text.
// The server accepts only the six taxonomy categories and marks the result
// `category_source: "manual"`.
export async function setCategory(id, category) {
  const res = await fetch(`${BASE}/api/documents/${id}/category`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category }),
  });
  return handle(res);
}

// Hard-delete a document from every store (SQLite, the vector index, and the
// original file + sidecar for an uploaded file). Scoped to the user server-side.
export async function deleteDocument(id) {
  const res = await fetch(`${BASE}/api/documents/${id}`, { method: "DELETE" });
  return handle(res);
}

export async function getGraph() {
  const res = await fetch(`${BASE}/api/graph`);
  return handle(res);
}

// Career-path inference is a Gemini call, so it is manual-trigger (a button on
// the graph), not run on every graph read. The response carries the item-B
// degradation contract (degraded_reason / retryable) so the UI can offer a retry.
export async function inferCareerPaths() {
  const res = await fetch(`${BASE}/api/career-paths`, { method: "POST" });
  return handle(res);
}

// Load the demo profile (plan.md §14) — a 10-document student journey, seeded
// server-side with no Gemini call. Idempotent: re-loading replaces the prior
// demo docs rather than duplicating them.
export async function seedDemo() {
  const res = await fetch(`${BASE}/api/seed-demo`, { method: "POST" });
  return handle(res);
}

export async function health() {
  const res = await fetch(`${BASE}/api/health`);
  return handle(res);
}

// The download link is a plain href, not a fetch, so it needs the same base.
// Exported rather than built inline in a component: there is one API origin and
// this file owns it.
export function downloadUrl(id) {
  return `${BASE}/api/documents/${id}/download`;
}
