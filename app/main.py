import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from datetime import timedelta
from functools import wraps

from flask import Flask, Response, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

from datetime import datetime, timedelta

from . import brands, geo, providers, replenish, scoring
from .basket import build_basket
from .compare import STRATEGIES, build_compare
from .db import DB_PATH, LATEST_PRICES_SQL, get_conn, init_db
from .scraper import scrape_all

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "static"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZIP_RE = re.compile(r"^\d{5}$")
RADII = (5, 10, 15)
BADGES = {"legend_badge": "Legend", "scout_badge": "Scout"}   # highest first
OPEN_PATHS = ("/manifest.webmanifest", "/sw.js", "/api/health")


def _load_secret_key():
    env = os.environ.get("SECRET_KEY")
    if env:
        return env
    path = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "secret.key")
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
app.secret_key = _load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1",
    PERMANENT_SESSION_LIFETIME=timedelta(days=60),
    MAX_CONTENT_LENGTH=64 * 1024,
)

_hits = defaultdict(deque)


def too_many(key, limit, window_seconds):
    """Tiny in-memory rate limiter (per process)."""
    now = time.time()
    q = _hits[key]
    while q and q[0] < now - window_seconds:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def rows(cursor):
    return [dict(r) for r in cursor.fetchall()]


def err(message, status=400):
    return jsonify(error=message), status


# ---------- request hooks ----------

@app.before_request
def _site_password():
    """Optional extra gate for the whole site: set APP_PASSWORD (any username)."""
    expected = os.environ.get("APP_PASSWORD")
    if not expected or request.path in OPEN_PATHS or request.path.startswith("/static/icons/"):
        return None
    auth = request.authorization
    if auth and auth.password is not None and hmac.compare_digest(
            auth.password.encode(), expected.encode()):
        return None
    return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Shelf Scout"'})


@app.before_request
def _csrf_guard():
    # Browsers can't add custom headers to cross-site form posts, and the session
    # cookie is SameSite=Lax, so requiring this header blocks cross-site writes.
    if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.path.startswith("/api/") \
            and request.path not in ("/api/admin/scrape", "/api/admin/offers"):
        if request.headers.get("X-App") != "1":
            return err("Request blocked.", 403)
    return None


@app.before_request
def _setup():
    if not getattr(app, "_db_ready", False):
        init_db()
        app._db_ready = True
    g.user = None
    uid = session.get("uid")
    if uid:
        with get_conn() as c:
            row = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        g.user = dict(row) if row else None
        if not g.user:
            session.clear()


@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'self'")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not g.user:
            return err("Sign in to continue.", 401)
        return fn(*args, **kwargs)
    return wrapper


# ---------- helpers ----------

def perk_map(conn, user_ids):
    ids = sorted({i for i in user_ids if i})
    if not ids:
        return {}
    q = ",".join("?" * len(ids))
    out = defaultdict(set)
    for r in conn.execute(
            f"SELECT d.user_id, r.key FROM redemptions d JOIN rewards r ON r.id = d.reward_id "
            f"WHERE d.user_id IN ({q})", ids):
        out[r["user_id"]].add(r["key"])
    return out


def author(name, perks):
    badge = next((label for key, label in BADGES.items() if key in perks), None)
    return {"name": name, "badge": badge, "gold": "gold_flair" in perks}


def user_json(uid):
    with get_conn() as c:
        u = c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        earned = c.execute("SELECT COALESCE(SUM(delta), 0) FROM points_ledger "
                           "WHERE user_id = ? AND delta > 0", (uid,)).fetchone()[0]
        perks = sorted(perk_map(c, [uid]).get(uid, set()))
        prefs = get_prefs(c, uid)
    return {"id": u["id"], "name": u["name"], "email": u["email"], "zip": u["zip"],
            "city": u["city"], "radius_miles": u["radius_miles"], "points": u["points"],
            "earned": earned, "level": scoring.level_for(earned), "perks": perks, "prefs": prefs}


PREF_LISTS = ("preferred_chains", "programs")


def get_prefs(conn, uid):
    conn.execute("INSERT OR IGNORE INTO user_prefs (user_id) VALUES (?)", (uid,))
    r = dict(conn.execute("SELECT * FROM user_prefs WHERE user_id = ?", (uid,)).fetchone())
    r.pop("user_id")
    for k in PREF_LISTS:
        try:
            r[k] = [x for x in json.loads(r[k]) if x in brands.BY_KEY]
        except ValueError:
            r[k] = []
    r["reminders_on"] = bool(r["reminders_on"])
    r["verified_only"] = bool(r["verified_only"])
    return r


def resolve_zip(zip5):
    if not ZIP_RE.match(zip5 or ""):
        return None, err("Enter a 5-digit ZIP code.")
    try:
        return geo.lookup_zip(zip5), None
    except geo.ZipNotFound:
        return None, err("We couldn't find that ZIP code. Check it and try again.")
    except geo.GeoUnavailable:
        return None, err("ZIP lookup isn't available right now. Try again in a minute.", 503)


def radius_arg():
    try:
        r = int(request.args.get("radius", g.user["radius_miles"]))
    except (TypeError, ValueError):
        r = g.user["radius_miles"]
    return max(1, min(25, r))


def center():
    return (g.user["lat"], g.user["lng"])


def nearby(rows_, radius):
    out = []
    for r in rows_:
        if r.get("lat") is None:
            continue
        d = geo.haversine_miles(g.user["lat"], g.user["lng"], r["lat"], r["lng"])
        if d <= radius:
            r = dict(r)
            r["distance"] = round(d, 1)
            out.append(r)
    return out


# ---------- pages ----------

