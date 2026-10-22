-- v1.6 检索结果排序：相关度×引用量 分数，列表按它降序（旧的 0 分自然沉底）。

ALTER TABLE search_cache ADD COLUMN score REAL NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_search_cache_score ON search_cache(task_id, score DESC, created_at DESC);
