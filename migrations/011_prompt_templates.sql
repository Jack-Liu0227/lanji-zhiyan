CREATE TABLE IF NOT EXISTS prompt_template_bindings (
  project_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  scope_id TEXT NOT NULL DEFAULT '',
  template_id TEXT NOT NULL,
  revision_id TEXT NOT NULL DEFAULT '',
  variables TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(project_id, scope, scope_id)
);
CREATE TRIGGER IF NOT EXISTS prompt_bindings_project_deleted AFTER DELETE ON research_tasks BEGIN
  DELETE FROM prompt_template_bindings WHERE project_id=OLD.id;
END;