@app.get("/")
def index():
    resp = send_from_directory(STATIC_DIR, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/sw.js")
def service_worker():
    resp = send_from_directory(STATIC_DIR, "sw.js", mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(STATIC_DIR, "manifest.webmanifest", mimetype="application/manifest+json")


@app.get("/api/health")
def health():
    return jsonify(ok=True)


# ---------- accounts ----------

@app.post("/api/auth/signup")
def signup():
    b = request.get_json(silent=True) or {}
    name = (b.get("name") or "").strip()
    email = (b.get("email") or "").strip().lower()
    password = b.get("password") or ""
    if not 1 <= len(name) <= 40:
        return err("Enter a display name up to 40 characters.")
    if not EMAIL_RE.match(email) or len(email) > 120:
        return err("Enter a valid email address.")
    if len(password) < 8:
        return err("Use a password with at least 8 characters.")
    if too_many(("signup", request.remote_addr), int(os.environ.get("SIGNUP_LIMIT", "30")), 3600):
        return err("Too many sign-ups from this network. Try again later.", 429)
    loc, e = resolve_zip((b.get("zip") or "").strip())
    if e:
        return e
    with get_conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO users (email, name, pw_hash, zip, city, lat, lng) VALUES (?,?,?,?,?,?,?)",
                (email, name, generate_password_hash(password), loc["zip"], loc["city"],
                 loc["lat"], loc["lng"]))
        except sqlite3.IntegrityError:
            return err("An account with that email already exists. Sign in instead.", 409)
        uid = cur.lastrowid
    session.clear()
    session.permanent = True
    session["uid"] = uid
    return jsonify(user=user_json(uid)), 201


@app.post("/api/auth/login")
def login():
    b = request.get_json(silent=True) or {}
    email = (b.get("email") or "").strip().lower()
    if too_many(("login", request.remote_addr, email), 8, 600):
        return err("Too many attempts. Try again in a few minutes.", 429)
    with get_conn() as c:
        u = c.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not u or not check_password_hash(u["pw_hash"], b.get("password") or ""):
        return err("That email and password don't match.", 401)
    session.clear()
    session.permanent = True
    session["uid"] = u["id"]
    return jsonify(user=user_json(u["id"]))


@app.post("/api/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    if not g.user:
        return jsonify(user=None, brands=brands.brands_json())
    return jsonify(user=user_json(g.user["id"]), brands=brands.brands_json())


@app.patch("/api/me")
@login_required
def update_me():
    b = request.get_json(silent=True) or {}
    name, zip5, radius = g.user["name"], g.user["zip"], g.user["radius_miles"]
    lat, lng, city = g.user["lat"], g.user["lng"], g.user["city"]
    if "name" in b:
        name = (b["name"] or "").strip()
        if not 1 <= len(name) <= 40:
            return err("Enter a display name up to 40 characters.")
    if "zip" in b and (b["zip"] or "").strip() != zip5:
        loc, e = resolve_zip((b["zip"] or "").strip())
        if e:
            return e
        zip5, lat, lng, city = loc["zip"], loc["lat"], loc["lng"], loc["city"]
    if "radius_miles" in b:
        if b["radius_miles"] not in RADII:
            return err("Choose 5, 10 or 15 miles.")
        radius = b["radius_miles"]
    with get_conn() as c:
        c.execute("UPDATE users SET name=?, zip=?, city=?, lat=?, lng=?, radius_miles=? WHERE id=?",
                  (name, zip5, city, lat, lng, radius, g.user["id"]))
    return jsonify(user=user_json(g.user["id"]))


# ---------- products ----------

@app.get("/api/products")
@login_required
def products():
    q = "%" + (request.args.get("q") or "").strip() + "%"
    with get_conn() as c:
        data = rows(c.execute(
            "SELECT p.id, p.name, p.category, p.unit, p.brand, p.package_size, p.image_url, "
            "COALESCE(ip.excluded, 0) AS excluded, COALESCE(ip.favorite, 0) AS favorite "
            "FROM products p LEFT JOIN item_prefs ip ON ip.product_id = p.id AND ip.user_id = ? "
            "WHERE p.name LIKE ? ORDER BY p.name LIMIT 60", (g.user["id"], q)))
        prefs = get_prefs(c, g.user["id"])
    avoid = [t.strip().lower() for t in prefs["avoid_terms"].split(",") if t.strip()]
    favs = [t.strip().lower() for t in prefs["fav_brands"].split(",") if t.strip()]
    data = [d for d in data if not d["excluded"] and not any(
        t in (d["name"] + " " + (d["brand"] or "")).lower() for t in avoid)]
    data.sort(key=lambda d: (not d["favorite"], not any(f in (d["brand"] or "").lower() for f in favs), d["name"]))
    return jsonify(data)


@app.post("/api/products")
@login_required
def add_product():
    b = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", (b.get("name") or "").strip())
    if not 2 <= len(name) <= 80:
        return err("Enter an item name between 2 and 80 characters.")
    with get_conn() as c:
        existing = c.execute("SELECT * FROM products WHERE lower(name) = lower(?)", (name,)).fetchone()
        if existing:
            return jsonify(dict(existing)), 200
        if too_many(("product", g.user["id"]), 10, 86400):
            return err("You've added the maximum number of new items for today.", 429)
        brand = re.sub(r"\s+", " ", str(b.get("brand") or "").strip())[:40] or None
        size = re.sub(r"\s+", " ", str(b.get("package_size") or "").strip())[:40] or None
        c.execute("INSERT INTO products (name, category, unit, brand, package_size) VALUES (?, ?, ?, ?, ?)",
                  (name, (b.get("category") or None), (b.get("unit") or None), brand, size))
        row = c.execute("SELECT * FROM products WHERE name = ?", (name,)).fetchone()
    return jsonify(dict(row)), 201


@app.get("/api/products/<int:product_id>/prices")
@login_required
def product_prices(product_id):
    radius = radius_arg()
    with get_conn() as c:
        product = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product:
            return err("We couldn't find that item.", 404)
        data = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ") WHERE product_id = ?",
                              (product_id,)))
    opts = sorted(nearby(data, radius), key=lambda r: (r["price"], r["distance"]))
    for o in opts:
        o["age_days"] = round(scoring.age_days(o["seen_at"]) or 0, 1)
    return jsonify(product=dict(product), radius=radius, options=opts)


@app.get("/api/history/<int:product_id>")
@login_required
def history(product_id):
    radius = radius_arg()
    days = max(7, min(365, int(request.args.get("days", 60))))
    with get_conn() as c:
        data = rows(c.execute(
            "SELECT s.id AS store_id, s.lat, s.lng, date(p.scraped_at) AS day, p.price "
            "FROM prices p JOIN listings l ON l.id = p.listing_id JOIN stores s ON s.id = l.store_id "
            "WHERE l.product_id = ? AND p.status = 'ok' AND p.scraped_at >= datetime('now', ?) "
            "ORDER BY p.scraped_at, p.id", (product_id, f"-{days} days")))
    per_day = defaultdict(dict)   # day -> store -> last price that day
    for r in nearby(data, radius):
        per_day[r["day"]][r["store_id"]] = r["price"]
    points = [{"day": d, "price": min(v.values())} for d, v in sorted(per_day.items())]
    return jsonify(points=points)


