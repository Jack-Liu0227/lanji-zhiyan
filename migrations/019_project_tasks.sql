CREATE TABLE IF NOT EXISTS project_tasks (
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, goal TEXT NOT NULL,
  executor_binding_id TEXT NOT NULL, member_id TEXT NOT NULL DEFAULT '',
  inputs TEXT NOT NULL DEFAULT '[]', output_contract TEXT NOT NULL DEFAULT '{}',
  review_required INTEGER NOT NULL DEFAULT 1, parent_task_id TEXT NOT NULL DEFAULT '',
  workflow_run_id TEXT NOT NULL DEFAULT '', workflow_node_id TEXT NOT NULL DEFAULT '',
  scope TEXT NOT NULL DEFAULT '{}', metadata TEXT NOT NULL DEFAULT '{}', dependencies TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'draft', version INTEGER NOT NULL DEFAULT 1,
  latest_run_id TEXT NOT NULL DEFAULT '', accepted_delivery_id TEXT NOT NULL DEFAULT '',
  request_key TEXT NOT NULL DEFAULT '', request_digest TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS project_task_request ON project_tasks(project_id,request_key) WHERE request_key<>'';
CREATE INDEX IF NOT EXISTS project_task_state ON project_tasks(project_id,status,updated_at);
CREATE INDEX IF NOT EXISTS project_task_parent ON project_tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS project_task_workflow ON project_tasks(workflow_run_id,workflow_node_id);
CREATE TABLE IF NOT EXISTS project_task_runs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', request_key TEXT NOT NULL,
  policy_snapshot TEXT NOT NULL DEFAULT '{}', input_snapshot TEXT NOT NULL DEFAULT '{}',
  executor_snapshot TEXT NOT NULL DEFAULT '{}', checkpoint TEXT NOT NULL DEFAULT '{}',
  output_snapshot TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE,
  UNIQUE(task_id,attempt), UNIQUE(task_id,request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS project_task_active_attempt ON project_task_runs(task_id) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS project_task_deliveries (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_run_id TEXT NOT NULL,
  decision TEXT NOT NULL, source TEXT NOT NULL, actor_id TEXT NOT NULL DEFAULT '',
  input_versions TEXT NOT NULL, output_versions TEXT NOT NULL, policy_snapshot TEXT NOT NULL,
  feedback TEXT NOT NULL DEFAULT '', request_key TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE,
  FOREIGN KEY(task_run_id) REFERENCES project_task_runs(id) ON DELETE CASCADE,
  UNIQUE(task_id,request_key)
);
CREATE INDEX IF NOT EXISTS project_delivery_task ON project_task_deliveries(task_id,created_at);
CREATE TABLE IF NOT EXISTS project_task_events (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_run_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_task_origins (
  kind TEXT NOT NULL, legacy_id TEXT NOT NULL, task_id TEXT NOT NULL, task_run_id TEXT NOT NULL DEFAULT '',
  PRIMARY KEY(kind,legacy_id), FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE
);
CREATE TRIGGER IF NOT EXISTS project_task_children_deleted AFTER DELETE ON project_tasks BEGIN
  DELETE FROM project_task_deliveries WHERE task_id=OLD.id;
  DELETE FROM project_task_runs WHERE task_id=OLD.id;
  DELETE FROM project_task_events WHERE task_id=OLD.id;
  DELETE FROM project_task_origins WHERE task_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS project_tasks_project_deleted AFTER DELETE ON research_tasks BEGIN
  DELETE FROM project_tasks WHERE project_id=OLD.id;
END;
