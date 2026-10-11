-- OpenJM Enterprise AI - QA1 governed structured-data fixture (synthetic only).
--
-- Applied to a dedicated PostgreSQL database `qa_demo` in the QA1 PostgreSQL
-- container. Registered as an OpenJM governed structured source. All rows are
-- synthetic; no real personal or production data is present.

DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS employee_summary;

CREATE TABLE customers (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    region    TEXT NOT NULL,
    tier      TEXT NOT NULL
);

CREATE TABLE orders (
    id          INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    order_date  DATE NOT NULL,
    status      TEXT NOT NULL,
    amount      NUMERIC(12,2) NOT NULL
);

CREATE TABLE inventory (
    id        INTEGER PRIMARY KEY,
    sku       TEXT NOT NULL,
    name      TEXT NOT NULL,
    quantity  INTEGER NOT NULL,
    unit_cost NUMERIC(12,2) NOT NULL
);

-- Synthetic, non-identifying compensation demo. The FY2025 discretionary bonus
-- target for the HR band is a deterministic fact for governed retrieval tests.
CREATE TABLE employee_summary (
    id               INTEGER PRIMARY KEY,
    employee_ref     TEXT NOT NULL,
    department       TEXT NOT NULL,
    job_band         TEXT NOT NULL,
    base_salary      NUMERIC(12,2) NOT NULL,
    bonus_target_pct NUMERIC(5,2) NOT NULL,
    fiscal_year      TEXT NOT NULL
);

INSERT INTO customers (id, name, region, tier) VALUES
    (1, 'Blue Mountain Cafe', 'Kingston', 'gold'),
    (2, 'Island Retail Ltd', 'Montego Bay', 'silver'),
    (3, 'Harbour Grocers', 'Port Royal', 'silver');

INSERT INTO orders (id, customer_id, order_date, status, amount) VALUES
    (1001, 1, '2026-09-01', 'completed', 325.00),
    (1002, 1, '2026-09-14', 'completed', 180.50),
    (1003, 2, '2026-09-20', 'pending', 910.75),
    (1004, 3, '2026-10-02', 'completed', 42.00);

INSERT INTO inventory (id, sku, name, quantity, unit_cost) VALUES
    (1, 'COF-001', 'Blue Mountain Coffee', 120, 18.75),
    (2, 'TEA-001', 'Mango Tea', 400, 3.40),
    (3, 'TEA-002', 'Ginger Tea', 260, 4.10);

INSERT INTO employee_summary (id, employee_ref, department, job_band, base_salary, bonus_target_pct, fiscal_year) VALUES
    (1, 'EMP-1001', 'General', 'band-2', 64000.00, 10.00, 'FY2025'),
    (2, 'EMP-2001', 'HR',      'band-4', 98000.00, 25.00, 'FY2025'),
    (3, 'EMP-3001', 'Finance', 'band-4', 101000.00, 25.00, 'FY2025');
