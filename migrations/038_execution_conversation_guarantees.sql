-- Repair the durable one-to-one projection without touching execution data.
INSERT OR IGNORE INTO execution_conversations(chat_id,project_id,kind,entity_id)
SELECT 'execution-'||t.id,t.project_id,'task',t.id
FROM project_tasks t;

INSERT OR IGNORE INTO execution_conversations(chat_id,project_id,kind,entity_id)
SELECT 'execution-'||r.id,r.project_id,'workflow',r.id
FROM workflow_runs r
JOIN workflow_executions e ON e.run_id=r.id;

CREATE INDEX execution_board_conversation_lifecycle ON execution_board_events(project_id,task_id,id)
WHERE kind IN ('conversation_trash','conversation_restore','conversation_archive','conversation_unarchive','conversation_purge');

WITH executions(chat_id,project_id,title,created_at,updated_at) AS (
    SELECT e.chat_id,t.project_id,t.title,t.created_at,t.updated_at
    FROM execution_conversations e JOIN project_tasks t ON e.kind='task' AND e.entity_id=t.id
    UNION ALL
    SELECT e.chat_id,r.project_id,COALESCE(json_extract(x.layout_snapshot,'$.name'),'工作流'),r.started_at,x.updated_at
    FROM execution_conversations e JOIN workflow_runs r ON e.kind='workflow' AND e.entity_id=r.id
    JOIN workflow_executions x ON x.run_id=r.id
)
INSERT OR IGNORE INTO chats(id,title,task_id,created_at,updated_at,deleted_at,archived_at)
SELECT e.chat_id,e.title,e.project_id,e.created_at,e.updated_at,
       CASE WHEN latest.kind='conversation_trash' THEN latest.created_at ELSE '' END,
       CASE WHEN latest.kind='conversation_archive' THEN latest.created_at ELSE '' END
FROM executions e
LEFT JOIN chats c ON c.id=e.chat_id
LEFT JOIN execution_board_events latest ON latest.id=(
    SELECT id FROM execution_board_events
    WHERE project_id=e.project_id AND task_id=e.chat_id
      AND kind IN ('conversation_trash','conversation_restore','conversation_archive','conversation_unarchive','conversation_purge')
    ORDER BY id DESC LIMIT 1
)
WHERE c.id IS NULL AND COALESCE(latest.kind,'')<>'conversation_purge';
