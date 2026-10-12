"""Load clearly-fake DEMO data so the UI has something to show.

These prices are randomly generated, not real. Delete grocery.db to start clean.
"""
import random
from datetime import datetime, timedelta

from app.db import get_conn, init_db

STORES = [("Publix", "Jacksonville"), ("Publix", "Miami"), ("Walmart", "Jacksonville"),
          ("Aldi", "Orlando"), ("Winn-Dixie", "Tampa"), ("Whole Foods", "Miami")]
# name, category, unit, base price
PRODUCTS = [("Milk, whole, 1 gal", "Dairy", "gal", 4.10), ("Eggs, large, 1 dozen", "Dairy", "dozen", 3.60),
            ("Bread, white loaf", "Bakery", "loaf", 2.80), ("Bananas", "Produce", "lb", 0.65),
            ("Chicken breast", "Meat", "lb", 3.90), ("Ground beef 80/20", "Meat", "lb", 5.60),
            ("Orange juice, 52 oz", "Beverages", "bottle", 4.30), ("Rice, long grain, 2 lb", "Pantry", "bag", 2.50)]
CHAIN_FACTOR = {"Publix": 1.10, "Walmart": 0.92, "Aldi": 0.85, "Winn-Dixie": 1.00, "Whole Foods": 1.30}


def main(days=60, seed=7):
    random.seed(seed)
    init_db()
    with get_conn() as c:
        if c.execute("SELECT COUNT(*) FROM prices").fetchone()[0]:
            print("Database already has prices; skipping seed.")
            return
        for chain, city in STORES:
            c.execute("INSERT OR IGNORE INTO stores (name, chain, city) VALUES (?,?,?)",
                      (f"{chain} ({city})", chain, city))
        for name, cat, unit, _ in PRODUCTS:
            c.execute("INSERT OR IGNORE INTO products (name, category, unit) VALUES (?,?,?)", (name, cat, unit))
        stores = c.execute("SELECT * FROM stores").fetchall()
        products = c.execute("SELECT * FROM products").fetchall()
        base = {n: b for n, _, _, b in PRODUCTS}
        now = datetime.now()
        for p in products:
            for s in stores:
                if random.random() < 0.15:
                    continue  # not every store has every item
                c.execute("INSERT OR IGNORE INTO listings (product_id, store_id) VALUES (?,?)", (p["id"], s["id"]))
                lid = c.execute("SELECT id FROM listings WHERE product_id=? AND store_id=?",
                                (p["id"], s["id"])).fetchone()["id"]
                price = base[p["name"]] * CHAIN_FACTOR[s["chain"]] * random.uniform(0.95, 1.05)
                for d in range(days, -1, -3):
                    price = max(0.3, price * random.uniform(0.97, 1.035))
                    ts = (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("INSERT INTO prices (listing_id, price, source, scraped_at) VALUES (?,?,?,?)",
                              (lid, round(price, 2), "demo", ts))
        egg = c.execute("SELECT id FROM products WHERE name LIKE 'Eggs%'").fetchone()["id"]
        c.execute("INSERT INTO alerts (product_id, target_price) VALUES (?, 3.50)", (egg,))
        for pid in (1, 2, 3):
            c.execute("INSERT OR IGNORE INTO list_items (product_id, qty) VALUES (?, 1)", (pid,))
    print("Seeded demo data.")


if __name__ == "__main__":
    main()
