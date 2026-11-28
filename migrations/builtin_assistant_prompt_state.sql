CREATE TABLE IF NOT EXISTS assistant_prompt_state (
  assistant_id TEXT NOT NULL,
  field TEXT NOT NULL CHECK (field IN ('rules', 'prompts')),
  revision TEXT NOT NULL DEFAULT '',
  source_hash TEXT NOT NULL DEFAULT '',
  custom INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (assistant_id, field)
);
