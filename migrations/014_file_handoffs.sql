CREATE TABLE IF NOT EXISTS file_handoffs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  layout_id TEXT NOT NULL,
  revision TEXT NOT NULL,
  node_id TEXT NOT NULL,
  request_key TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  payload TEXT NOT NULL,
  run_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(project_id, request_key),
  FOREIGN KEY(project_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS file_handoffs_project ON file_handoffs(project_id, created_at);
