ALTER TABLE project_todos ADD COLUMN acceptance TEXT NOT NULL DEFAULT '';
ALTER TABLE project_todos ADD COLUMN dependencies TEXT NOT NULL DEFAULT '[]';
CREATE TABLE plan_task_links (
  plan_id TEXT NOT NULL, task_id TEXT NOT NULL, created_at TEXT NOT NULL,
  PRIMARY KEY(plan_id,task_id),
  FOREIGN KEY(plan_id) REFERENCES project_todos(id) ON DELETE CASCADE,
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE
);
INSERT OR IGNORE INTO plan_task_links
 SELECT o.legacy_id,o.task_id,t.created_at FROM project_task_origins o
 JOIN project_tasks t ON t.id=o.task_id JOIN project_todos p ON p.id=o.legacy_id
 WHERE o.kind='todo' AND p.task_id=t.project_id;
CREATE TABLE execution_board_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
  task_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL, kind TEXT NOT NULL, activity TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE INDEX execution_board_project ON execution_board_events(project_id,id);
ALTER TABLE chats ADD COLUMN pinned_at TEXT NOT NULL DEFAULT '';
ALTER TABLE chats ADD COLUMN archived_at TEXT NOT NULL DEFAULT '';
ALTER TABLE chats ADD COLUMN deleted_at TEXT NOT NULL DEFAULT '';
ALTER TABLE chats ADD COLUMN channel TEXT NOT NULL DEFAULT 'conversation';
CREATE TABLE conversation_flags (
  kind TEXT NOT NULL, id TEXT NOT NULL, pinned_at TEXT NOT NULL DEFAULT '',
  archived_at TEXT NOT NULL DEFAULT '', deleted_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(kind,id)
);
