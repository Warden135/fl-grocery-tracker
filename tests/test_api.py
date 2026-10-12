from app.db import get_conn
from tests.conftest import H, delete, patch, post


def product_id(c, query):
    return c.get(f"/api/products?q={query}").get_json()[0]["id"]


def stores_near(c, radius=10):
    return c.get(f"/api/stores/nearby?radius={radius}").get_json()["stores"]


# ---------- accounts ----------

def test_signup_login_logout_me(client):
    r = post(client, "/api/auth/signup", name="Ana", email="Ana@Example.com",
             password="longpassword", zip="33101")
    assert r.status_code == 201
    assert r.get_json()["user"]["city"] == "Miami"
    assert client.get("/api/me").get_json()["user"]["email"] == "ana@example.com"
    post(client, "/api/auth/logout")
    assert client.get("/api/me").get_json()["user"] is None
    assert post(client, "/api/auth/login", email="ana@example.com", password="nope").status_code == 401
    assert post(client, "/api/auth/login", email="ana@example.com", password="longpassword").status_code == 200


def test_signup_validation(client):
    ok = dict(name="B", email="b@example.com", password="longpassword", zip="32202")
    assert post(client, "/api/auth/signup", **{**ok, "password": "short"}).status_code == 400
    assert post(client, "/api/auth/signup", **{**ok, "email": "nope"}).status_code == 400
    assert post(client, "/api/auth/signup", **{**ok, "zip": "abc"}).status_code == 400
    assert post(client, "/api/auth/signup", **{**ok, "zip": "99999"}).status_code == 503  # offline lookup
    assert post(client, "/api/auth/signup", **ok).status_code == 201
    assert post(client, "/api/auth/signup", **ok).status_code == 409


def test_writes_need_app_header_and_login(client, make_user):
    assert client.post("/api/auth/login", json={}).status_code == 403          # no X-App header
    assert client.get("/api/basket").status_code == 401                        # not signed in
    c, _ = make_user()
    assert c.post("/api/list", json={"product_id": 1}).status_code == 403      # no X-App header


def test_update_profile(make_user):
    c, _ = make_user()
    r = patch(c, "/api/me", zip="33101", radius_miles=5)
    assert r.status_code == 200
    u = r.get_json()["user"]
    assert (u["zip"], u["radius_miles"], u["city"]) == ("33101", 5, "Miami")
    assert patch(c, "/api/me", radius_miles=7).status_code == 400


# ---------- nearby search and basket ----------

def test_radius_limits_stores(make_user):
    c, _ = make_user("32202")
    five, ten, fifteen = (len(stores_near(c, r)) for r in (5, 10, 15))
    assert 0 < five < ten <= fifteen
    assert all(s["distance"] <= 5 for s in stores_near(c, 5))
    # nothing in Miami is near Jacksonville
    names = " ".join(s["name"] for s in stores_near(c, 15))
    assert "Brickell" not in names and "Riverside" in names


def test_list_is_per_user(make_user):
    a, _ = make_user()
    b, _ = make_user()
    pid = product_id(a, "Milk")
    assert post(a, "/api/list", product_id=pid, qty=2).status_code == 201
    assert len(a.get("/api/list").get_json()) == 1
    assert b.get("/api/list").get_json() == []
    item = a.get("/api/list").get_json()[0]
    assert patch(b, f"/api/list/{item['id']}", qty=5).status_code == 404       # not B's item
    assert patch(a, f"/api/list/{item['id']}", qty=3).status_code == 200
    post(a, "/api/list", product_id=pid)                                       # adding again adds one
    assert a.get("/api/list").get_json()[0]["qty"] == 4
    delete(a, f"/api/list/{item['id']}")
    assert a.get("/api/list").get_json() == []


def test_basket_plans(make_user):
    c, _ = make_user("32202")
    for q in ("Milk", "Eggs", "Bananas", "Chicken"):
        post(c, "/api/list", product_id=product_id(c, q))
    r = c.get("/api/basket?radius=10").get_json()
    assert r["best"] and r["stores"]
    totals = [s["total"] for s in r["stores"] if s["complete"]]
    assert totals == sorted(totals)
    plans = {p["stops"]: p for p in r["plans"]}
    assert 1 in plans and 2 in plans
    assert plans[2]["total"] <= plans[1]["total"]
    for item in r["items"]:
        best = r["per_item"][str(item["product_id"])]["best"]
        assert best and best["distance"] <= 10
    # checked-off items drop out of the totals
    first = r["items"][0]["item_id"]
    patch(c, f"/api/list/{first}", checked=True)
    r2 = c.get("/api/basket?radius=10").get_json()
    assert r2["best"]["total"] < r["best"]["total"] or len(r2["best"]["items"]) == 3


