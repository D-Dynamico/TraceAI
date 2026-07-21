-- TraceAI SQLite schema (plan.md §5).
--
-- Note on `user_id` in `documents`: originals live under uploads/{user_id}/,
-- and storage.find_by_id() needs the user to locate a file. Phase 2 has no auth
-- so everything is written as 'demo', but carrying the column now means the
-- multi-user stretch goal (plan.md § Stretch Goals) does not require a migration.

PRAGMA foreign_keys = ON;

-- Core document metadata
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL DEFAULT 'demo',
    filename TEXT NOT NULL,
    original_path TEXT NOT NULL,
    file_type TEXT,            -- pdf, docx, pptx, image, text, url, text_entry
    source_url TEXT,           -- for URL-based inputs
    checksum TEXT,             -- SHA-256, proves original file integrity
    document_type TEXT,
    category TEXT,
    title TEXT,
    summary TEXT,
    extracted_date TEXT,
    upload_date TEXT DEFAULT CURRENT_TIMESTAMP,
    raw_text TEXT,
    embedding_id TEXT,
    confidence REAL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
CREATE INDEX IF NOT EXISTS idx_documents_category ON documents(category);
-- Timeline (Module 4) sorts on extracted_date.
CREATE INDEX IF NOT EXISTS idx_documents_extracted_date ON documents(extracted_date);

-- Extracted entities
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    entity_type TEXT,  -- skill, organization, person, date
    entity_value TEXT
);

CREATE INDEX IF NOT EXISTS idx_entities_document ON entities(document_id);
-- The relationship engine (Module 3) joins documents on shared entity values.
CREATE INDEX IF NOT EXISTS idx_entities_type_value ON entities(entity_type, entity_value);

-- Relationships between documents/entities
CREATE TABLE IF NOT EXISTS relationships (
    id TEXT PRIMARY KEY,
    source_id TEXT,
    source_type TEXT,  -- document or entity
    target_id TEXT,
    target_type TEXT,
    relation_type TEXT,  -- certifies_skill, skill_used_in, similar_to, etc.
    weight REAL DEFAULT 1.0
);

CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_id);
CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_id);

-- Inferred career paths
CREATE TABLE IF NOT EXISTS career_paths (
    id TEXT PRIMARY KEY,
    title TEXT,                -- e.g. "AI/ML Engineer"
    match_score REAL,          -- 0.0-1.0 confidence
    evidence TEXT,             -- supporting docs/skills
    skill_gaps TEXT            -- suggested next steps
);

-- Tags for flexible categorization
CREATE TABLE IF NOT EXISTS tags (
    document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
    tag TEXT
);

CREATE INDEX IF NOT EXISTS idx_tags_document ON tags(document_id);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