# ---------- shopping list and basket ----------

@app.get("/api/list")
@login_required
def list_get():
    with get_conn() as c:
        return jsonify(rows(c.execute(
            "SELECT li.id, li.qty, li.checked, p.id AS product_id, p.name, p.unit, "
            "p.brand, p.package_size, p.image_url "
            "FROM list_items li JOIN products p ON p.id = li.product_id "
            "WHERE li.user_id = ? ORDER BY li.checked, p.name", (g.user["id"],))))


@app.post("/api/list")
@login_required
def list_add():
    b = request.get_json(silent=True) or {}
    try:
        product_id = int(b.get("product_id"))
        qty = max(1, min(99, int(b.get("qty", 1))))
    except (TypeError, ValueError):
        return err("Choose an item to add.")
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            return err("We couldn't find that item.", 404)
        c.execute("INSERT INTO list_items (user_id, product_id, qty) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id, product_id) DO UPDATE SET qty = MIN(99, qty + excluded.qty), checked = 0",
                  (g.user["id"], product_id, qty))
    return jsonify(ok=True), 201


@app.patch("/api/list/<int:item_id>")
@login_required
def list_update(item_id):
    b = request.get_json(silent=True) or {}
    sets, vals = [], []
    if "qty" in b:
        try:
            sets.append("qty = ?")
            vals.append(max(1, min(99, int(b["qty"]))))
        except (TypeError, ValueError):
            return err("Enter a quantity from 1 to 99.")
    if "checked" in b:
        sets.append("checked = ?")
        vals.append(1 if b["checked"] else 0)
    if not sets:
        return err("Nothing to update.")
    with get_conn() as c:
        cur = c.execute(f"UPDATE list_items SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
                        vals + [item_id, g.user["id"]])
        if cur.rowcount == 0:
            return err("We couldn't find that list item.", 404)
    return jsonify(ok=True)