def test_basket_empty_list_and_no_stores(make_user):
    c, _ = make_user("32202")
    r = c.get("/api/basket").get_json()
    assert r["stores"] == [] and r["plans"] == [] and r["best"] is None
    far, _ = make_user("33101")
    patch(far, "/api/me", zip="32250")  # beach: few stores within 1 mile
    post(far, "/api/list", product_id=product_id(far, "Milk"))
    assert far.get("/api/basket?radius=1").get_json()["stores"] == []


def test_product_prices_and_history(make_user):
    c, _ = make_user("32202")
    pid = product_id(c, "Eggs")
    r = c.get(f"/api/products/{pid}/prices?radius=10").get_json()
    prices = [o["price"] for o in r["options"]]
    assert prices and prices == sorted(prices)
    pts = c.get(f"/api/history/{pid}?days=60&radius=10").get_json()["points"]
    assert len(pts) > 5


# ---------- price reports, confirmations, points ----------

def _store_and_product(c):
    store = stores_near(c)[0]
    return store["id"], product_id(c, "Butter")


def test_report_earns_points_once_per_day(make_user):
    c, u = make_user()
    sid, pid = _store_and_product(c)
    r = post(c, "/api/prices", store_id=sid, product_id=pid, price=4.60).get_json()
    assert r["points"] == 5 and r["status"] == "ok"
    again = post(c, "/api/prices", store_id=sid, product_id=pid, price=4.62).get_json()
    assert again["points"] == 0
    assert c.get("/api/me").get_json()["user"]["points"] == 5


def test_report_validation(make_user):
    c, _ = make_user()
    sid, pid = _store_and_product(c)
    assert post(c, "/api/prices", store_id=sid, product_id=pid, price=0).status_code == 400
    assert post(c, "/api/prices", store_id=sid, product_id=pid, price="abc").status_code == 400
    assert post(c, "/api/prices", store_id=99999, product_id=pid, price=2).status_code == 404


def test_unusual_price_needs_two_confirmations(make_user):
    reporter, ru = make_user()
    sid, pid = _store_and_product(reporter)
    r = post(reporter, "/api/prices", store_id=sid, product_id=pid, price=40.00).get_json()
    assert r["status"] == "pending" and r["points"] == 0
    price_id = r["id"]
    c1, _ = make_user()
    c2, _ = make_user()
    assert post(c1, f"/api/prices/{price_id}/vote", vote="confirm").status_code == 200
    # still pending, so it must not be the displayed price yet
    opts = c1.get(f"/api/products/{pid}/prices").get_json()["options"]
    assert all(o["price"] != 40.00 for o in opts)
    assert post(c2, f"/api/prices/{price_id}/vote", vote="confirm").status_code == 200
    opts = c1.get(f"/api/products/{pid}/prices").get_json()["options"]
    assert any(o["price"] == 40.00 for o in opts)
    # reporter: 2 + 2 confirmation bonuses + 5 for the verified report
    assert reporter.get("/api/me").get_json()["user"]["points"] == 9


def test_confirm_rules(make_user):
    a, au = make_user()
    b, bu = make_user()
    sid, pid = _store_and_product(a)
    price_id = post(a, "/api/prices", store_id=sid, product_id=pid, price=4.55).get_json()["id"]
    assert post(a, f"/api/prices/{price_id}/vote", vote="confirm").status_code == 403    # own price
    r = post(b, f"/api/prices/{price_id}/vote", vote="confirm")
    assert r.status_code == 200 and r.get_json()["points"] == 1
    assert post(b, f"/api/prices/{price_id}/vote", vote="confirm").status_code == 409     # twice
    assert b.get("/api/me").get_json()["user"]["points"] == 1
    assert a.get("/api/me").get_json()["user"]["points"] == 5 + 2


def test_confirm_bonus_is_capped(make_user):
    a, _ = make_user()
    sid, pid = _store_and_product(a)
    price_id = post(a, "/api/prices", store_id=sid, product_id=pid, price=4.58).get_json()["id"]
    for _ in range(5):
        v, _ = make_user()
        assert post(v, f"/api/prices/{price_id}/vote", vote="confirm").status_code == 200
    assert a.get("/api/me").get_json()["user"]["points"] == 5 + 3 * 2   # only 3 bonuses


def test_verify_feed_excludes_own_and_voted(make_user):
    a, _ = make_user()
    sid, pid = _store_and_product(a)
    own = post(a, "/api/prices", store_id=sid, product_id=pid, price=4.57).get_json()["id"]
    feed = a.get("/api/prices/verify-feed").get_json()["feed"]
    assert feed and own not in [f["price_id"] for f in feed]
    first = feed[0]["price_id"]
    post(a, f"/api/prices/{first}/vote", vote="confirm")
    assert first not in [f["price_id"] for f in a.get("/api/prices/verify-feed").get_json()["feed"]]


