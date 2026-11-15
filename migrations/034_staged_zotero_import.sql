-- Downloaded papers stay in the project staging queue until an explicit Zotero import.
ALTER TABLE dl_queue ADD COLUMN zotero_status TEXT NOT NULL DEFAULT 'staged';
ALTER TABLE dl_queue ADD COLUMN zotero_key TEXT NOT NULL DEFAULT '';
ALTER TABLE dl_queue ADD COLUMN zotero_attachment_key TEXT NOT NULL DEFAULT '';
ALTER TABLE dl_queue ADD COLUMN zotero_error TEXT NOT NULL DEFAULT '';
ALTER TABLE dl_queue ADD COLUMN zotero_updated_at TEXT NOT NULL DEFAULT '';

-- Records imported from Zotero are already managed externally, not pending export.
UPDATE dl_queue
SET zotero_status='external', zotero_updated_at=updated_at
WHERE result_id IN (SELECT id FROM search_cache WHERE source='zotero');
