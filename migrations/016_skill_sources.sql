CREATE TABLE IF NOT EXISTS skill_sources (
    id TEXT PRIMARY KEY,
    adapter TEXT NOT NULL,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    api_url TEXT NOT NULL,
    download_url TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    builtin INTEGER NOT NULL DEFAULT 0,
    deleted INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0
);