@app.delete("/api/list/<int:item_id>")
@login_required
def list_delete(item_id):
    with get_conn() as c:
        c.execute("DELETE FROM list_items WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
    return jsonify(ok=True)


@app.get("/api/basket")
@login_required
def basket():
    radius = radius_arg()
    with get_conn() as c:
        items = rows(c.execute(
            "SELECT li.id AS item_id, li.qty, li.checked, p.id AS product_id, p.name, p.unit, "
            "p.brand, p.package_size, p.image_url "
            "FROM list_items li JOIN products p ON p.id = li.product_id "
            "WHERE li.user_id = ? ORDER BY li.checked, p.name", (g.user["id"],)))
        active = [i for i in items if not i["checked"]]
        price_rows = []
        if active:
            q = ",".join("?" * len(active))
            price_rows = rows(c.execute(
                f"SELECT * FROM ({LATEST_PRICES_SQL}) WHERE product_id IN ({q})",
                [i["product_id"] for i in active]))
    result = build_basket(active, price_rows, center(), radius)
    result["items"] = items
    result["per_item"] = {str(k): v for k, v in result["per_item"].items()}
    return jsonify(result)


# ---------- stores and reviews ----------

@app.get("/api/stores/nearby")
@login_required
def stores_nearby():
    radius = radius_arg()
    with get_conn() as c:
        data = rows(c.execute(
            "SELECT s.id, s.name, s.chain, s.address, s.zip, s.lat, s.lng, s.source, "
            "(SELECT ROUND(AVG(rating), 1) FROM store_reviews WHERE store_id = s.id) AS rating, "
            "(SELECT COUNT(*) FROM store_reviews WHERE store_id = s.id) AS review_count "
            "FROM stores s WHERE s.lat IS NOT NULL"))
    out = sorted(nearby(data, radius), key=lambda s: s["distance"])
    for s in out:
        s["sample"] = s.pop("source") == "demo"
        s.pop("lat"), s.pop("lng")
    return jsonify(radius=radius, stores=out)


@app.post("/api/stores")
@login_required
def add_store():
    b = request.get_json(silent=True) or {}
    name = re.sub(r"\s+", " ", (b.get("name") or "").strip())
    address = (b.get("address") or "").strip()[:120]
    if not 2 <= len(name) <= 80:
        return err("Enter the store name (2 to 80 characters).")
    loc, e = resolve_zip((b.get("zip") or "").strip())
    if e:
        return e
    with get_conn() as c:
        existing = c.execute("SELECT id, name FROM stores WHERE lower(name) = lower(?) AND zip = ? "
                             "AND address = ?", (name, loc["zip"], address)).fetchone()
        if existing:
            return jsonify(dict(existing)), 200
        if too_many(("store", g.user["id"]), 5, 86400):
            return err("You've added the maximum number of stores for today.", 429)
        cur = c.execute(
            "INSERT INTO stores (name, chain, city, address, zip, lat, lng, source, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'user', ?)",
            (name, name, loc["city"], address, loc["zip"], loc["lat"], loc["lng"], g.user["id"]))
        sid = cur.lastrowid
    return jsonify(id=sid, name=name), 201


@app.get("/api/stores/<int:store_id>")
@login_required
def store_detail(store_id):
    with get_conn() as c:
        s = c.execute("SELECT id, name, chain, address, zip, source FROM stores WHERE id = ?",
                      (store_id,)).fetchone()
        if not s:
            return err("We couldn't find that store.", 404)
        revs = rows(c.execute(
            "SELECT r.id, r.rating, r.comment, r.created_at, r.user_id, u.name "
            "FROM store_reviews r JOIN users u ON u.id = r.user_id WHERE r.store_id = ? "
            "ORDER BY r.created_at DESC LIMIT 30", (store_id,)))
        perks = perk_map(c, [r["user_id"] for r in revs])
    mine = None
    for r in revs:
        r["author"] = author(r.pop("name"), perks.get(r["user_id"], set()))
        r["mine"] = r.pop("user_id") == g.user["id"]
        if r["mine"]:
            mine = {"rating": r["rating"], "comment": r["comment"]}
    avg = round(sum(r["rating"] for r in revs) / len(revs), 1) if revs else None
    store = dict(s)
    store["sample"] = store.pop("source") == "demo"
    return jsonify(store=store, rating=avg, review_count=len(revs), reviews=revs, mine=mine)


@app.post("/api/stores/<int:store_id>/reviews")
@login_required
def add_review(store_id):
    b = request.get_json(silent=True) or {}
    try:
        rating = int(b.get("rating"))
    except (TypeError, ValueError):
        rating = 0
    comment = (b.get("comment") or "").strip()[:500]
    if not 1 <= rating <= 5:
        return err("Choose a rating from 1 to 5 stars.")
    uid = g.user["id"]
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM stores WHERE id = ?", (store_id,)).fetchone():
            return err("We couldn't find that store.", 404)
        if too_many(("review", uid), 30, 86400):
            return err("You've reached today's review limit.", 429)
        had = c.execute("SELECT 1 FROM store_reviews WHERE store_id = ? AND user_id = ?",
                        (store_id, uid)).fetchone()
        c.execute("INSERT INTO store_reviews (store_id, user_id, rating, comment) VALUES (?,?,?,?) "
                  "ON CONFLICT(store_id, user_id) DO UPDATE SET rating = excluded.rating, "
                  "comment = excluded.comment, created_at = datetime('now')",
                  (store_id, uid, rating, comment))
        earned = 0
        if not had and len(comment) >= 20 \
                and scoring.count_ref(c, uid, "review", store_id) == 0:
            scoring.award(c, uid, scoring.REVIEW_POINTS, "review", store_id)
            earned = scoring.REVIEW_POINTS
    msg = "Review saved." if not earned else f"Review saved. You earned {earned} points."
    return jsonify(ok=True, points=earned, message=msg), 201


# ---------- price reports ----------

@app.post("/api/prices")
@login_required
def submit_price():
    b = request.get_json(silent=True) or {}
    try:
        product_id, store_id = int(b["product_id"]), int(b["store_id"])
        price = round(float(b["price"]), 2)
    except (KeyError, TypeError, ValueError):
        return err("Choose a store and an item, and enter the price.")
    if not 0.05 <= price <= 999:
        return err("Enter a price between $0.05 and $999.")
    try:
        corrects = int(b["corrects_price_id"]) if b.get("corrects_price_id") else None
    except (TypeError, ValueError):
        corrects = None
    uid = g.user["id"]
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM stores WHERE id = ?", (store_id,)).fetchone():
            return err("We couldn't find that store.", 404)
        if not c.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            return err("We couldn't find that item.", 404)
        c.execute("INSERT OR IGNORE INTO listings (product_id, store_id) VALUES (?, ?)",
                  (product_id, store_id))
        lid = c.execute("SELECT id FROM listings WHERE product_id = ? AND store_id = ?",
                        (product_id, store_id)).fetchone()["id"]
        known = [r["price"] for r in c.execute(
            "SELECT price FROM (" + LATEST_PRICES_SQL + ") WHERE product_id = ?", (product_id,))]
        med = scoring.median(known) if len(known) >= 3 else None
        status = "ok" if scoring.is_plausible(price, med) else "pending"
        already_today = c.execute(
            "SELECT 1 FROM prices WHERE listing_id = ? AND user_id = ? "
            "AND scraped_at >= datetime('now', '-1 day')", (lid, uid)).fetchone()
        cur = c.execute(
            "INSERT INTO prices (listing_id, price, source, user_id, status, corrects_price_id) "
            "VALUES (?, ?, 'user', ?, ?, ?)", (lid, price, uid, status, corrects))
        price_id = cur.lastrowid
        earned = 0
        if status == "pending":
            msg = ("That price looks unusual, so it needs confirmation from two other "
                   "shoppers before it counts.")
        elif already_today:
            msg = "Saved. You already reported this item here today, so there are no extra points."
        elif scoring.count_recent(c, uid, "report") >= scoring.DAILY_REPORT_CAP:
            msg = "Saved. You've reached today's limit for report points."
        else:
            scoring.award(c, uid, scoring.REPORT_POINTS, "report", price_id)
            earned = scoring.REPORT_POINTS
            msg = f"Thanks. You earned {earned} points."
        if corrects:
            orig = c.execute("SELECT * FROM prices WHERE id = ? AND listing_id = ?",
                             (corrects, lid)).fetchone()
            if orig and orig["user_id"] != uid and abs(price - orig["price"]) >= 0.05 * orig["price"]:
                c.execute("UPDATE prices SET disputed_count = disputed_count + 1 WHERE id = ?", (corrects,))
    return jsonify(id=price_id, status=status, points=earned, message=msg), 201


@app.get("/api/prices/verify-feed")
@login_required
def verify_feed():
    radius = radius_arg()
    with get_conn() as c:
        data = rows(c.execute(
            "SELECT p.id AS price_id, p.price, p.scraped_at, p.confirmed_at, p.status, "
            "p.verified_count, p.user_id, p.source, pr.id AS product_id, pr.name AS product, "
            "pr.unit, s.id AS store_id, s.name AS store, s.lat, s.lng, s.source AS store_source, "
            "u.name AS author_name "
            "FROM prices p JOIN listings l ON l.id = p.listing_id "
            "JOIN products pr ON pr.id = l.product_id JOIN stores s ON s.id = l.store_id "
            "LEFT JOIN users u ON u.id = p.user_id "
            "WHERE p.status IN ('ok', 'pending') AND (p.user_id IS NULL OR p.user_id != ?) "
            "AND p.id = (SELECT id FROM prices WHERE listing_id = l.id AND status IN ('ok','pending') "
            "            ORDER BY scraped_at DESC, id DESC LIMIT 1) "
            "AND p.scraped_at >= datetime('now', ?) "
            "AND NOT EXISTS (SELECT 1 FROM price_votes v WHERE v.price_id = p.id AND v.user_id = ?)",
            (g.user["id"], f"-{scoring.MAX_PRICE_AGE_DAYS} days", g.user["id"])))
        perks = perk_map(c, [r["user_id"] for r in data])
    feed = sorted(nearby(data, radius),
                  key=lambda r: (r["status"] != "pending", r["verified_count"], r["distance"]))[:10]
    out = []
    for r in feed:
        out.append({
            "price_id": r["price_id"], "price": r["price"], "product": r["product"],
            "product_id": r["product_id"], "unit": r["unit"], "store": r["store"],
            "store_id": r["store_id"], "distance": r["distance"], "status": r["status"],
            "verified": r["verified_count"], "sample": r["store_source"] == "demo",
            "age_days": round(scoring.age_days(r["scraped_at"]) or 0, 1),
            "author": author(r["author_name"], perks.get(r["user_id"], set())) if r["author_name"] else None,
        })
    return jsonify(feed=out)


@app.post("/api/prices/<int:price_id>/vote")
@login_required
def vote_price(price_id):
    uid = g.user["id"]
    with get_conn() as c:
        p = c.execute("SELECT * FROM prices WHERE id = ?", (price_id,)).fetchone()
        if not p or p["status"] not in ("ok", "pending"):
            return err("That price is no longer available.", 404)
        if p["user_id"] == uid:
            return err("You can't confirm your own price.", 403)
        if (scoring.age_days(p["scraped_at"]) or 0) > scoring.MAX_PRICE_AGE_DAYS:
            return err("That price is more than 2 weeks old. Report the current price instead.")
        if c.execute("SELECT 1 FROM price_votes WHERE price_id = ? AND user_id = ?",
                     (price_id, uid)).fetchone():
            return err("You already confirmed this price.", 409)
        c.execute("INSERT INTO price_votes (price_id, user_id, vote) VALUES (?, ?, 'confirm')",
                  (price_id, uid))
        c.execute("UPDATE prices SET verified_count = verified_count + 1, "
                  "confirmed_at = datetime('now') WHERE id = ?", (price_id,))
        earned = 0
        if scoring.count_recent(c, uid, "confirm") < scoring.DAILY_CONFIRM_CAP:
            scoring.award(c, uid, scoring.CONFIRM_POINTS, "confirm", price_id)
            earned = scoring.CONFIRM_POINTS
        reporter = p["user_id"]
        if reporter and scoring.count_ref(c, reporter, "confirmed", price_id) < scoring.MAX_BONUS_PER_REPORT:
            scoring.award(c, reporter, scoring.CONFIRMED_BONUS, "confirmed", price_id)
        if p["status"] == "pending" and p["verified_count"] + 1 >= 2:
            c.execute("UPDATE prices SET status = 'ok' WHERE id = ?", (price_id,))
            if reporter:
                scoring.award(c, reporter, scoring.REPORT_POINTS, "verified_report", price_id)
    msg = f"Thanks. You earned {earned} point." if earned else "Thanks. You've reached today's limit for confirmation points."
    return jsonify(ok=True, points=earned, message=msg)


# ---------- alerts ----------

@app.get("/api/alerts")
@login_required
def alerts_get():
    radius = radius_arg()
    with get_conn() as c:
        alerts = rows(c.execute(
            "SELECT a.id, a.target_price, p.id AS product_id, p.name FROM alerts a "
            "JOIN products p ON p.id = a.product_id WHERE a.user_id = ? ORDER BY p.name",
            (g.user["id"],)))
        latest = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ")"))
    near = sorted(nearby(latest, radius), key=lambda r: r["price"])
    for a in alerts:
        hits = [r for r in near if r["product_id"] == a["product_id"] and r["price"] <= a["target_price"]]
        a["triggered"] = bool(hits)
        a["best"] = {"store": hits[0]["store"], "price": hits[0]["price"],
                     "distance": hits[0]["distance"]} if hits else None
    return jsonify(alerts)


