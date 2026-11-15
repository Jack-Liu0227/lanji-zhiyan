-- A binding identifies a library; collection is only the optional destination for NEW items.
CREATE TABLE IF NOT EXISTS zotero_bindings (
  task_id TEXT PRIMARY KEY, library TEXT NOT NULL, collection TEXT NOT NULL,
  last_sync TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS zotero_project_sources (
  task_id TEXT NOT NULL, library TEXT NOT NULL, collection TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'selected' CHECK(mode IN ('selected','all')),
  PRIMARY KEY(task_id, library, collection));
-- Preserve explicitly configured legacy whole-collection synchronization.
INSERT OR IGNORE INTO zotero_project_sources (task_id,library,collection,mode)
SELECT task_id,library,collection,'all' FROM zotero_bindings WHERE collection!='';
