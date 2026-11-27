-- Ensure assistant_id exists on legacy tables when migrations run standalone,
-- then prune redundant/non-core built-in assistants unless referenced or customized.

ALTER TABLE team_members ADD COLUMN assistant_id TEXT NOT NULL DEFAULT '';
ALTER TABLE schedules ADD COLUMN assistant_id TEXT NOT NULL DEFAULT '';

DELETE FROM assistants
WHERE source = 'builtin'
  AND id IN (
    'builtin-pony',
    'builtin-deepread',
    'builtin-topic',
    'builtin-proposal',
    'builtin-survey',
    'builtin-writingcoach',
    'builtin-entrans',
    'builtin-ml',
    'builtin-code',
    'builtin-experiment',
    'builtin-format',
    'builtin-refcheck',
    'builtin-response',
    'builtin-submit',
    'builtin-slides',
    'builtin-grant',
    'builtin-minutes',
    'builtin-tutor'
  )
  AND model_id = ''
  AND fallback_model_ids = '[]'
  AND updated_at = created_at
  AND NOT EXISTS (SELECT 1 FROM chats WHERE assistant_id = assistants.id)
  AND NOT EXISTS (SELECT 1 FROM team_members WHERE assistant_id = assistants.id)
  AND NOT EXISTS (SELECT 1 FROM schedules WHERE assistant_id = assistants.id)
  AND NOT EXISTS (
    SELECT 1 FROM project_executor_bindings
    WHERE kind = 'assistant' AND target_id = assistants.id
  )
  AND NOT EXISTS (
    SELECT 1 FROM project_todos
    WHERE assigned_assistant = assistants.id OR assigned_assistant = assistants.name
  );

DELETE FROM capability_bindings
WHERE assistant_id IN (
  'builtin-pony',
  'builtin-deepread',
  'builtin-topic',
  'builtin-proposal',
  'builtin-survey',
  'builtin-writingcoach',
  'builtin-entrans',
  'builtin-ml',
  'builtin-code',
  'builtin-experiment',
  'builtin-format',
  'builtin-refcheck',
  'builtin-response',
  'builtin-submit',
  'builtin-slides',
  'builtin-grant',
  'builtin-minutes',
  'builtin-tutor'
)
  AND NOT EXISTS (SELECT 1 FROM assistants WHERE id = capability_bindings.assistant_id);
