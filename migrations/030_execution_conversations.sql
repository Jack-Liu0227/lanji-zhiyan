CREATE TABLE execution_conversations (
  chat_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES research_tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('task','workflow')),
  entity_id TEXT NOT NULL,
  UNIQUE(kind,entity_id)
);
CREATE INDEX execution_conversations_project ON execution_conversations(project_id);
CREATE TABLE execution_conversation_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES project_tasks(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL,
  event_key TEXT NOT NULL UNIQUE,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX execution_conversation_events_run ON execution_conversation_events(run_id,id);

INSERT INTO chats(id,title,created_at,updated_at,task_id,assistant_id)
SELECT 'execution-'||t.id,t.title,t.created_at,t.updated_at,t.project_id,
       CASE WHEN b.kind='assistant' THEN b.target_id ELSE '' END
FROM project_tasks t LEFT JOIN project_executor_bindings b ON b.id=t.executor_binding_id;
INSERT INTO execution_conversations SELECT 'execution-'||id,project_id,'task',id FROM project_tasks;
INSERT INTO chats(id,title,created_at,updated_at,task_id)
SELECT 'execution-'||r.id,COALESCE(json_extract(e.layout_snapshot,'$.name'),'工作流'),r.started_at,e.updated_at,r.project_id
FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id;
INSERT INTO execution_conversations SELECT 'execution-'||r.id,r.project_id,'workflow',r.id
FROM workflow_runs r JOIN workflow_executions e ON e.run_id=r.id;

UPDATE assistants SET name=replace(name,'助手','专家') ;
UPDATE chats SET deleted_at=COALESCE((SELECT json_extract(t.metadata,'$.deleted_at') FROM project_tasks t WHERE 'execution-'||t.id=chats.id),'')
WHERE id IN (SELECT chat_id FROM execution_conversations WHERE kind='task');

INSERT OR IGNORE INTO execution_board_hidden(project_id,item_kind,item_id,deleted_at)
SELECT h.project_id,'conversation',t.owner_id,MIN(h.deleted_at)
FROM execution_board_hidden h JOIN tasks t ON t.id=h.item_id AND t.kind='agent_run'
WHERE h.item_kind='conversation'
GROUP BY h.project_id,t.owner_id
HAVING COUNT(*)=(SELECT COUNT(*) FROM tasks all_runs WHERE all_runs.owner_id=t.owner_id AND all_runs.kind='agent_run');
