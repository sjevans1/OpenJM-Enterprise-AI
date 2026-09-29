"""Create the deterministic SQLite acceptance database for Vertical Slice 2."""

from pathlib import Path
import sqlite3


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = REPO_ROOT / "data" / "structured-demo.db"


SCHEMA_AND_DATA = """
PRAGMA foreign_keys = ON;

DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL,
    order_date TEXT NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY(customer_id) REFERENCES customers(id)
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price REAL NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(id),
    FOREIGN KEY(product_id) REFERENCES products(id)
);

INSERT INTO customers (id, name, region) VALUES
    (1, 'Blue Mountain Cafe', 'Kingston'),
    (2, 'Island Retail Ltd', 'Montego Bay');

INSERT INTO products (id, sku, name, category) VALUES
    (1, 'COF-001', 'Blue Mountain Coffee', 'Coffee'),
    (2, 'TEA-001', 'Mango Tea', 'Tea'),
    (3, 'TEA-002', 'Ginger Tea', 'Tea');

INSERT INTO orders (id, customer_id, order_date, status) VALUES
    (1001, 1, '2026-09-01', 'completed'),
    (1002, 1, '2026-09-15', 'completed'),
    (1003, 2, '2026-09-20', 'completed');

INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES
    (1, 1001, 1, 4, 25.00),
    (2, 1001, 2, 5, 12.00),
    (3, 1002, 1, 3, 25.00),
    (4, 1002, 3, 9, 10.00),
    (5, 1003, 2, 10, 12.00),
    (6, 1003, 3, 2, 10.00);
"""


def create_demo_database(path: Path = DEFAULT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA_AND_DATA)
        connection.commit()
    finally:
        connection.close()
    return path


if __name__ == "__main__":
    database = create_demo_database()
    print(f"Created Vertical Slice 2 demo database: {database}")
    print("Acceptance fact: Blue Mountain Cafe revenue = 325.00")
    print("Acceptance fact: highest-selling product by revenue = Mango Tea (180.00)")
