"""Load clearly-labelled SAMPLE data so the app has something to show.

The stores are sample locations near real Florida ZIP codes and the prices are
randomly generated. They are marked "sample" in the app. Delete grocery.db to start
clean, or run `python -m scripts.import_osm_stores <zip>` for real store locations.
"""
import random
import secrets
from datetime import datetime, timedelta, timezone

from werkzeug.security import generate_password_hash

from app import scoring
from app.db import get_conn, init_db
from app.geo import OFFLINE_ZIPS

# chain, store name, zip (for the center point), lat offset, lng offset
STORES = [
    ("Publix", "Publix Riverside", "32202", -0.02, -0.03),
    ("Publix", "Publix San Marco", "32207", 0.0, 0.0),
    ("Walmart", "Walmart Southside", "32246", 0.0, 0.0),
    ("Aldi", "Aldi Arlington", "32202", 0.01, 0.09),
    ("Winn-Dixie", "Winn-Dixie Springfield", "32202", 0.03, 0.0),
    ("Whole Foods", "Whole Foods Southbank", "32207", 0.01, 0.01),
    ("Publix", "Publix Jacksonville Beach", "32250", 0.0, 0.0),
    ("Publix", "Publix Brickell", "33101", -0.03, 0.0),
    ("Walmart", "Walmart Little Havana", "33133", 0.03, -0.02),
    ("Aldi", "Aldi Aventura", "33180", 0.0, 0.0),
    ("Winn-Dixie", "Winn-Dixie Miami Beach", "33139", 0.0, 0.0),
    ("Whole Foods", "Whole Foods Coconut Grove", "33133", 0.005, 0.01),
    ("Publix", "Publix Downtown Orlando", "32801", 0.0, 0.0),
    ("Walmart", "Walmart Dr. Phillips", "32819", 0.0, 0.0),
    ("Aldi", "Aldi Orlando East", "32803", 0.01, 0.02),
    ("Winn-Dixie", "Winn-Dixie Colonial", "32803", 0.02, -0.03),
    ("Whole Foods", "Whole Foods Winter Park", "32803", 0.06, 0.0),
    ("Publix", "Publix Hyde Park", "33609", 0.0, 0.0),
    ("Walmart", "Walmart New Tampa", "33647", 0.0, 0.0),
    ("Aldi", "Aldi Tampa Heights", "33602", 0.01, 0.03),
    ("Winn-Dixie", "Winn-Dixie Channelside", "33602", -0.02, 0.02),
    ("Whole Foods", "Whole Foods Carrollwood", "33609", 0.08, -0.02),
]

# name, category, unit, base price
PRODUCTS = [
    ("Milk, whole, 1 gal", "Dairy", "gal", 4.10), ("Eggs, large, 1 dozen", "Dairy", "dozen", 3.60),
    ("Butter, 1 lb", "Dairy", "lb", 4.80), ("Bread, white loaf", "Bakery", "loaf", 2.80),
    ("Bananas", "Produce", "lb", 0.65), ("Tomatoes", "Produce", "lb", 1.90),
    ("Chicken breast", "Meat", "lb", 3.90), ("Ground beef 80/20", "Meat", "lb", 5.60),
    ("Orange juice, 52 oz", "Beverages", "bottle", 4.30), ("Coffee, ground, 12 oz", "Beverages", "bag", 8.50),
    ("Rice, long grain, 2 lb", "Pantry", "bag", 2.50), ("Spaghetti, 1 lb", "Pantry", "box", 1.60),
]
CHAIN_FACTOR = {"Publix": 1.10, "Walmart": 0.92, "Aldi": 0.85, "Winn-Dixie": 1.00, "Whole Foods": 1.30}

# Sample shoppers who "reported" the recent prices. They can't sign in.
SHOPPERS = [("Maria S.", 1), ("Jay T.", 1), ("Priya K.", 0), ("Dev R.", 0)]


