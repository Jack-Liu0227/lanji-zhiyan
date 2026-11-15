CREATE TABLE execution_board_hidden (
  project_id TEXT NOT NULL,
  item_kind TEXT NOT NULL CHECK(item_kind IN ('task','conversation')),
  item_id TEXT NOT NULL,
  deleted_at TEXT NOT NULL,
  PRIMARY KEY(project_id,item_kind,item_id),
  FOREIGN KEY(project_id) REFERENCES research_tasks(id) ON DELETE CASCADE
);
