CREATE TABLE schedule_trigger_queue (
  id TEXT PRIMARY KEY,
  schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
  due_at TEXT NOT NULL,
  trigger TEXT NOT NULL,
  claimed INTEGER NOT NULL DEFAULT 0,
  UNIQUE(schedule_id, due_at)
);
CREATE INDEX schedule_trigger_queue_pending ON schedule_trigger_queue(claimed, due_at);