@app.post("/api/alerts")
@login_required
def alerts_add():
    b = request.get_json(silent=True) or {}
    try:
        product_id, target = int(b["product_id"]), round(float(b["target_price"]), 2)
    except (KeyError, TypeError, ValueError):
        return err("Choose an item and enter a target price.")
    if not 0.05 <= target <= 999:
        return err("Enter a target price between $0.05 and $999.")
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            return err("We couldn't find that item.", 404)
        if c.execute("SELECT COUNT(*) FROM alerts WHERE user_id = ?", (g.user["id"],)).fetchone()[0] >= 25:
            return err("You can have up to 25 alerts.")
        c.execute("DELETE FROM alerts WHERE user_id = ? AND product_id = ?", (g.user["id"], product_id))
        c.execute("INSERT INTO alerts (user_id, product_id, target_price) VALUES (?, ?, ?)",
                  (g.user["id"], product_id, target))
    return jsonify(ok=True), 201


@app.delete("/api/alerts/<int:alert_id>")
@login_required
def alerts_delete(alert_id):
    with get_conn() as c:
        c.execute("DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, g.user["id"]))
    return jsonify(ok=True)


# ---------- rewards ----------

@app.get("/api/rewards")
@login_required
def rewards_get():
    uid = g.user["id"]
    with get_conn() as c:
        owned = {r["reward_id"] for r in c.execute(
            "SELECT reward_id FROM redemptions WHERE user_id = ?", (uid,))}
        catalog = rows(c.execute("SELECT id, key, name, description, cost, kind FROM rewards ORDER BY cost"))
        for r in catalog:
            r["owned"] = r.pop("id") in owned
        ledger = rows(c.execute(
            "SELECT delta, reason, created_at FROM points_ledger WHERE user_id = ? "
            "ORDER BY id DESC LIMIT 20", (uid,)))
        for l in ledger:
            l["text"] = scoring.REASON_TEXT.get(l["reason"], l["reason"])
        board = rows(c.execute(
            "SELECT u.id AS user_id, u.name, SUM(l.delta) AS points FROM points_ledger l "
            "JOIN users u ON u.id = l.user_id WHERE l.delta > 0 "
            "AND l.created_at >= datetime('now', '-30 days') "
            "GROUP BY u.id ORDER BY points DESC, u.id LIMIT 5"))
        perks = perk_map(c, [b["user_id"] for b in board])
    for b in board:
        b["author"] = author(b.pop("name"), perks.get(b["user_id"], set()))
        b["you"] = b.pop("user_id") == uid
    return jsonify(user=user_json(uid), catalog=catalog, ledger=ledger, leaderboard=board,
                   rules=scoring.RULES)


@app.post("/api/rewards/<key>/redeem")
@login_required
def redeem(key):
    uid = g.user["id"]
    with get_conn() as c:
        r = c.execute("SELECT * FROM rewards WHERE key = ?", (key,)).fetchone()
        if not r:
            return err("We couldn't find that perk.", 404)
        if c.execute("SELECT 1 FROM redemptions WHERE user_id = ? AND reward_id = ?",
                     (uid, r["id"])).fetchone():
            return err("You already unlocked this perk.", 409)
        balance = c.execute("SELECT points FROM users WHERE id = ?", (uid,)).fetchone()["points"]
        if balance < r["cost"]:
            return err(f"You need {r['cost'] - balance} more points for this perk.")
        c.execute("INSERT INTO redemptions (user_id, reward_id, cost) VALUES (?, ?, ?)",
                  (uid, r["id"], r["cost"]))
        scoring.award(c, uid, -r["cost"], "redeem", r["id"])
    return jsonify(ok=True, user=user_json(uid), message=f"{r['name']} unlocked.")


# ---------- preferences ----------

def _num(v, lo, hi, name, integer=False):
    if v in (None, ""):
        return None
    try:
        n = int(v) if integer else round(float(v), 2)
    except (TypeError, ValueError):
        raise ValueError(f"Enter a number for {name}.")
    if not lo <= n <= hi:
        raise ValueError(f"{name[0].upper() + name[1:]} must be between {lo} and {hi}.")
    return n


@app.patch("/api/prefs")
@login_required
def update_prefs():
    b = request.get_json(silent=True) or {}
    sets, vals = [], []

    def put(col, val):
        sets.append(f"{col} = ?")
        vals.append(val)
    try:
        for k in PREF_LISTS:
            if k in b:
                if not isinstance(b[k], list) or any(x not in brands.BY_KEY for x in b[k]):
                    raise ValueError("Choose from Winn-Dixie, Publix and Walmart.")
                put(k, json.dumps(sorted(set(b[k]))))
        if "budget" in b:
            put("budget", _num(b["budget"], 1, 100000, "budget"))
        if "household" in b:
            put("household", _num(b["household"], 1, 20, "household size", integer=True))
        if "min_savings" in b:
            put("min_savings", _num(b["min_savings"], 0, 100, "minimum savings") or 0)
        if "cost_per_mile" in b:
            put("cost_per_mile", _num(b["cost_per_mile"], 0, 5, "cost per mile") or 0)
        if "strategy" in b:
            if b["strategy"] not in STRATEGIES:
                raise ValueError("Choose lowest cost, fewest stores or best value.")
            put("strategy", b["strategy"])
        for k in ("reminders_on", "verified_only"):
            if k in b:
                put(k, 1 if b[k] else 0)
        for k, n in (("fav_brands", 200), ("avoid_terms", 200), ("diet_note", 200)):
            if k in b:
                put(k, re.sub(r"\s+", " ", str(b[k] or "").strip())[:n])
    except ValueError as e:
        return err(str(e))
    if not sets:
        return err("Nothing to update.")
    with get_conn() as c:
        get_prefs(c, g.user["id"])
        c.execute(f"UPDATE user_prefs SET {', '.join(sets)} WHERE user_id = ?", vals + [g.user["id"]])
    return jsonify(user=user_json(g.user["id"]))


# ---------- compare across Winn-Dixie, Publix and Walmart ----------

def _list_items(c, uid):
    return rows(c.execute(
        "SELECT li.id AS item_id, li.qty, li.checked, p.id AS product_id, p.name, p.unit, "
        "p.brand, p.package_size, p.image_url FROM list_items li JOIN products p ON p.id = li.product_id "
        "WHERE li.user_id = ? ORDER BY li.checked, p.name", (uid,)))


@app.get("/api/compare")
@login_required
def compare_route():
    radius = radius_arg()
    with get_conn() as c:
        prefs = get_prefs(c, g.user["id"])
        items = _list_items(c, g.user["id"])
        price_rows = rows(c.execute(
            "SELECT * FROM (" + LATEST_PRICES_SQL + ") WHERE product_id IN "
            "(SELECT product_id FROM list_items WHERE user_id = ?)", (g.user["id"],)))
        offers = providers.active_offers(c)
    out = build_compare(items, price_rows, offers, center(), radius, prefs)
    out["providers"] = [p for p in providers.status() if p.get("chain")]
    return jsonify(out)


# ---------- deals ----------

@app.get("/api/deals")
@login_required
def deals():
    radius = radius_arg()
    with get_conn() as c:
        prefs = get_prefs(c, g.user["id"])
        items = {i["product_id"]: i for i in _list_items(c, g.user["id"]) if not i["checked"]}
        offers = providers.active_offers(c)
        price_rows = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ")"))
    near = nearby(price_rows, radius)
    shelf = {}   # (chain, product) -> cheapest nearby price at that chain
    for r in near:
        k = brands.chain_key(r["chain"])
        if k and r["store_source"] != "demo":
            key = (k, r["product_id"])
            if key not in shelf or r["price"] < shelf[key]:
                shelf[key] = r["price"]
    out, best = [], {}
    for o in offers:
        item = items.get(o["product_id"]) if o["product_id"] else None
        v = {k: o[k] for k in ("id", "chain", "kind", "title", "product", "product_id", "sale_price", "amount_off",
                               "min_spend", "max_qty", "requires_account", "conditions", "starts_on",
                               "expires_on", "source", "source_url")}
        v["chain_name"] = brands.BY_KEY.get(o["chain"], {}).get("name", o["chain"])
        v["on_list"] = bool(item)
        v["account_ok"] = (not o["requires_account"]) or o["chain"] in prefs["programs"]
        v["saves"] = None
        if item and (o["chain"], o["product_id"]) in shelf:
            price = shelf[(o["chain"], o["product_id"])]
            unit = o["sale_price"] if o["sale_price"] is not None else price - (o["amount_off"] or 0)
            if unit < price:
                n = min(item["qty"], o["max_qty"]) if o["max_qty"] else item["qty"]
                v["saves"] = round((price - unit) * n, 2)
                if v["account_ok"] and not o["min_spend"]:
                    key = (o["chain"], o["product_id"])
                    best[key] = max(best.get(key, 0), v["saves"])
        out.append(v)
    by_chain = defaultdict(float)
    for (chain, _), amount in best.items():
        by_chain[chain] += amount
    return jsonify(offers=out, providers=providers.status(), programs=prefs["programs"],
                   list_savings={k: round(v, 2) for k, v in by_chain.items()},
                   list_size=len(items))


