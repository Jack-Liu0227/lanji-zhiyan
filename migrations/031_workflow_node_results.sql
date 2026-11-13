CREATE TABLE project_task_output_versions (
  task_run_id TEXT NOT NULL REFERENCES project_task_runs(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  output_snapshot TEXT NOT NULL,
  checkpoint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_run_id, revision)
);
CREATE INDEX project_task_output_versions_task ON project_task_output_versions(task_id,task_run_id,revision);

CREATE TABLE workflow_node_drafts (
  task_run_id TEXT NOT NULL REFERENCES project_task_runs(id) ON DELETE CASCADE,
  revision INTEGER NOT NULL,
  artifact_id TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  window_id TEXT NOT NULL,
  base_digest TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  feedback TEXT NOT NULL DEFAULT '',
  instruction TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(task_run_id,revision,artifact_id,owner_id,window_id)
);

CREATE TABLE project_task_rework_requests (
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  request_key TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  base_run_id TEXT NOT NULL,
  base_revision INTEGER NOT NULL,
  new_run_id TEXT NOT NULL REFERENCES project_task_runs(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, request_key)
);

CREATE TABLE project_task_file_operations (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  task_run_id TEXT NOT NULL REFERENCES project_task_runs(id) ON DELETE CASCADE,
  base_revision INTEGER NOT NULL,
  target_revision INTEGER NOT NULL,
  files TEXT NOT NULL,
  state TEXT NOT NULL,
  error TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX project_task_file_operations_state ON project_task_file_operations(state);

CREATE TABLE workflow_node_commands (
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  request_key TEXT NOT NULL,
  action TEXT NOT NULL,
  request_digest TEXT NOT NULL,
  task_run_id TEXT NOT NULL REFERENCES project_task_runs(id) ON DELETE CASCADE,
  output_revision INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id,request_key)
);

INSERT INTO project_task_output_versions(task_run_id,revision,task_id,output_snapshot,checkpoint,created_at)
SELECT id,COALESCE(json_extract(checkpoint,'$.output_revision'),0),task_id,output_snapshot,checkpoint,ended_at
FROM project_task_runs WHERE status='succeeded' AND output_snapshot<>'{}';
