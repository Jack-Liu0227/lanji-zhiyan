CREATE TABLE IF NOT EXISTS paper_zotero_refs (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    library TEXT NOT NULL,
    item_key TEXT NOT NULL,
    attachment_key TEXT NOT NULL,
    UNIQUE(task_id, library, item_key, attachment_key)
);

CREATE TABLE IF NOT EXISTS zotero_save_plans (
    token TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    expires REAL NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS zotero_pending_writes (
    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    result_id TEXT NOT NULL,
    library TEXT NOT NULL,
    item_key TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(task_id, result_id, library)
);

CREATE TABLE IF NOT EXISTS zotero_downloads (
    task_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    result_id TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(task_id, result_id)
);

CREATE TABLE IF NOT EXISTS paper_evidence_recheck (
    evidence_id TEXT PRIMARY KEY REFERENCES evidence(id) ON DELETE CASCADE
);