@app.post("/api/admin/offers")
def import_offers_route():
    """Load offers from a real feed. Needs ADMIN_TOKEN and a matching X-Admin-Token header."""
    token = os.environ.get("ADMIN_TOKEN")
    sent = request.headers.get("X-Admin-Token", "")
    if not token or not hmac.compare_digest(sent.encode(), token.encode()):
        return err("Not allowed.", 403)
    b = request.get_json(silent=True) or {}
    if not isinstance(b.get("offers"), list):
        return err("Send {\"offers\": [...]}.")
    saved, errors = providers.import_offers(b["offers"], b.get("replace_source"))
    return jsonify(saved=saved, errors=errors)


# ---------- removing items, purchase history ----------

def _ref_high(c, product_id):
    """Highest of the three chains' lowest real (non-sample) prices near the shopper, or None."""
    data = rows(c.execute("SELECT * FROM (" + LATEST_PRICES_SQL + ") WHERE product_id = ?", (product_id,)))
    best = {}
    for r in nearby(data, g.user["radius_miles"]):
        k = brands.chain_key(r["chain"])
        if k and r["store_source"] != "demo":
            best[k] = min(best.get(k, 1e9), r["price"])
    return max(best.values()) if len(best) >= 2 else None


def _item_pref(c, uid, pid):
    c.execute("INSERT OR IGNORE INTO item_prefs (user_id, product_id) VALUES (?, ?)", (uid, pid))
    return dict(c.execute("SELECT * FROM item_prefs WHERE user_id = ? AND product_id = ?", (uid, pid)).fetchone())


