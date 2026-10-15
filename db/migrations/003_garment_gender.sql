-- Garment intended gender tag (product attribute from Vertex extraction).
ALTER TABLE garment_instances
  ADD COLUMN IF NOT EXISTS gender TEXT;
