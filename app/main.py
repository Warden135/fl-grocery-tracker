import hmac
import os
from collections import defaultdict

from flask import Flask, Response, jsonify, request

from .db import LATEST_PRICES_SQL, get_conn, init_db
from .scraper import scrape_all

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static")
app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")


@app.before_request
def _require_password():
    """If APP_PASSWORD is set, every route needs HTTP Basic auth (any username)."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected:
        return None
    auth = request.authorization
    if auth and auth.password is not None and hmac.compare_digest(
            auth.password.encode(), expected.encode()):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Grocery Tracker"'})


@app.before_request
def _ensure_db():
    if not getattr(app, "_db_ready", False):
        init_db()
        app._db_ready = True


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


@app.get("/")
def index():
    return app.send_static_file("index.html")


# ---------- products / stores ----------

@app.get("/api/products")
def products():
    q = "%" + request.args.get("q", "").strip() + "%"
    with get_conn() as c:
        data = rows(c.execute(
            """SELECT p.*, MIN(lp.price) AS lowest_price, COUNT(lp.listing_id) AS store_count
               FROM products p
               LEFT JOIN (""" + LATEST_PRICES_SQL + """) lp ON lp.product_id = p.id
               WHERE p.name LIKE ? GROUP BY p.id ORDER BY p.category, p.name""", (q,)))
    return jsonify(data)


@app.post("/api/products")
def add_product():
    body = request.get_json(force=True)
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify(error="name required"), 400
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO products (name, category, unit) VALUES (?, ?, ?)",
                  (name, body.get("category"), body.get("unit")))
        row = c.execute("SELECT * FROM products WHERE name = ?", (name,)).fetchone()
    return jsonify(dict(row)), 201


@app.get("/api/stores")
def stores():
    with get_conn() as c:
        return jsonify(rows(c.execute("SELECT * FROM stores ORDER BY chain, city")))


@app.post("/api/stores")
def add_store():
    body = request.get_json(force=True)
    chain, city = (body.get("chain") or "").strip(), (body.get("city") or "").strip()
    if not chain or not city:
        return jsonify(error="chain and city required"), 400
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO stores (name, chain, city) VALUES (?, ?, ?)",
                  (f"{chain} ({city})", chain, city))
        row = c.execute("SELECT * FROM stores WHERE chain=? AND city=?", (chain, city)).fetchone()
    return jsonify(dict(row)), 201


# ---------- prices ----------

@app.post("/api/prices")
def submit_price():
    """Manually submit/log a price: {product_id, store_id, price}."""
    b = request.get_json(force=True)
    try:
        product_id, store_id, price = int(b["product_id"]), int(b["store_id"]), float(b["price"])
    except (KeyError, TypeError, ValueError):
        return jsonify(error="product_id, store_id, price required"), 400
    if not 0 < price < 10000:
        return jsonify(error="price out of range"), 400
    with get_conn() as c:
        c.execute("INSERT OR IGNORE INTO listings (product_id, store_id) VALUES (?, ?)",
                  (product_id, store_id))
        lid = c.execute("SELECT id FROM listings WHERE product_id=? AND store_id=?",
                        (product_id, store_id)).fetchone()["id"]
        c.execute("INSERT INTO prices (listing_id, price, source) VALUES (?, ?, 'user')", (lid, price))
    return jsonify(ok=True), 201


@app.get("/api/compare/<int:product_id>")
def compare(product_id):
    with get_conn() as c:
        product = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not product:
            return jsonify(error="not found"), 404
        data = rows(c.execute(
            "SELECT * FROM (" + LATEST_PRICES_SQL + ") WHERE product_id=? ORDER BY price", (product_id,)))
    return jsonify(product=dict(product), prices=data)


@app.get("/api/history/<int:product_id>")
def history(product_id):
    """Daily price per store (last price seen that day), shaped for a line chart."""
    days = int(request.args.get("days", 90))
    with get_conn() as c:
        data = rows(c.execute(
            """SELECT s.name AS store, date(p.scraped_at) AS day, p.price, p.id
               FROM prices p JOIN listings l ON l.id = p.listing_id
               JOIN stores s ON s.id = l.store_id
               WHERE l.product_id = ? AND p.scraped_at >= datetime('now', ?)
               ORDER BY p.scraped_at, p.id""", (product_id, f"-{days} days")))
    per_store = defaultdict(dict)
    for r in data:
        per_store[r["store"]][r["day"]] = r["price"]  # later rows overwrite earlier same-day rows
    labels = sorted({d for s in per_store.values() for d in s})
    series = [{"store": s, "data": [pts.get(d) for d in labels]} for s, pts in sorted(per_store.items())]
    return jsonify(labels=labels, series=series)


@app.post("/api/scrape")
def run_scrape():
    return jsonify(scrape_all())


# ---------- shopping list ----------

@app.get("/api/list")
def get_list():
    with get_conn() as c:
        return jsonify(rows(c.execute(
            """SELECT li.id, li.qty, p.id AS product_id, p.name, p.unit
               FROM list_items li JOIN products p ON p.id = li.product_id ORDER BY p.name""")))


@app.post("/api/list")
def add_list_item():
    b = request.get_json(force=True)
    qty = max(1, int(b.get("qty", 1)))
    with get_conn() as c:
        c.execute(
            """INSERT INTO list_items (product_id, qty) VALUES (?, ?)
               ON CONFLICT(product_id) DO UPDATE SET qty = excluded.qty""",
            (int(b["product_id"]), qty))
    return jsonify(ok=True), 201


@app.delete("/api/list/<int:item_id>")
def delete_list_item(item_id):
    with get_conn() as c:
        c.execute("DELETE FROM list_items WHERE id=?", (item_id,))
    return jsonify(ok=True)


@app.get("/api/list/totals")
def list_totals():
    """Basket total at each store. Stores missing some items are flagged, not hidden."""
    with get_conn() as c:
        items = rows(c.execute("SELECT product_id, qty FROM list_items"))
        latest = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ")"))
        store_rows = rows(c.execute("SELECT id, name FROM stores"))
    price_map = {(r["store_id"], r["product_id"]): r["price"] for r in latest}
    results = []
    for s in store_rows:
        total, missing = 0.0, 0
        for it in items:
            p = price_map.get((s["id"], it["product_id"]))
            if p is None:
                missing += 1
            else:
                total += p * it["qty"]
        if items and missing < len(items):
            results.append({"store": s["name"], "total": round(total, 2),
                            "missing": missing, "complete": missing == 0})
    results.sort(key=lambda r: (r["missing"], r["total"]))
    return jsonify(item_count=len(items), stores=results)


# ---------- alerts ----------

@app.get("/api/alerts")
def get_alerts():
    with get_conn() as c:
        alerts = rows(c.execute(
            """SELECT a.id, a.target_price, p.id AS product_id, p.name FROM alerts a
               JOIN products p ON p.id = a.product_id ORDER BY p.name"""))
        latest = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ") ORDER BY price"))
    for a in alerts:
        hits = [r for r in latest if r["product_id"] == a["product_id"] and r["price"] <= a["target_price"]]
        a["triggered"] = bool(hits)
        a["best"] = {"store": hits[0]["store"], "price": hits[0]["price"]} if hits else None
    return jsonify(alerts)


@app.post("/api/alerts")
def add_alert():
    b = request.get_json(force=True)
    with get_conn() as c:
        c.execute("INSERT INTO alerts (product_id, target_price) VALUES (?, ?)",
                  (int(b["product_id"]), float(b["target_price"])))
    return jsonify(ok=True), 201


@app.delete("/api/alerts/<int:alert_id>")
def delete_alert(alert_id):
    with get_conn() as c:
        c.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
