-- Analytics tables for garment similarity (same dataset pattern as command center).
-- Cloud SQL remains the operational source of truth; BQ is for Admin / marts.
--
--   bq query --use_legacy_sql=false < similarity/scripts/create_bq_similarity_tables.sql
-- Or set PROJECT/DATASET and run via bq mk.

CREATE TABLE IF NOT EXISTS `suffai_ops.garment_similarity_observations` (
  event_ts TIMESTAMP,
  garment_id STRING,
  user_uuid STRING,
  pin_code STRING,
  brand_name STRING,
  outcome STRING,
  gcs_object_key STRING,
  similarity_score FLOAT64
);
