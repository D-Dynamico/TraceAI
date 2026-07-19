"""Original-file storage with integrity guarantees.

Implements the "Original Format Preservation" principle from the plan:
  - Originals are written byte-for-byte unchanged under uploads/{user_id}/
  - A SHA-256 checksum is computed at upload time
  - Extracted text/metadata live in a *separate* sidecar file — the original
    is never modified
  - Downloads re-verify the checksum before serving, proving integrity

Phase 1 keeps the manifest as a JSON sidecar next to each original. Phase 2
migrates this metadata into SQLite (the `documents` table already has matching
`checksum` / `original_path` / `source_url` columns); the sidecar stays as the
on-disk source of truth for integrity.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import settings

MANIFEST_SUFFIX = ".meta.json"

# Document ids are uuid4().hex — 32 hex characters, nothing else.
_DOC_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Stream a file through SHA-256 so large files don't load into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class DocumentManifest:
    id: str
    filename: str            # original filename as uploaded
    stored_path: str         # path relative to project root
    file_type: str           # pdf | docx | pptx | text | image | text_entry | url
    checksum: str            # SHA-256 of the original bytes
    size_bytes: int
    upload_date: str
    source_url: str = ""     # populated for URL-based inputs (Phase 3)
    extraction: dict = field(default_factory=dict)  # text + method, kept separate

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def manifest_path_for(stored_path: Path) -> Path:
    return stored_path.with_name(stored_path.name + MANIFEST_SUFFIX)


def user_dir(user_id: str) -> Path:
    d = settings.upload_dir / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_original(user_id: str, doc_id: str, filename: str, data: bytes) -> tuple[Path, str]:
    """Write the original bytes unchanged. Returns (stored_path, checksum).

    The checksum is computed from the in-memory bytes and then re-verified
    against what actually landed on disk, so a truncated or corrupted write
    fails loudly at upload time rather than silently later.
    """
    ext = Path(filename).suffix.lower()
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "_" for c in Path(filename).stem)[:80]
    stored_path = user_dir(user_id) / f"{doc_id}__{safe_stem or 'file'}{ext}"

    expected = sha256_bytes(data)
    stored_path.write_bytes(data)

    actual = sha256_file(stored_path)
    if actual != expected:
        stored_path.unlink(missing_ok=True)
        raise IOError(
            f"Integrity check failed writing {filename}: "
            f"expected {expected[:12]}…, got {actual[:12]}…"
        )
    return stored_path, expected


def write_manifest(manifest: DocumentManifest, stored_path: Path) -> Path:
    path = manifest_path_for(stored_path)
    path.write_text(manifest.to_json(), encoding="utf-8")
    return path


def load_manifest(stored_path: Path) -> DocumentManifest | None:
    path = manifest_path_for(stored_path)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return DocumentManifest(**data)


def is_valid_doc_id(doc_id: str) -> bool:
    return bool(_DOC_ID_RE.match(doc_id or ""))


def find_by_id(doc_id: str, user_id: str) -> tuple[Path, DocumentManifest] | None:
    """Locate a stored original by document id. Returns (path, manifest).

    `doc_id` reaches here straight from the URL path and is interpolated into a
    glob pattern, so it is validated first. Without this, a doc_id of `*` is a
    valid pattern that matches the first stored file — letting a caller retrieve
    someone's document without knowing any id at all. `..` is rejected by the
    same check.
    """
    if not is_valid_doc_id(doc_id):
        return None

    base = settings.upload_dir / user_id
    if not base.exists():
        return None
    for candidate in base.glob(f"{doc_id}__*"):
        if candidate.name.endswith(MANIFEST_SUFFIX):
            continue
        manifest = load_manifest(candidate)
        if manifest is not None:
            return candidate, manifest
    return None


def verify_integrity(stored_path: Path, manifest: DocumentManifest) -> bool:
    """Recompute the checksum and compare against the recorded one."""
    return sha256_file(stored_path) == manifest.checksum


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