def test_correction_marks_original_disputed(make_user):
    a, _ = make_user()
    b, _ = make_user()
    sid, pid = _store_and_product(a)
    orig = post(a, "/api/prices", store_id=sid, product_id=pid, price=4.50).get_json()["id"]
    r = post(b, "/api/prices", store_id=sid, product_id=pid, price=5.20, corrects_price_id=orig)
    assert r.get_json()["points"] == 5
    with get_conn() as conn:
        assert conn.execute("SELECT disputed_count FROM prices WHERE id = ?", (orig,)).fetchone()[0] == 1
    shown = [o for o in a.get(f"/api/products/{pid}/prices").get_json()["options"] if o["store_id"] == sid]
    assert shown[0]["price"] == 5.20


# ---------- rewards ----------

def test_redeem_perk(make_user):
    c, u = make_user()
    r = post(c, "/api/rewards/scout_badge/redeem")
    assert r.status_code == 400 and "50 more points" in r.get_json()["error"]
    with get_conn() as conn:
        conn.execute("UPDATE users SET points = 120 WHERE id = ?", (u["id"],))
        conn.execute("INSERT INTO points_ledger (user_id, delta, reason) VALUES (?, 120, 'report')", (u["id"],))
    r = post(c, "/api/rewards/scout_badge/redeem")
    assert r.status_code == 200
    me = r.get_json()["user"]
    assert me["points"] == 70 and "scout_badge" in me["perks"]
    assert me["level"]["name"] == "Scout"                      # level uses lifetime points
    assert post(c, "/api/rewards/scout_badge/redeem").status_code == 409
    assert post(c, "/api/rewards/nothing/redeem").status_code == 404
    data = c.get("/api/rewards").get_json()
    assert [k["key"] for k in data["catalog"] if k["owned"]] == ["scout_badge"]
    assert data["ledger"][0]["text"] == "Unlocked a perk"


# ---------- reviews, feedback, alerts, stores ----------

def test_reviews(make_user):
    c, u = make_user()
    sid = stores_near(c)[0]["id"]
    assert post(c, f"/api/stores/{sid}/reviews", rating=0).status_code == 400
    r = post(c, f"/api/stores/{sid}/reviews", rating=5, comment="Great produce and friendly staff here.")
    assert r.get_json()["points"] == 3
    r = post(c, f"/api/stores/{sid}/reviews", rating=4, comment="Updated: still great produce overall.")
    assert r.get_json()["points"] == 0                          # edits don't earn again
    d = c.get(f"/api/stores/{sid}").get_json()
    mine = [x for x in d["reviews"] if x["mine"]]
    assert len(mine) == 1 and mine[0]["rating"] == 4 and d["mine"]["rating"] == 4
    short, _ = make_user()
    assert post(short, f"/api/stores/{sid}/reviews", rating=3, comment="ok").get_json()["points"] == 0


def test_feedback_validation(make_user):
    c, _ = make_user()
    assert post(c, "/api/feedback", category="nope", message="Hello there").status_code == 400
    assert post(c, "/api/feedback", category="idea", message="hi").status_code == 400
    assert post(c, "/api/feedback", category="idea", message="Add a barcode scanner please").status_code == 201


def test_alerts_trigger_within_radius(make_user):
    c, _ = make_user("32202")
    pid = product_id(c, "Eggs")
    cheapest = c.get(f"/api/products/{pid}/prices?radius=10").get_json()["options"][0]["price"]
    post(c, "/api/alerts", product_id=pid, target_price=cheapest + 0.5)
    a = c.get("/api/alerts").get_json()[0]
    assert a["triggered"] and a["best"]["price"] <= cheapest + 0.5
    post(c, "/api/alerts", product_id=pid, target_price=0.10)   # replaces the old alert
    alerts = c.get("/api/alerts").get_json()
    assert len(alerts) == 1 and not alerts[0]["triggered"]


def test_add_store_and_product(make_user):
    c, _ = make_user("33101")
    r = post(c, "/api/stores", name="Corner Market", zip="33101", address="1 Example St")
    assert r.status_code == 201
    same = post(c, "/api/stores", name="corner market", zip="33101", address="1 Example St")
    assert same.status_code == 200 and same.get_json()["id"] == r.get_json()["id"]
    assert "Corner Market" in [s["name"] for s in stores_near(c, 5)]
    p = post(c, "/api/products", name="Plantains")
    assert p.status_code == 201
    assert post(c, "/api/products", name="plantains").status_code == 200
    assert post(c, "/api/products", name="x").status_code == 400


def test_admin_scrape_needs_token(client, monkeypatch):
    assert client.post("/api/admin/scrape").status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "t0ken")
    assert client.post("/api/admin/scrape", headers={"X-Admin-Token": "bad"}).status_code == 403


def test_site_password_gate(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "gate")
    assert client.get("/").status_code == 401
    assert client.get("/manifest.webmanifest").status_code == 200
    import base64
    ok = {"Authorization": "Basic " + base64.b64encode(b"x:gate").decode()}
    assert client.get("/", headers=ok).status_code == 200
