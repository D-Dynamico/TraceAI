// Thin fetch wrapper around the TraceAI backend API.
// Requests go to /api/* and are proxied to FastAPI by Vite in dev.

import { getUserId, USER_HEADER } from "./userId";

// Where the API lives. Empty in dev and in tests, so every request stays a
// same-origin /api/* path and Vite's proxy (vite.config.js) forwards it to
// :8000 — unchanged behaviour. Deployed, the frontend is on Vercel and the API
// on Render, a different origin, so VITE_API_URL supplies it at build time.
// Vite inlines import.meta.env at build, so this cannot be changed after the
// fact: rebuild the frontend to repoint it.
const BASE = (import.meta.env?.VITE_API_URL ?? "").replace(/\/+$/, "");

// Every request carries this browser's id so the backend can scope it to that
// visitor's own documents (see userId.js and backend/identity.py). Centralised
// here rather than added per call: a single endpoint that forgot the header
// would silently read and write the *shared* dataset, which looks like working
// software right up until two people use it at once.
function apiFetch(path, options = {}) {
  return fetch(`${BASE}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), [USER_HEADER]: getUserId() },
  });
}

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
  const res = await apiFetch(`/api/upload`, { method: "POST", body: form });
  return handle(res);
}

export async function ingestUrl(url) {
  const res = await apiFetch(`/api/ingest-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handle(res);
}

export async function ingestText(text) {
  const res = await apiFetch(`/api/ingest-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  return handle(res);
}

export async function search(query, k = 5) {
  const res = await apiFetch(`/api/search`, {
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
  const res = await apiFetch(`/api/answer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, doc_ids: docIds }),
  });
  return handle(res);
}

export async function listDocuments() {
  const res = await apiFetch(`/api/documents`);
  return handle(res);
}

export async function getDocument(id) {
  const res = await apiFetch(`/api/documents/${id}`);
  return handle(res);
}

export async function recategorize(id) {
  const res = await apiFetch(`/api/documents/${id}/recategorize`, {
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
  const res = await apiFetch(`/api/documents/${id}/category`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ category }),
  });
  return handle(res);
}

// Hard-delete a document from every store (SQLite, the vector index, and the
// original file + sidecar for an uploaded file). Scoped to the user server-side.
export async function deleteDocument(id) {
  const res = await apiFetch(`/api/documents/${id}`, { method: "DELETE" });
  return handle(res);
}

export async function getGraph() {
  const res = await apiFetch(`/api/graph`);
  return handle(res);
}

// Career-path inference is a Gemini call, so it is manual-trigger (a button on
// the graph), not run on every graph read. The response carries the item-B
// degradation contract (degraded_reason / retryable) so the UI can offer a retry.
export async function inferCareerPaths() {
  const res = await apiFetch(`/api/career-paths`, { method: "POST" });
  return handle(res);
}

// Load the demo profile (plan.md §14) — a 10-document student journey, seeded
// server-side with no Gemini call. Idempotent: re-loading replaces the prior
// demo docs rather than duplicating them.
export async function seedDemo() {
  const res = await apiFetch(`/api/seed-demo`, { method: "POST" });
  return handle(res);
}

// Undo the seed — removes only this visitor's demo-* documents, never their own
// uploads (the scoping lives in the backend's clear_demo).
export async function clearDemo() {
  const res = await apiFetch(`/api/seed-demo`, { method: "DELETE" });
  return handle(res);
}

export async function health() {
  const res = await apiFetch(`/api/health`);
  return handle(res);
}

// The download link is a plain href, not a fetch, so it needs the same base.
// Exported rather than built inline in a component: there is one API origin and
// this file owns it.
//
// The id rides as `?u=` because a browser navigation cannot carry a custom
// header — without it every download 404s for every visitor except the default
// one, since the route now resolves the original under the caller's own
// uploads/{user_id}/ directory. The backend prefers the header when both are
// present, so this affects nothing else.
export function downloadUrl(id) {
  return `${BASE}/api/documents/${id}/download?u=${encodeURIComponent(getUserId())}`;
}
