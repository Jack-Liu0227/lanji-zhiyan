CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, owner_id TEXT NOT NULL,
  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
  checkpoint TEXT NOT NULL DEFAULT '{}', cancel_requested INTEGER NOT NULL DEFAULT 0,
  idempotency_key TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_attempts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL, error_code TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS run_events (
  run_id TEXT NOT NULL, event_id INTEGER NOT NULL, kind TEXT NOT NULL,
  payload TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(run_id,event_id)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY, actor TEXT NOT NULL, assistant_id TEXT NOT NULL DEFAULT '',
  tool TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
  input_hash TEXT NOT NULL, outcome TEXT NOT NULL, duration_ms INTEGER NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_idempotency ON tasks(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_run_events_run_id ON run_events(run_id, event_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_project ON audit_events(project_id, created_at DESC);
