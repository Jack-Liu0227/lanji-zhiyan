CREATE TABLE IF NOT EXISTS model_requests (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
 project_id TEXT NOT NULL DEFAULT '', owner_id TEXT NOT NULL DEFAULT '',
 assistant_id TEXT NOT NULL DEFAULT '', run_id TEXT NOT NULL DEFAULT '',
 profile_id TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL, model TEXT NOT NULL,
 status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
 prompt_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
 reasoning_tokens INTEGER, cache_write_tokens INTEGER, duration_ms INTEGER NOT NULL,
 first_token_ms INTEGER, context_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS model_requests_time ON model_requests(created_at);
CREATE INDEX IF NOT EXISTS model_requests_owner ON model_requests(owner_id,created_at);
