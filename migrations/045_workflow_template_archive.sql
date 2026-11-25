-- Keep removed template definitions and reference changes available for recovery.
CREATE TABLE IF NOT EXISTS workflow_template_archive (
  id TEXT PRIMARY KEY,
  operation TEXT NOT NULL,
  template_id TEXT NOT NULL,
  canonical_id TEXT NOT NULL DEFAULT '',
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
