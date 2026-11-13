CREATE TABLE IF NOT EXISTS agent_handoffs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_member_id TEXT NOT NULL DEFAULT '',
  target_member_id TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  context_snapshot TEXT NOT NULL DEFAULT '{}',
  target_run_id TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES project_tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS agent_handoff_task ON agent_handoffs(task_id, created_at);
CREATE INDEX IF NOT EXISTS agent_handoff_target ON agent_handoffs(target_member_id, status);
