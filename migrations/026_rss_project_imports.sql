CREATE TABLE IF NOT EXISTS rss_project_imports (
    item_id TEXT NOT NULL REFERENCES rss_items(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
    result_id TEXT NOT NULL REFERENCES search_cache(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, project_id)
);
CREATE INDEX IF NOT EXISTS idx_rss_project_imports_project ON rss_project_imports(project_id);
