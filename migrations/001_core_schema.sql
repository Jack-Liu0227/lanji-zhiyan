CREATE TABLE IF NOT EXISTS research_tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '',
  material_system TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT 'definition',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, base_dir TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS chats (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, out_dir TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  assistant_id TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, filename TEXT NOT NULL,
  source_path TEXT NOT NULL, sha256 TEXT NOT NULL, pages INTEGER NOT NULL,
  text_chars INTEGER NOT NULL, ocr_status TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS paper_pages (
  paper_id TEXT NOT NULL, page_no INTEGER NOT NULL, text TEXT NOT NULL,
  PRIMARY KEY(paper_id, page_no), FOREIGN KEY(paper_id) REFERENCES papers(id)
);
CREATE TABLE IF NOT EXISTS evidence (
  id TEXT PRIMARY KEY, paper_id TEXT NOT NULL, page_no INTEGER NOT NULL,
  field TEXT NOT NULL, value TEXT, excerpt TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'fact',
  created_at TEXT NOT NULL, FOREIGN KEY(paper_id) REFERENCES papers(id)
);
CREATE TABLE IF NOT EXISTS artifacts (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
  sha256 TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, command TEXT NOT NULL,
  status TEXT NOT NULL, input_hash TEXT NOT NULL, created_at TEXT NOT NULL,
  FOREIGN KEY(task_id) REFERENCES research_tasks(id)
);
CREATE TABLE IF NOT EXISTS rss_sources (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
  site TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '学术论文',
  enabled INTEGER NOT NULL DEFAULT 1, in_daily INTEGER NOT NULL DEFAULT 1,
  last_error TEXT NOT NULL DEFAULT '', last_fetch TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rss_items (
  id TEXT PRIMARY KEY, source_id TEXT NOT NULL, link TEXT NOT NULL,
  title TEXT NOT NULL, authors TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
  published_at TEXT NOT NULL DEFAULT '', source_title TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '', fetched_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS rss_items_link ON rss_items(link);
