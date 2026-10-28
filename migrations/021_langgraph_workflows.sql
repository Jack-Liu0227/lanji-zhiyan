-- LangGraph durable workflow execution and human review checkpoints
CREATE TABLE IF NOT EXISTS langgraph_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  graph_type TEXT NOT NULL,
  title TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL, -- running, awaiting_review, completed, failed, cancelled
  current_node TEXT NOT NULL DEFAULT '',
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
  output_summary TEXT NOT NULL DEFAULT '',
  artifacts_json TEXT NOT NULL DEFAULT '[]',
  review_status TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected, edited
  reviewer_feedback TEXT NOT NULL DEFAULT '',
  reviewed_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  FOREIGN KEY(run_id) REFERENCES langgraph_runs(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_langgraph_runs_project ON langgraph_runs(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_run ON langgraph_checkpoints(run_id);
