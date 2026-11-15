-- Connect LangGraph runs and checkpoints to unified project_tasks
-- Handled safely if columns already exist or not
CREATE TABLE IF NOT EXISTS langgraph_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  graph_type TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  current_node TEXT NOT NULL DEFAULT '',
  root_task_id TEXT NOT NULL DEFAULT '',
  state_snapshot TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(project_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  node_label TEXT NOT NULL,
  task_id TEXT NOT NULL DEFAULT '',
  task_run_id TEXT NOT NULL DEFAULT '',
  output_summary TEXT NOT NULL DEFAULT '',
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  review_status TEXT NOT NULL DEFAULT 'pending',
  reviewer_feedback TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES langgraph_runs(id) ON DELETE CASCADE
);