def main(days=60, seed=7):
    random.seed(seed)
    init_db()
    with get_conn() as c:
        if c.execute("SELECT COUNT(*) FROM stores WHERE lat IS NOT NULL").fetchone()[0]:
            print("Database already has stores with locations; skipping seed.")
            return
        # leftovers from the first version of the app had no locations
        c.execute("DELETE FROM stores WHERE lat IS NULL")

        for chain, name, zip5, dlat, dlng in STORES:
            lat, lng, city, _ = OFFLINE_ZIPS[zip5]
            c.execute("INSERT OR IGNORE INTO stores (name, chain, city, address, zip, lat, lng, source) "
                      "VALUES (?, ?, ?, '', ?, ?, ?, 'demo')",
                      (name, chain, city, zip5, lat + dlat, lng + dlng))
        for name, cat, unit, _ in PRODUCTS:
            c.execute("INSERT OR IGNORE INTO products (name, category, unit) VALUES (?, ?, ?)", (name, cat, unit))

        shopper_ids = []
        for name, _ in SHOPPERS:
            cur = c.execute(
                "INSERT INTO users (email, name, pw_hash, zip, city, lat, lng) VALUES (?, ?, ?, '32202', "
                "'Jacksonville', 30.3322, -81.6557)",
                (f"sample-{name.split()[0].lower()}@example.invalid", name,
                 generate_password_hash(secrets.token_hex(16))))
            shopper_ids.append(cur.lastrowid)

        stores = c.execute("SELECT * FROM stores").fetchall()
        products = c.execute("SELECT * FROM products").fetchall()
        base = {n: b for n, _, _, b in PRODUCTS}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        listings = []
        for p in products:
            for s in stores:
                if random.random() < 0.12:
                    continue   # not every store has every item
                c.execute("INSERT OR IGNORE INTO listings (product_id, store_id) VALUES (?, ?)", (p["id"], s["id"]))
                lid = c.execute("SELECT id FROM listings WHERE product_id = ? AND store_id = ?",
                                (p["id"], s["id"])).fetchone()["id"]
                listings.append((lid, p["name"], s["chain"]))
                price = base[p["name"]] * CHAIN_FACTOR[s["chain"]] * random.uniform(0.95, 1.05)
                for d in range(days, 3, -3):
                    price = max(0.3, price * random.uniform(0.97, 1.035))
                    ts = (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("INSERT INTO prices (listing_id, price, source, scraped_at) VALUES (?, ?, 'demo', ?)",
                              (lid, round(price, 2), ts))
                # most recent price: a sample shopper report for about half of the listings
                price = max(0.3, price * random.uniform(0.97, 1.03))
                if random.random() < 0.5:
                    uid = random.choice(shopper_ids)
                    ts = (now - timedelta(days=random.uniform(0.2, 6))).strftime("%Y-%m-%d %H:%M:%S")
                    cur = c.execute(
                        "INSERT INTO prices (listing_id, price, source, scraped_at, user_id, verified_count) "
                        "VALUES (?, ?, 'user', ?, ?, ?)", (lid, round(price, 2), ts, uid, random.choice([0, 0, 1, 2])))
                    scoring.award(c, uid, scoring.REPORT_POINTS, "report", cur.lastrowid)
                else:
                    ts = (now - timedelta(days=random.uniform(0.2, 3))).strftime("%Y-%m-%d %H:%M:%S")
                    c.execute("INSERT INTO prices (listing_id, price, source, scraped_at) VALUES (?, ?, 'demo', ?)",
                              (lid, round(price, 2), ts))

        # give the two most active sample shoppers a perk so badges are visible
        for uid in shopper_ids[:2]:
            r = c.execute("SELECT id, cost FROM rewards WHERE key = 'scout_badge'").fetchone()
            if c.execute("SELECT points FROM users WHERE id = ?", (uid,)).fetchone()["points"] >= r["cost"]:
                c.execute("INSERT INTO redemptions (user_id, reward_id, cost) VALUES (?, ?, ?)",
                          (uid, r["id"], r["cost"]))
                scoring.award(c, uid, -r["cost"], "redeem", r["id"])
        c.execute("INSERT INTO store_reviews (store_id, user_id, rating, comment) VALUES (?, ?, 4, ?)",
                  (stores[0]["id"], shopper_ids[0], "Clean store, good produce, short lines on weekday mornings."))
    print(f"Seeded {len(stores)} sample stores, {len(products)} items and prices.")


if __name__ == "__main__":
    main()
