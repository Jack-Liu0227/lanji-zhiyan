-- v1.4 foundations: canonical assets, revisions, provenance, resumable runs and team checkpoints.

CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL,
  title TEXT NOT NULL DEFAULT '',
  canonical_uri TEXT NOT NULL UNIQUE,
  storage TEXT NOT NULL DEFAULT 'file',
  mime TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL DEFAULT '',
  current_revision_id TEXT NOT NULL DEFAULT '',
  source TEXT NOT NULL DEFAULT 'local',
  status TEXT NOT NULL DEFAULT 'active',
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_revisions (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  content_uri TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'system',
  run_id TEXT NOT NULL DEFAULT '',
  message TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(asset_id, revision)
);

CREATE TABLE IF NOT EXISTS asset_relations (
  from_asset_id TEXT NOT NULL,
  relation TEXT NOT NULL,
  to_asset_id TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY(from_asset_id, relation, to_asset_id)
);

CREATE TABLE IF NOT EXISTS asset_provenance (
  id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT 'system',
  operation TEXT NOT NULL,
  input_snapshot TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS asset_fts USING fts5(
  asset_id UNINDEXED,
  project_id UNINDEXED,
  title,
  body,
  tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS workflow_runs (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL DEFAULT '',
  workflow TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  current_node TEXT NOT NULL DEFAULT '',
  idempotency_key TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  ended_at TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_runs_idempotency
  ON workflow_runs(project_id, idempotency_key)
  WHERE idempotency_key <> '';

CREATE TABLE IF NOT EXISTS workflow_nodes (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  stage TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  input_assets TEXT NOT NULL DEFAULT '[]',
  output_assets TEXT NOT NULL DEFAULT '[]',
  acceptance TEXT NOT NULL DEFAULT '{}',
  checkpoint TEXT NOT NULL DEFAULT '{}',
  attempt INTEGER NOT NULL DEFAULT 0,
  started_at TEXT NOT NULL DEFAULT '',
  ended_at TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_assets_project_kind ON assets(project_id, kind, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_asset_revisions_asset ON asset_revisions(asset_id, revision DESC);
CREATE INDEX IF NOT EXISTS idx_asset_relations_to ON asset_relations(to_asset_id, relation);
CREATE INDEX IF NOT EXISTS idx_workflow_nodes_run ON workflow_nodes(run_id, status);

ALTER TABLE team_tasks ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE team_tasks ADD COLUMN checkpoint TEXT NOT NULL DEFAULT '{}';
ALTER TABLE team_tasks ADD COLUMN retry_at TEXT NOT NULL DEFAULT '';
ALTER TABLE team_tasks ADD COLUMN run_id TEXT NOT NULL DEFAULT '';
ALTER TABLE team_tasks ADD COLUMN input_assets TEXT NOT NULL DEFAULT '[]';
ALTER TABLE team_tasks ADD COLUMN output_assets TEXT NOT NULL DEFAULT '[]';
ALTER TABLE team_tasks ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending';

CREATE INDEX IF NOT EXISTS idx_team_tasks_retry ON team_tasks(status, retry_at);