def _record_purchase(c, uid, pid, qty, chain=None, paid=None):
    cur = c.execute("INSERT INTO purchases (user_id, product_id, qty, chain, paid, ref_high) VALUES (?, ?, ?, ?, ?, ?)",
                    (uid, pid, qty, chain, paid, _ref_high(c, pid)))
    c.execute("UPDATE item_prefs SET snoozed_until = NULL WHERE user_id = ? AND product_id = ?", (uid, pid))
    return cur.lastrowid


@app.post("/api/list/<int:item_id>/remove")
@login_required
def list_remove(item_id):
    """Take an item off the active list. bought=true also records the purchase."""
    b = request.get_json(silent=True) or {}
    if not isinstance(b.get("bought"), bool):
        return err("Say whether you bought this item.")
    chain, paid = b.get("chain") or None, None
    if chain and chain not in brands.BY_KEY:
        return err("Choose Winn-Dixie, Publix or Walmart.")
    try:
        paid = _num(b.get("paid"), 0.01, 999, "price paid")
    except ValueError as e:
        return err(str(e))
    uid = g.user["id"]
    with get_conn() as c:
        it = c.execute("SELECT li.*, p.name FROM list_items li JOIN products p ON p.id = li.product_id "
                       "WHERE li.id = ? AND li.user_id = ?", (item_id, uid)).fetchone()
        if not it:
            return err("We couldn't find that list item.", 404)
        pid = None
        if b["bought"]:
            _item_pref(c, uid, it["product_id"])
            pid = _record_purchase(c, uid, it["product_id"], it["qty"], chain, paid)
        c.execute("DELETE FROM list_items WHERE id = ? AND user_id = ?", (item_id, uid))
    return jsonify(ok=True, purchase_id=pid, product_id=it["product_id"], qty=it["qty"], name=it["name"])


@app.post("/api/purchases/<int:purchase_id>/undo")
@login_required
def purchase_undo(purchase_id):
    uid = g.user["id"]
    with get_conn() as c:
        p = c.execute("SELECT * FROM purchases WHERE id = ? AND user_id = ?", (purchase_id, uid)).fetchone()
        if not p:
            return err("We couldn't find that purchase.", 404)
        c.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
        if (request.get_json(silent=True) or {}).get("restore", True):   # put it back on the list
            c.execute("INSERT INTO list_items (user_id, product_id, qty) VALUES (?, ?, ?) "
                      "ON CONFLICT(user_id, product_id) DO NOTHING", (uid, p["product_id"], p["qty"]))
    return jsonify(ok=True)


@app.delete("/api/purchases/<int:purchase_id>")
@login_required
def purchase_delete(purchase_id):
    with get_conn() as c:
        c.execute("DELETE FROM purchases WHERE id = ? AND user_id = ?", (purchase_id, g.user["id"]))
    return jsonify(ok=True)


@app.get("/api/purchases")
@login_required
def purchases_get():
    uid = g.user["id"]
    with get_conn() as c:
        hist = rows(c.execute(
            "SELECT pu.id, pu.product_id, p.name, pu.qty, pu.bought_at, pu.chain, pu.paid, pu.ref_high "
            "FROM purchases pu JOIN products p ON p.id = pu.product_id WHERE pu.user_id = ? "
            "ORDER BY pu.bought_at DESC, pu.id DESC LIMIT 60", (uid,)))
        total = c.execute("SELECT COUNT(*) FROM purchases WHERE user_id = ?", (uid,)).fetchone()[0]
        sv = c.execute(
            "SELECT COALESCE(SUM((ref_high - paid) * qty), 0), COUNT(*) FROM purchases "
            "WHERE user_id = ? AND paid IS NOT NULL AND ref_high IS NOT NULL AND ref_high > paid", (uid,)).fetchone()
    for h in hist:
        h["chain_name"] = brands.BY_KEY.get(h["chain"], {}).get("name")
    return jsonify(purchases=hist, total=total, est_savings=round(sv[0], 2), savings_count=sv[1])


# ---------- usage and restock reminders ----------

def _estimates(c, uid, today=None):
    prefs = {r["product_id"]: dict(r) for r in c.execute("SELECT * FROM item_prefs WHERE user_id = ?", (uid,))}
    names = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM products")}
    hist = defaultdict(list)
    for r in c.execute("SELECT product_id, bought_at, qty FROM purchases WHERE user_id = ? "
                       "ORDER BY bought_at, id", (uid,)):
        hist[r["product_id"]].append(dict(r))
    on_list = {r["product_id"] for r in c.execute("SELECT product_id FROM list_items WHERE user_id = ?", (uid,))}
    out = []
    for pid in set(hist) | {p for p, v in prefs.items() if v.get("interval_days")}:
        pref = prefs.get(pid, {})
        est = replenish.estimate(hist.get(pid, []), pref, today)
        out.append({"product_id": pid, "name": names.get(pid, "Item"), "estimate": est,
                    "state": replenish.reminder_state(est, pref) if est else "later",
                    "reminders": bool(pref.get("reminders", 1)), "excluded": bool(pref.get("excluded")),
                    "on_list": pid in on_list, "purchases": len(hist.get(pid, []))})
    out.sort(key=lambda r: (r["estimate"] is None, r["estimate"]["days_left"] if r["estimate"] else 0, r["name"]))
    return out, prefs, names


