CREATE TABLE IF NOT EXISTS service_settings (
  service TEXT NOT NULL,
  owner TEXT NOT NULL DEFAULT '',
  data TEXT NOT NULL,
  PRIMARY KEY(service, owner)
);

CREATE TABLE model_budget_counters (
  scope_key TEXT PRIMARY KEY,
  requests INTEGER NOT NULL DEFAULT 0,
  known_tokens INTEGER NOT NULL DEFAULT 0
);
