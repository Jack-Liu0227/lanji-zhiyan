CREATE TABLE IF NOT EXISTS research_setups (
    request_key TEXT PRIMARY KEY,
    project_id TEXT NOT NULL DEFAULT '',
    plan_hash TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'configuring',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_setups_project ON research_setups(project_id);
