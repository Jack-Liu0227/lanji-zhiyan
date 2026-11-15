CREATE TABLE IF NOT EXISTS project_paper_links (
  result_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, paper_id TEXT NOT NULL,
  FOREIGN KEY(result_id) REFERENCES search_cache(id) ON DELETE CASCADE,
  FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS project_paper_links_project ON project_paper_links(task_id);
CREATE TABLE IF NOT EXISTS workflow_executions (
  run_id TEXT PRIMARY KEY, layout_snapshot TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES workflow_runs(id) ON DELETE CASCADE
);
