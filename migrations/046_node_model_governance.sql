CREATE TABLE IF NOT EXISTS model_health_state (
  profile_id TEXT PRIMARY KEY,
  status TEXT NOT NULL DEFAULT 'unknown',
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  cooldown_until TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  last_error_code TEXT NOT NULL DEFAULT '',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  last_checked_at TEXT NOT NULL DEFAULT '',
  last_ok_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

ALTER TABLE assistants ADD COLUMN fallback_model_ids TEXT NOT NULL DEFAULT '[]';
ALTER TABLE team_members ADD COLUMN fallback_model_ids TEXT NOT NULL DEFAULT '[]';
