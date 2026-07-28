"""Per-visitor identity for the deployed demo.

**The problem this solves.** Every route used to pin `DEFAULT_USER = "demo"`, so
one dataset was shared by everyone who opened the public URL: the second visitor
never saw the empty state, and — the part that actually matters — anything a
visitor uploaded was readable, downloadable, and deletable by the next one. A
reviewer trying the app with their real résumé published it to strangers.

The storage layer was always user-scoped (`list_documents`, `delete_document`,
`build_graph`, `embeddings.query`, and `uploads/{user_id}/` all take a user id,
and the graph's scoping is mutation-tested). Nothing ever supplied a *second*
identity. This module supplies it.

**What this is not.** The id arrives in a request header that the client
generates, so anyone can send someone else's id and read their documents. It is
**separation, not authentication** — it stops accidental collisions between two
reviewers, not a deliberate one. Real auth is plan.md §17's stretch goal. Say
this plainly wherever it is surfaced rather than implying a security boundary
that does not exist.

**Why validation is not optional.** `user_id` is interpolated into a filesystem
path (`storage.user_dir`). An id of `../../etc` would escape the uploads
directory on write and on read, so a header straight from the internet is
rejected unless it matches a strict allowlist. Same class of hole as the doc-id
glob that `storage.find_by_id` already guards.
"""

from __future__ import annotations

import re

from fastapi import Header, Query

# The header the frontend sets on every request. Custom, so it needs no cookie
# and carries no ambient authority — a cross-site request cannot attach it
# without CORS approval the way a cookie would ride along automatically.
USER_HEADER = "X-User-Id"

# Kept for the CLI seeder, the tests, and any caller that sends no header at all
# (curl, the OpenAPI docs page) — those keep the old shared dataset rather than
# failing.
DEFAULT_USER = "demo"

# Deliberately strict: lowercase hex, dashes, 8-64 chars. That admits a uuid4
# (with or without dashes) and nothing else — no dots, no slashes, no
# backslashes, no percent-encoding, so nothing that reaches `storage.user_dir`
# can traverse out of the uploads directory or collide with a sibling path.
_USER_ID_RE = re.compile(r"^[0-9a-f][0-9a-f\-]{6,62}[0-9a-f]$")


def is_valid_user_id(value: str) -> bool:
    return bool(_USER_ID_RE.match(value or ""))


def resolve_user(value: str | None) -> str:
    """Map a raw header value to the user id the request will act as.

    Falls back to `DEFAULT_USER` for both "absent" and "malformed" on purpose.
    Rejecting a malformed id with a 400 would turn a corrupted localStorage
    value into a hard-broken app for that visitor, with no way out but clearing
    site data; falling back degrades them into the shared demo dataset, which is
    exactly where they were before this existed.
    """
    if value and is_valid_user_id(value):
        return value
    return DEFAULT_USER


def current_user(
    x_user_id: str | None = Header(default=None),
    u: str | None = Query(default=None),
) -> str:
    """FastAPI dependency: the user id this request acts as.

    FastAPI maps the `x_user_id` parameter name to the `X-User-Id` header.

    **`?u=` exists for the download link, which cannot send a header.** The
    original is served through a plain `<a href>`; a browser navigation carries
    no custom header, so a header-only scheme would 404 every download for every
    visitor except the default one. The header wins when both are present, so
    ordinary XHR traffic is unaffected by a stray query param.

    The trade-off is that a user id can land in a URL — in browser history, and
    in the server access log. Acceptable only because this is separation rather
    than authentication (see the module docstring); it would not be if the id
    were a credential.
    """
    return resolve_user(x_user_id or u)
