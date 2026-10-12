import os
import sqlite3

from app import db, scoring
from app.basket import build_basket
from app.geo import haversine_miles


def row(pid, sid, store, price, lat, lng, seen="2026-10-10 12:00:00"):
    return {"product_id": pid, "store_id": sid, "store": store, "chain": store, "price": price,
            "lat": lat, "lng": lng, "store_source": "user", "price_id": pid * 100 + sid,
            "verified_count": 0, "seen_at": seen}


ITEMS = [{"product_id": 1, "name": "Milk", "qty": 1}, {"product_id": 2, "name": "Eggs", "qty": 2}]
HERE = (30.0, -81.0)


def test_haversine():
    assert abs(haversine_miles(30.0, -81.0, 30.0, -81.0)) < 1e-9
    assert 68 < haversine_miles(30.0, -81.0, 31.0, -81.0) < 70      # one degree of latitude


def test_basket_picks_cheapest_and_flags_missing():
    rows = [
        row(1, 1, "A", 4.00, 30.01, -81.0), row(2, 1, "A", 3.00, 30.01, -81.0),   # A: 4 + 6 = 10
        row(1, 2, "B", 3.50, 30.02, -81.0),                                       # B: milk only
        row(2, 3, "C", 2.00, 30.03, -81.0), row(1, 3, "C", 4.50, 30.03, -81.0),   # C: 4.5 + 4 = 8.5
        row(1, 4, "Far", 0.50, 31.0, -81.0),                                      # outside radius
    ]
    r = build_basket(ITEMS, rows, HERE, 10)
    assert [s["store"] for s in r["stores"]] == ["C", "A", "B"]
    assert r["stores"][0]["total"] == 8.5 and r["stores"][-1]["missing"] == 1
    assert r["per_item"][1]["best"]["store"] == "B"
    p = {x["stops"]: x for x in r["plans"]}
    assert p[1]["total"] == 8.5
    assert p[2]["total"] == 3.5 + 4.0                      # milk at B, eggs at C
    assert p[2]["savings"] == 1.0
    assert all(s["items"] for s in p[2]["stores"])


def test_item_nobody_carries_is_set_aside():
    rows = [row(1, 1, "A", 4.00, 30.01, -81.0)]
    r = build_basket(ITEMS, rows, HERE, 10)          # eggs (product 2) has no price anywhere
    assert [u["name"] for u in r["unpriced"]] == ["Eggs"]
    assert r["priced_count"] == 1 and r["item_count"] == 2
    assert r["stores"][0]["complete"] and r["stores"][0]["total"] == 4.0
    assert r["per_item"][2]["best"] is None


def test_plan_prefers_closer_store_on_ties():
    rows = [row(1, 1, "Near", 2.0, 30.01, -81.0), row(1, 2, "Far", 2.0, 30.10, -81.0)]
    r = build_basket([ITEMS[0]], rows, HERE, 15)
    assert r["plans"][0]["stores"][0]["store"] == "Near"


def test_basket_empty_inputs():
    assert build_basket([], [], HERE, 10)["plans"] == []
    assert build_basket(ITEMS, [], HERE, 10)["best"] is None


def test_scoring_helpers():
    assert scoring.level_for(0)["name"] == "Newcomer"
    assert scoring.level_for(50)["name"] == "Scout" and scoring.level_for(50)["next_at"] == 150
    assert scoring.level_for(9999)["next_name"] is None
    assert scoring.median([3, 1, 2]) == 2 and scoring.median([1, 2, 3, 4]) == 2.5
    assert scoring.median([]) is None
    assert scoring.is_plausible(4.0, 4.0) and scoring.is_plausible(8.0, 4.0)
    assert not scoring.is_plausible(8.01, 4.0) and not scoring.is_plausible(1.9, 4.0)
    assert scoring.is_plausible(100, None)


def test_migration_from_v1(tmp_path, monkeypatch):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT NOT NULL, chain TEXT NOT NULL,
                             city TEXT NOT NULL, UNIQUE(chain, city));
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, category TEXT, unit TEXT);
        CREATE TABLE listings (id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
                               store_id INTEGER NOT NULL REFERENCES stores(id), url TEXT, selector TEXT,
                               UNIQUE(product_id, store_id));
        CREATE TABLE prices (id INTEGER PRIMARY KEY, listing_id INTEGER NOT NULL REFERENCES listings(id),
                             price REAL NOT NULL, source TEXT NOT NULL DEFAULT 'scrape',
                             scraped_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE alerts (id INTEGER PRIMARY KEY, product_id INTEGER, target_price REAL);
        CREATE TABLE list_items (id INTEGER PRIMARY KEY, product_id INTEGER UNIQUE, qty INTEGER);
        INSERT INTO stores VALUES (1, 'Publix (Tampa)', 'Publix', 'Tampa');
        INSERT INTO products VALUES (1, 'Milk', 'Dairy', 'gal');
        INSERT INTO listings (id, product_id, store_id) VALUES (1, 1, 1);
        INSERT INTO prices (listing_id, price, source) VALUES (1, 3.99, 'user');
        INSERT INTO list_items VALUES (1, 1, 2);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()                                   # running twice is safe
    with db.get_conn() as c:
        assert c.execute("SELECT name, source, lat FROM stores").fetchone()["source"] == "demo"
        p = c.execute("SELECT price, status, verified_count FROM prices").fetchone()
        assert (p["price"], p["status"], p["verified_count"]) == (3.99, "ok", 0)
        cols = {r[1] for r in c.execute("PRAGMA table_info(list_items)")}
        assert "user_id" in cols                                        # rebuilt per-user
        assert c.execute("SELECT COUNT(*) FROM list_items").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM rewards").fetchone()[0] == 4
        assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        # foreign keys from listings still resolve after the stores table was rebuilt
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_osm_import_parse_and_save(tmp_path, monkeypatch):
    from scripts import import_osm_stores as osm
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "osm.db"))
    data = {"elements": [
        {"type": "node", "id": 1, "lat": 30.30, "lon": -81.60,
         "tags": {"name": "Publix", "brand": "Publix", "addr:housenumber": "10", "addr:street": "Main St",
                  "addr:postcode": "32202-1234"}},
        {"type": "way", "id": 2, "center": {"lat": 30.31, "lon": -81.61}, "tags": {"name": "Aldi"}},
        {"type": "node", "id": 3, "lat": 30.0, "lon": -81.0, "tags": {}},                 # no name
        {"type": "node", "id": 4, "tags": {"name": "No location"}},
    ]}
    stores = osm.parse_elements(data)
    assert [s["name"] for s in stores] == ["Publix", "Aldi"]
    assert stores[0]["address"] == "10 Main St" and stores[0]["zip"] == "32202"
    assert stores[1]["lat"] == 30.31
    assert osm.save(stores) == 2
    assert osm.save(stores) == 0                      # running again adds nothing
    assert 'around:16093,30.0,-81.0' in osm.build_query(30.0, -81.0, 16093.4)
