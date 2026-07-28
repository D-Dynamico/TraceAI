// Per-browser identity for the deployed demo.
//
// The app is deployed at one public URL with one shared dataset, so the first
// visitor's "Load Demo Profile" click populated the app for everyone after
// them — and anything a visitor uploaded was readable, downloadable, and
// deletable by the next. This gives each browser its own id; the backend scopes
// every read and write to it (see backend/identity.py).
//
// NOT a credential. It is generated here, sent in a header the client controls,
// and stored in localStorage where any script on the page can read it. It stops
// two reviewers colliding, not a determined one. Real auth is plan.md §17.
//
// Persisted rather than per-session so a reload, a new tab, or a visit tomorrow
// finds the same documents — a sessionStorage id would strand every upload the
// moment the tab closed, which is worse than the shared state it replaces.

const STORAGE_KEY = "traceai.userId";

// Matches backend/identity.py's allowlist: lowercase hex and dashes. The server
// falls back to the shared dataset for anything else, so a value that fails
// here would silently put the visitor back in the shared pool.
const VALID = /^[0-9a-f][0-9a-f-]{6,62}[0-9a-f]$/;

// Set when localStorage is unavailable (Safari private mode, disabled storage,
// a sandboxed iframe). The visitor still gets a private dataset — it just does
// not survive a reload, which beats throwing on page load.
let fallbackId = null;

function newId() {
  // randomUUID needs a secure context; localhost and https qualify, but a LAN
  // dev URL over plain http does not, which is exactly how this app gets opened
  // on a phone for a mobile check.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  // Last resort. Not unique enough to rely on for anything but keeping two
  // browsers apart, which is all this does.
  return `${Date.now().toString(16)}${Math.random().toString(16).slice(2, 14)}`.slice(0, 32);
}

export function getUserId() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored && VALID.test(stored)) return stored;
    // Replace a missing *or corrupted* value: a malformed id would be rejected
    // server-side and drop this visitor back into the shared dataset.
    const fresh = newId();
    window.localStorage.setItem(STORAGE_KEY, fresh);
    return fresh;
  } catch {
    if (!fallbackId) fallbackId = newId();
    return fallbackId;
  }
}

// Exported for the tests and for a future "start fresh" control.
export function resetUserId() {
  fallbackId = null;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* storage unavailable — the in-memory id above was already cleared */
  }
}

export const USER_HEADER = "X-User-Id";
