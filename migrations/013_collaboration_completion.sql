CREATE TABLE IF NOT EXISTS zotero_library_items (
  library TEXT NOT NULL, item_key TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 0,
  data TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (library,item_key)
);
CREATE TABLE IF NOT EXISTS zotero_project_memberships (
  task_id TEXT NOT NULL, library TEXT NOT NULL, item_key TEXT NOT NULL,
  result_id TEXT NOT NULL DEFAULT '', identity TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active', batch_id TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL, PRIMARY KEY (task_id,library,item_key)
);
CREATE INDEX IF NOT EXISTS zotero_membership_project ON zotero_project_memberships(task_id,status);
CREATE TABLE IF NOT EXISTS zotero_link_batches (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, library TEXT NOT NULL,
  collection TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'preview'
);
CREATE TABLE IF NOT EXISTS schedule_workflows (
  schedule_id TEXT PRIMARY KEY, layout_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_workflow_runs (
  schedule_run_id TEXT PRIMARY KEY, workflow_run_id TEXT NOT NULL UNIQUE,
  schedule_snapshot TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY(workflow_run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS review_reminders (
  node_id TEXT PRIMARY KEY, due_at TEXT NOT NULL DEFAULT '', remind_at TEXT NOT NULL DEFAULT '',
  dismissed INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
