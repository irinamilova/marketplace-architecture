ALTER TABLE export_queue ADD COLUMN IF NOT EXISTS exported_at timestamptz;
