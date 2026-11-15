-- Keep the legacy six-column download queue compatible with direct inserts.
CREATE TABLE IF NOT EXISTS zotero_staging (
  task_id TEXT NOT NULL,
  result_id TEXT NOT NULL,
  zotero_status TEXT NOT NULL DEFAULT 'staged',
  zotero_key TEXT NOT NULL DEFAULT '',
  zotero_attachment_key TEXT NOT NULL DEFAULT '',
  zotero_error TEXT NOT NULL DEFAULT '',
  zotero_updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (task_id, result_id)
);

INSERT OR REPLACE INTO zotero_staging
  (task_id,result_id,zotero_status,zotero_key,zotero_attachment_key,zotero_error,zotero_updated_at)
SELECT task_id,result_id,zotero_status,zotero_key,zotero_attachment_key,zotero_error,zotero_updated_at
FROM dl_queue;

CREATE TABLE dl_queue_legacy (
  task_id TEXT NOT NULL,
  result_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  path TEXT NOT NULL DEFAULT '',
  error TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  PRIMARY KEY (task_id, result_id)
);

INSERT INTO dl_queue_legacy (task_id,result_id,status,path,error,updated_at)
SELECT task_id,result_id,status,path,error,updated_at FROM dl_queue;
DROP TABLE dl_queue;
ALTER TABLE dl_queue_legacy RENAME TO dl_queue;
