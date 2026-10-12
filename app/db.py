import os
import sqlite3

DB_PATH = os.environ.get(
    "GROCERY_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "grocery.db"),
)
SCHEMA_VERSION = 2


def _stores_ddl(name="stores"):
    return f"""
CREATE TABLE IF NOT EXISTS {name} (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    chain TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    zip TEXT,
    lat REAL,
    lng REAL,
    source TEXT NOT NULL DEFAULT 'user',   -- demo | osm | user
    created_by INTEGER,
    UNIQUE(name, zip, address)
);
"""


CORE_SCHEMA = _stores_ddl() + """
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
    source TEXT NOT NULL DEFAULT 'scrape',  -- scrape | user | demo
    scraped_at TEXT NOT NULL DEFAULT (datetime('now')),
    user_id INTEGER,
    status TEXT NOT NULL DEFAULT 'ok',      -- ok | pending (unusual, needs 2 confirmations)
    verified_count INTEGER NOT NULL DEFAULT 0,
    disputed_count INTEGER NOT NULL DEFAULT 0,
    corrects_price_id INTEGER,
    confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_prices_listing ON prices(listing_id, scraped_at);
"""

EXTRA_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    zip TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    radius_miles INTEGER NOT NULL DEFAULT 10,
    points INTEGER NOT NULL DEFAULT 0,       -- spendable balance
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS price_votes (
    id INTEGER PRIMARY KEY,
    price_id INTEGER NOT NULL REFERENCES prices(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vote TEXT NOT NULL DEFAULT 'confirm',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(price_id, user_id)
);
CREATE TABLE IF NOT EXISTS points_ledger (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON points_ledger(user_id, reason, created_at);
CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    cost INTEGER NOT NULL,
    kind TEXT NOT NULL                        -- badge | theme | flair
);
CREATE TABLE IF NOT EXISTS redemptions (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    reward_id INTEGER NOT NULL REFERENCES rewards(id),
    cost INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, reward_id)
);
CREATE TABLE IF NOT EXISTS store_reviews (
    id INTEGER PRIMARY KEY,
    store_id INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(store_id, user_id)
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS zip_cache (
    zip TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    city TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS list_items (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    qty INTEGER NOT NULL DEFAULT 1,
    checked INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, product_id)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    target_price REAL NOT NULL
);
"""

REWARDS = [
    ("scout_badge", "Scout badge", "Shows a Scout badge next to your name on reviews and price reports.", 50, "badge"),
    ("citrus_theme", "Citrus theme", "Switches the app to a warm citrus accent color.", 120, "theme"),
    ("gold_flair", "Gold name", "Shows your name in gold on reviews and price reports.", 300, "flair"),
    ("legend_badge", "Legend badge", "Shows a Legend badge next to your name. It replaces the Scout badge.", 600, "badge"),
]


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate_v1():
    """Upgrade a version-1 database (single-user demo app) in place.

    Prices and products are kept. The old global shopping list and alerts are
    dropped because lists and alerts are now per user.
    """
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN")
        conn.execute(_stores_ddl("stores_new").strip().rstrip(";"))
        conn.execute("INSERT INTO stores_new (id, name, chain, city, source) "
                     "SELECT id, name, chain, city, 'demo' FROM stores")
        conn.execute("DROP TABLE stores")
        conn.execute("ALTER TABLE stores_new RENAME TO stores")
        have = {r[1] for r in conn.execute("PRAGMA table_info(prices)")}
        for col, ddl in [
            ("user_id", "INTEGER"),
            ("status", "TEXT NOT NULL DEFAULT 'ok'"),
            ("verified_count", "INTEGER NOT NULL DEFAULT 0"),
            ("disputed_count", "INTEGER NOT NULL DEFAULT 0"),
            ("corrects_price_id", "INTEGER"),
            ("confirmed_at", "TEXT"),
        ]:
            if col not in have:
                conn.execute(f"ALTER TABLE prices ADD COLUMN {col} {ddl}")
        conn.execute("DROP TABLE IF EXISTS list_items")
        conn.execute("DROP TABLE IF EXISTS alerts")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        has_stores = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='stores'").fetchone()
    finally:
        conn.close()
    if has_stores and version < SCHEMA_VERSION:
        _migrate_v1()
    with get_conn() as c:
        c.executescript(CORE_SCHEMA)
        c.executescript(EXTRA_SCHEMA)
        for key, name, desc, cost, kind in REWARDS:
            c.execute("INSERT OR IGNORE INTO rewards (key, name, description, cost, kind) "
                      "VALUES (?, ?, ?, ?, ?)", (key, name, desc, cost, kind))
        c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


# Latest accepted price for every product/store pair. "pending" prices (unusual
# values waiting for confirmation) are left out so they can't skew comparisons.
LATEST_PRICES_SQL = """
SELECT l.id AS listing_id, l.product_id, s.id AS store_id, s.name AS store,
       s.chain, s.city, s.zip, s.lat, s.lng, s.source AS store_source,
       p.id AS price_id, p.price, p.scraped_at, p.source,
       p.verified_count, p.user_id,
       COALESCE(p.confirmed_at, p.scraped_at) AS seen_at
FROM listings l
JOIN stores s ON s.id = l.store_id
JOIN prices p ON p.id = (
    SELECT id FROM prices WHERE listing_id = l.id AND status = 'ok'
    ORDER BY scraped_at DESC, id DESC LIMIT 1)
"""
