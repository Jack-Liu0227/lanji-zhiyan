CREATE TABLE IF NOT EXISTS workflow_layouts (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  definition TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_workflow_definitions (
  project_id TEXT NOT NULL, layout_id TEXT NOT NULL, revision INTEGER NOT NULL,
  definition TEXT NOT NULL, base_fingerprint TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(project_id,layout_id),
  FOREIGN KEY(project_id) REFERENCES research_tasks(id) ON DELETE CASCADE,
  FOREIGN KEY(layout_id) REFERENCES workflow_layouts(id) ON DELETE CASCADE
);
CREATE TRIGGER IF NOT EXISTS project_workflow_definition_project_deleted AFTER DELETE ON research_tasks BEGIN
  DELETE FROM project_workflow_definitions WHERE project_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS project_workflow_definition_template_deleted AFTER DELETE ON workflow_layouts BEGIN
  DELETE FROM project_workflow_definitions WHERE layout_id=OLD.id;
END;
CREATE TABLE IF NOT EXISTS project_task_output_revisions (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_run_id TEXT NOT NULL,
  revision INTEGER NOT NULL, output_index INTEGER NOT NULL,
  original_path TEXT NOT NULL, revised_path TEXT NOT NULL,
  original_hash TEXT NOT NULL, revised_hash TEXT NOT NULL,
  actor_id TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(task_run_id,revision),
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE,
  FOREIGN KEY(task_run_id) REFERENCES project_task_runs(id) ON DELETE CASCADE
);
CREATE TRIGGER IF NOT EXISTS project_task_output_revisions_deleted AFTER DELETE ON project_tasks BEGIN
  DELETE FROM project_task_output_revisions WHERE task_id=OLD.id;
END;
