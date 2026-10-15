-- Customer profiles (customer_uuid == garment image user_uuid / source_customer_id).
CREATE TABLE IF NOT EXISTS customers (
  customer_uuid TEXT PRIMARY KEY,
  user_id TEXT,
  gender TEXT,
  age INT,
  pin VARCHAR(6),
  address TEXT,
  phone_number TEXT,
  device_type TEXT,
  customer_name TEXT,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customers_pin ON customers(pin);
CREATE INDEX IF NOT EXISTS idx_customers_gender ON customers(gender);
CREATE INDEX IF NOT EXISTS idx_customers_device ON customers(device_type);
CREATE INDEX IF NOT EXISTS idx_customers_user_id ON customers(user_id);
