-- Preserve insertion order for ops_json_documents collections (append-style logs, campaigns)
ALTER TABLE ops_json_documents ADD COLUMN IF NOT EXISTS seq BIGSERIAL;

CREATE INDEX IF NOT EXISTS idx_ops_json_collection_seq ON ops_json_documents(collection, seq);
