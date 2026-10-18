-- v1.3.0 features schema: easyScholar cache, project todos, and unified integration settings

CREATE TABLE IF NOT EXISTS easyscholar_cache (
  publication_name TEXT PRIMARY KEY,
  official_rank TEXT NOT NULL DEFAULT '{}',
  custom_rank TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS project_todos (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'TODO',
  priority TEXT NOT NULL DEFAULT 'P1',
  assigned_assistant TEXT NOT NULL DEFAULT '',
  due_date TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_project_todos_task_id ON project_todos(task_id, status);

CREATE TABLE IF NOT EXISTS integration_settings (
  service_id TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 0,
  credentials_protected TEXT NOT NULL DEFAULT '',
  options TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);