@app.get("/api/reminders")
@login_required
def reminders_get():
    uid = g.user["id"]
    with get_conn() as c:
        prefs_row = get_prefs(c, uid)
        tracked, prefs, names = _estimates(c, uid)
    tracked = [t for t in tracked if not t["excluded"]]
    due = [t for t in tracked if t["state"] == "due" and t["reminders"] and not t["on_list"]]
    favorites = [{"product_id": p, "name": names.get(p, "Item")} for p, v in prefs.items() if v.get("favorite")]
    excluded = [{"product_id": p, "name": names.get(p, "Item")} for p, v in prefs.items() if v.get("excluded")]
    return jsonify(enabled=prefs_row["reminders_on"], due=due if prefs_row["reminders_on"] else [],
                   tracked=tracked, favorites=favorites, excluded=excluded)


@app.post("/api/reminders/<int:product_id>")
@login_required
def reminder_action(product_id):
    b = request.get_json(silent=True) or {}
    action, uid = b.get("action"), g.user["id"]
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            return err("We couldn't find that item.", 404)
        _item_pref(c, uid, product_id)
        tracked, prefs, _ = _estimates(c, uid)
        cur = next((t for t in tracked if t["product_id"] == product_id), None)
        qty = (cur and cur["estimate"] and cur["estimate"]["usual_qty"]) or 1
        if action == "snooze":
            try:
                days = _num(b.get("days", 3), 1, 30, "snooze days", integer=True)
            except ValueError as e:
                return err(str(e))
            until = (datetime.utcnow() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("UPDATE item_prefs SET snoozed_until = ? WHERE user_id = ? AND product_id = ?",
                      (until, uid, product_id))
        elif action == "dismiss":
            last = cur["estimate"]["last_bought"] if cur and cur["estimate"] else datetime.utcnow().strftime("%Y-%m-%d")
            c.execute("UPDATE item_prefs SET dismissed_after = ? WHERE user_id = ? AND product_id = ?",
                      (last, uid, product_id))
        elif action == "complete":
            pid = _record_purchase(c, uid, product_id, qty)
            return jsonify(ok=True, purchase_id=pid)
        elif action == "add":
            c.execute("INSERT INTO list_items (user_id, product_id, qty) VALUES (?, ?, ?) "
                      "ON CONFLICT(user_id, product_id) DO UPDATE SET checked = 0", (uid, product_id, qty))
        else:
            return err("Choose snooze, dismiss, complete or add.")
    return jsonify(ok=True)


@app.get("/api/products/<int:product_id>/usage")
@login_required
def product_usage(product_id):
    uid = g.user["id"]
    with get_conn() as c:
        p = c.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if not p:
            return err("We couldn't find that item.", 404)
        pref = _item_pref(c, uid, product_id)
        hist = rows(c.execute("SELECT id, bought_at, qty FROM purchases WHERE user_id = ? AND product_id = ? "
                              "ORDER BY bought_at, id", (uid, product_id)))
    est = replenish.estimate(hist, pref)
    pref["reminders"], pref["excluded"], pref["favorite"] = (bool(pref[k]) for k in ("reminders", "excluded", "favorite"))
    return jsonify(prefs=pref, estimate=est, purchases=hist[-5:][::-1], count=len(hist))


@app.patch("/api/products/<int:product_id>/prefs")
@login_required
def product_prefs(product_id):
    b = request.get_json(silent=True) or {}
    uid, sets, vals = g.user["id"], [], []
    try:
        if "interval_days" in b:
            sets.append("interval_days = ?")
            vals.append(_num(b["interval_days"], 1, 365, "restock interval", integer=True))
        if "usual_qty" in b:
            sets.append("usual_qty = ?")
            vals.append(_num(b["usual_qty"], 1, 99, "quantity", integer=True))
    except ValueError as e:
        return err(str(e))
    for k in ("reminders", "excluded", "favorite"):
        if k in b:
            sets.append(f"{k} = ?")
            vals.append(1 if b[k] else 0)
    if not sets:
        return err("Nothing to update.")
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            return err("We couldn't find that item.", 404)
        _item_pref(c, uid, product_id)
        c.execute(f"UPDATE item_prefs SET {', '.join(sets)} WHERE user_id = ? AND product_id = ?",
                  vals + [uid, product_id])
        if b.get("excluded"):   # an excluded product leaves the active list too
            c.execute("DELETE FROM list_items WHERE user_id = ? AND product_id = ?", (uid, product_id))
    return jsonify(ok=True)


# ---------- feedback and admin ----------

@app.post("/api/feedback")
@login_required
def feedback():
    b = request.get_json(silent=True) or {}
    category = b.get("category")
    message = (b.get("message") or "").strip()
    if category not in ("bug", "idea", "wrong_price", "other"):
        return err("Choose what your feedback is about.")
    if not 5 <= len(message) <= 1000:
        return err("Write between 5 and 1000 characters.")
    if too_many(("feedback", g.user["id"]), 10, 86400):
        return err("You've reached today's feedback limit. Thank you.", 429)
    with get_conn() as c:
        c.execute("INSERT INTO feedback (user_id, category, message) VALUES (?, ?, ?)",
                  (g.user["id"], category, message))
    return jsonify(ok=True, message="Thanks. We read every message."), 201


@app.post("/api/admin/scrape")
def run_scrape():
    """Run the scraper. Needs the ADMIN_TOKEN environment variable and a matching header."""
    token = os.environ.get("ADMIN_TOKEN")
    sent = request.headers.get("X-Admin-Token", "")
    if not token or not hmac.compare_digest(sent.encode(), token.encode()):
        return err("Not allowed.", 403)
    return jsonify(scrape_all())


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
