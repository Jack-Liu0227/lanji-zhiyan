CREATE TABLE IF NOT EXISTS task_links (
    todo_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    team_task_id TEXT NOT NULL DEFAULT '',
    schedule_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    output_assets TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'queued',
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS task_links_team ON task_links(team_task_id) WHERE team_task_id <> '';
CREATE INDEX IF NOT EXISTS task_links_schedule ON task_links(schedule_id);
CREATE TRIGGER IF NOT EXISTS task_links_todo_deleted AFTER DELETE ON project_todos BEGIN
    DELETE FROM task_links WHERE todo_id=OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS task_links_project_deleted AFTER DELETE ON research_tasks BEGIN
    DELETE FROM task_links WHERE project_id=OLD.id;
END;
