CREATE TABLE deleted_project_locations (
    project_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    base_dir TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);
