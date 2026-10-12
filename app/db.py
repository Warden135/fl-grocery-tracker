import os
import sqlite3

DB_PATH = os.environ.get(
    "GROCERY_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grocery.db"),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS stores (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    chain TEXT NOT NULL,
    city TEXT NOT NULL,
    UNIQUE(chain, city)
);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    category TEXT,
    unit TEXT
);
-- A listing = "this product at this store". url/selector are used by the scraper.
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    url TEXT,
    selector TEXT,
    UNIQUE(product_id, store_id)
);
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    listing_id INTEGER NOT NULL REFERENCES listings(id) ON DELETE CASCADE,
    price REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'scrape',
    scraped_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_prices_listing ON prices(listing_id, scraped_at);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    target_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL UNIQUE REFERENCES products(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL DEFAULT 1
);
"""


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_conn() as c:
        c.executescript(SCHEMA)


LATEST_PRICES_SQL = """
SELECT l.id AS listing_id, l.product_id, s.id AS store_id, s.name AS store,
       s.chain, s.city, p.price, p.scraped_at
FROM listings l
JOIN stores s ON s.id = l.store_id
JOIN prices p ON p.id = (
    SELECT id FROM prices WHERE listing_id = l.id
    ORDER BY scraped_at DESC, id DESC LIMIT 1)
"""
