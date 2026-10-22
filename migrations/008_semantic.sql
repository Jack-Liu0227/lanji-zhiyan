-- v1.7 语义检索层：分块表 + 内容哈希台账 + 词法腿（FTS5 trigram）。
-- 设计口径：索引是派生数据，内容哈希不变就不重嵌，所以任何写入方都不用挂钩子。

CREATE TABLE IF NOT EXISTS sem_chunks (
  id TEXT PRIMARY KEY,
  source_kind TEXT NOT NULL,              -- paper / note / md_note / evidence
  source_id TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  page_no INTEGER NOT NULL DEFAULT 0,     -- 论文按页切；笔记/证据为 0
  ord INTEGER NOT NULL DEFAULT 0,
  title TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  dim INTEGER NOT NULL DEFAULT 0,
  vec BLOB,                               -- float32 单位向量（行归一化，余弦=点积）
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sem_chunks_source ON sem_chunks(source_kind, source_id);
CREATE INDEX IF NOT EXISTS idx_sem_chunks_project ON sem_chunks(project_id, source_kind);

-- 台账：一个源（论文/笔记）一行。hash+model 都一致才认为已索引，换模型自动全量重嵌。
CREATE TABLE IF NOT EXISTS sem_state (
  source_kind TEXT NOT NULL,
  source_id TEXT NOT NULL,
  project_id TEXT NOT NULL DEFAULT '',
  content_hash TEXT NOT NULL,
  model TEXT NOT NULL DEFAULT '',
  dim INTEGER NOT NULL DEFAULT 0,
  chunks INTEGER NOT NULL DEFAULT 0,
  indexed_at TEXT NOT NULL,
  PRIMARY KEY (source_kind, source_id)
);

-- 词法腿：trigram 让中文也能切（unicode61 会把整段中文当一个 token）。
-- rowid 与 sem_chunks.rowid 对齐，就是「第二张倒排表」，不做 external content（省那点空间不值当）。
-- ponytail: trigram 要求查询 ≥3 字符；2 字中文查询由 semantic.lexical 退回 LIKE 子串扫。
CREATE VIRTUAL TABLE IF NOT EXISTS sem_fts USING fts5(
  text,
  tokenize = 'trigram'
);
