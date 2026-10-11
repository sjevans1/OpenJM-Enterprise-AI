-- OpenJM Enterprise AI - QA2 tenant-visible structured fixture (synthetic only).
--
-- Applied to a dedicated PostgreSQL database `qa_ops_demo` and registered as a
-- governed structured source with classification `internal` (tenant-visible), so
-- an ordinary tenant member has an authorized structured source for QA2 end-user
-- journeys. It deliberately contains NO compensation or HR data: the restricted
-- fixture (qa_demo.sql) remains the source for negative authorization testing.

DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS inventory;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    region TEXT NOT NULL,
    tier   TEXT NOT NULL
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
