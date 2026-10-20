-- v1.5 team work-item graph and review gate.

ALTER TABLE team_tasks ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]';
ALTER TABLE team_tasks ADD COLUMN output_contract TEXT NOT NULL DEFAULT '{}';
ALTER TABLE team_tasks ADD COLUMN reviewer_id TEXT NOT NULL DEFAULT '';
ALTER TABLE team_tasks ADD COLUMN review_attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE team_tasks ADD COLUMN approved_at TEXT NOT NULL DEFAULT '';
ALTER TABLE team_tasks ADD COLUMN review_feedback TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_team_tasks_review ON team_tasks(review_status, reviewer_id, status);
