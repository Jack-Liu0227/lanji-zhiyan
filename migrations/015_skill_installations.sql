CREATE TABLE IF NOT EXISTS skill_installations (
    path TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scope TEXT NOT NULL,
    provider TEXT NOT NULL,
    source TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '',
    digest TEXT NOT NULL,
    files INTEGER NOT NULL,
    installed_at TEXT NOT NULL
);
