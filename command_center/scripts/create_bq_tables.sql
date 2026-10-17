-- Explicit BQ tables for the command-center dashboard (suffai_ops).
-- Does not modify existing suffai_staging / suffai_marts star schema.
-- Run via: bq query --use_legacy_sql=false < scripts/create_bq_tables.sql
-- Or load_to_bq.sh (creates dataset + loads CSVs).

CREATE TABLE IF NOT EXISTS suffai_ops.customers (
  _id STRING,
  name STRING,
  number STRING,
  address STRING,
  age INT64,
  gender STRING,
  pin_code STRING,
  rfm_segment STRING,
  lat FLOAT64,
  lon FLOAT64
);

CREATE TABLE IF NOT EXISTS suffai_ops.orders (
  _id STRING,
  customer_id STRING,
  scheduled_date DATE,
  service STRING,
  total_number_of_clothes INT64,
  amount FLOAT64
);

-- Optional for taste panels (color/size/brand/etc.)
CREATE TABLE IF NOT EXISTS suffai_ops.commodities (
  customer_id STRING,
  order_id STRING,
  commodity_id STRING,
  name STRING,
  color STRING,
  brand STRING,
  pattern STRING,
  size STRING,
  confidence_score FLOAT64,
  reason STRING,
  sleeve_type STRING,
  collar_type STRING,
  gender STRING,
  material STRING
);

-- Role-based Command Center login (load via login_cred_db.csv)
CREATE TABLE IF NOT EXISTS suffai_ops.login_cred_db (
  username STRING NOT NULL,
  password_hash STRING NOT NULL,
  role STRING NOT NULL,
  brand_name STRING,
  customer_id STRING,
  display_name STRING,
  active BOOL,
  created_at TIMESTAMP
);
