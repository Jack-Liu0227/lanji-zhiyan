CREATE TABLE IF NOT EXISTS calendar_events (
  id TEXT PRIMARY KEY, task_id TEXT, title TEXT NOT NULL, date TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'milestone', note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS model_profiles (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
  api_key TEXT NOT NULL DEFAULT '', model TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, paper_id TEXT, title TEXT NOT NULL,
  markdown TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS steps (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
  status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS search_cache (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, source TEXT NOT NULL, title TEXT NOT NULL,
  authors TEXT NOT NULL DEFAULT '', year TEXT NOT NULL DEFAULT '', doi TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '', pdf_url TEXT NOT NULL DEFAULT '', abstract TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
  events TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, scope TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS project_memory (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS md_notes (
  id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '未命名笔记',
  content TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT '',
  paper_id TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL,
  api TEXT NOT NULL DEFAULT 'openai-completions', api_key TEXT NOT NULL DEFAULT '',
  models TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'builtin',
  enabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dl_queue (
  task_id TEXT NOT NULL, result_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  path TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
  PRIMARY KEY (task_id, result_id)
);
CREATE TABLE IF NOT EXISTS assistants (
  id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'user', name TEXT NOT NULL,
  avatar TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
  rules TEXT NOT NULL DEFAULT '', prompts TEXT NOT NULL DEFAULT '[]',
  model_id TEXT NOT NULL DEFAULT '', enabled INTEGER NOT NULL DEFAULT 1,
  sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_members (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', step TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'idle',
  note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS team_tasks (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, member_id TEXT NOT NULL DEFAULT '',
  member_name TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'queued',
  created_at TEXT NOT NULL, started_at TEXT NOT NULL DEFAULT '', ended_at TEXT NOT NULL DEFAULT '',
  output_path TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '', trigger TEXT NOT NULL DEFAULT 'leader',
  origin TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS team_events (
  id TEXT PRIMARY KEY, team_task_id TEXT NOT NULL, task_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'note', text TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_status ON team_tasks(status);
CREATE TABLE IF NOT EXISTS schedules (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL DEFAULT '',
  project_id TEXT NOT NULL DEFAULT '', frequency TEXT NOT NULL DEFAULT 'daily',
  cron TEXT NOT NULL DEFAULT '0 9 * * *', output_dir TEXT NOT NULL DEFAULT '定时任务',
  enabled INTEGER NOT NULL DEFAULT 1, last_run_at TEXT NOT NULL DEFAULT '',
  next_run_at TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schedule_runs (
  id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
  trigger TEXT NOT NULL DEFAULT 'scheduler', status TEXT NOT NULL DEFAULT 'running',
  started_at TEXT NOT NULL, ended_at TEXT NOT NULL DEFAULT '', output_path TEXT NOT NULL DEFAULT '',
  summary TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_schedule_runs_time ON schedule_runs(started_at DESC);
CREATE TABLE IF NOT EXISTS capability_bindings (
  assistant_id TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY (assistant_id, kind, item_id)
);
CREATE TABLE IF NOT EXISTS capability_tool_metadata (
  name TEXT PRIMARY KEY, label TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS capability_mcp_config (
  id INTEGER PRIMARY KEY CHECK(id=1), protected TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capability_mcp_tools (
  server TEXT PRIMARY KEY, config_hash TEXT NOT NULL, tools TEXT NOT NULL DEFAULT '[]'
);
