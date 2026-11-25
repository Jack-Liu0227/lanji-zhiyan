ALTER TABLE sem_state ADD COLUMN embedding_id TEXT NOT NULL DEFAULT '';
ALTER TABLE sem_chunks ADD COLUMN embedding_id TEXT NOT NULL DEFAULT '';
CREATE INDEX sem_embedding_vector ON sem_chunks(embedding_id, dim);
