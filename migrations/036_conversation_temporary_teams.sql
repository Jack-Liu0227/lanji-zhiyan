CREATE TABLE IF NOT EXISTS conversation_temporary_teams (
    team_id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversation_temporary_teams_chat ON conversation_temporary_teams(chat_id);
