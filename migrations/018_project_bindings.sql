CREATE TABLE IF NOT EXISTS project_executor_bindings (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('assistant','team')),
  target_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  tools TEXT,
  source TEXT NOT NULL DEFAULT 'user',
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(project_id,kind,target_id)
);
CREATE INDEX IF NOT EXISTS project_executor_bindings_project ON project_executor_bindings(project_id,enabled);

CREATE TABLE IF NOT EXISTS project_execution_settings (
  project_id TEXT PRIMARY KEY,
  autonomy_mode TEXT NOT NULL DEFAULT 'supervised' CHECK(autonomy_mode IN ('supervised','full_access')),
  revision INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_workflow_roles (
  project_id TEXT NOT NULL,
  template_id TEXT NOT NULL,
  role_id TEXT NOT NULL,
  binding_id TEXT NOT NULL,
  member_id TEXT NOT NULL DEFAULT '',
  revision INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'user',
  updated_at TEXT NOT NULL,
  PRIMARY KEY(project_id,template_id,role_id)
);
CREATE INDEX IF NOT EXISTS project_workflow_roles_binding ON project_workflow_roles(binding_id);

CREATE TABLE IF NOT EXISTS project_binding_origins (
  binding_id TEXT NOT NULL,
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(binding_id,source,source_id)
);

CREATE TRIGGER IF NOT EXISTS project_executor_bindings_deleted
AFTER DELETE ON project_executor_bindings BEGIN
  DELETE FROM project_workflow_roles WHERE binding_id=OLD.id;
  DELETE FROM project_binding_origins WHERE binding_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS project_execution_project_deleted
AFTER DELETE ON research_tasks BEGIN
  DELETE FROM project_executor_bindings WHERE project_id=OLD.id;
  DELETE FROM project_workflow_roles WHERE project_id=OLD.id;
  DELETE FROM project_execution_settings WHERE project_id=OLD.id;
END;
