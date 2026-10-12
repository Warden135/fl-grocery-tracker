"""Apify connector: parsing, suggested vs confirmed matches, and where prices land."""
from app import apify
from app.db import get_conn
from tests.conftest import post


def item(id_, title, price, **kw):
    return {"usItemId": id_, "title": title, "price": price, "recordType": "listing", **kw}


def test_parse_drops_bad_rows():
    rows = apify.parse_items([
        item("1", "Great Value Whole Milk Gallon", 3.12, brand="Great Value", url="https://w/ip/1"),
        item("2", "No price", None), item("3", "", 2.0), {"recordType": "error", "error": "blocked"},
        item("", "No id", 1.0), item("4", "Absurd", 99999), "junk"])
    assert [r["external_id"] for r in rows] == ["1"]
    assert rows[0]["price"] == 3.12 and rows[0]["brand"] == "Great Value"


def _product(c, q):
    return c.get(f"/api/products?q={q}").get_json()[0]


def test_search_makes_suggestions_not_prices(make_user):
    c, u = make_user("32202")
    milk = _product(c, "Spaghetti")
    calls = []

    def fake(payload):
        calls.append(payload)
        return [item("11", "Barilla Spaghetti Pasta, 1 lb box", 1.58),
                item("12", "Birthday Cake Mix", 2.0)]
    saved, sug = apify.sync("32202", 30.3322, -81.6557, [milk["id"]], runner=fake)
    assert saved == 0 and sug == 1                                       # unrelated result ignored
    assert calls[0]["keywords"] == ["Spaghetti 1 lb"] and calls[0]["zip"] == "32202"
    with get_conn() as conn:
        assert conn.execute("SELECT status FROM product_links WHERE external_id = '11'").fetchone()[0] == "suggested"
        assert conn.execute("SELECT COUNT(*) FROM prices p JOIN listings l ON l.id = p.listing_id "
                            "WHERE p.source = 'apify' AND l.product_id = ?", (milk["id"],)).fetchone()[0] == 0
    # nothing is searched again while a suggestion is waiting
    apify.sync("32202", 30.3322, -81.6557, [milk["id"]], runner=lambda p: (_ for _ in ()).throw(AssertionError("no")))


def test_confirm_saves_online_price_and_label(make_user):
    c, u = make_user("32202")
    eggs = _product(c, "Eggs")
    post(c, "/api/list", product_id=eggs["id"])
    apify.sync("32202", 30.3322, -81.6557, [eggs["id"]],
               runner=lambda p: [item("21", "Great Value Large White Eggs, 12 Count", 2.48)])
    link = c.get("/api/online").get_json()["links"][0]
    assert link["status"] == "suggested" and link["price_seen"] == 2.48
    assert post(c, f"/api/online/links/{link['id']}", action="confirm").status_code == 200
    comp = c.get("/api/compare").get_json()
    cell = next(i for i in comp["items"] if i["product_id"] == eggs["id"])["prices"]["walmart"]
    assert cell["price"] == 2.48 and cell["quality"] == "online" and comp["flags"]["online"]
    assert any("store's website" in n for n in comp["notes"])
    assert "Walmart.com prices" in cell["store"]
    # online pseudo-stores never appear as places to shop or to report prices
    assert all("Walmart.com" not in s["name"] for s in c.get("/api/stores/nearby").get_json()["stores"])
    # a later sync refreshes the confirmed item by id
    seen = []
    saved, _ = apify.sync("32202", 30.3322, -81.6557, [eggs["id"]],
                          runner=lambda p: seen.append(p) or [item("21", "x eggs", 2.58)])
    assert saved == 1 and seen[0]["itemIds"] == ["21"]
    comp = c.get("/api/compare").get_json()
    assert next(i for i in comp["items"] if i["product_id"] == eggs["id"])["prices"]["walmart"]["price"] == 2.58


def test_reject_is_remembered(make_user):
    c, u = make_user("32202")
    butter = _product(c, "Butter")
    post(c, "/api/list", product_id=butter["id"])
    feed = lambda p: [item("31", "Great Value Butter Salted, 1 lb", 3.98)]
    apify.sync("32202", 30.3322, -81.6557, [butter["id"]], runner=feed)
    lid = c.get("/api/online").get_json()["links"][0]["id"]
    post(c, f"/api/online/links/{lid}", action="reject")
    assert c.get("/api/online").get_json()["links"] == []
    assert apify.sync("32202", 30.3322, -81.6557, [butter["id"]], runner=feed) == (0, 0)   # same item not suggested again


def test_endpoints_need_token_and_list(make_user, monkeypatch):
    c, _ = make_user("32202")
    monkeypatch.setenv("APIFY_TOKEN", "")
    assert post(c, "/api/online/sync").status_code == 503
    assert c.get("/api/online").get_json()["configured"] is False
    monkeypatch.setenv("APIFY_TOKEN", "t")
    assert post(c, "/api/online/sync").status_code == 400                   # empty list
    assert post(c, "/api/online/links/999999", action="confirm").status_code == 404
    assert post(c, "/api/online/links/1", action="nope").status_code == 400


def test_run_actor_errors_are_readable(monkeypatch):
    import requests
    monkeypatch.setenv("APIFY_TOKEN", "t")

    class R:
        def __init__(self, code, body=None): self.status_code, self._b = code, body
        def json(self): return self._b
    for code, text in ((401, "rejected the token"), (402, "out of credit"), (500, "error (500)")):
        monkeypatch.setattr(requests, "post", lambda *a, **k: R(code))
        try:
            apify.run_actor({})
        except apify.ApifyError as e:
            assert text in str(e)
        else:
            raise AssertionError("expected ApifyError")
    seen = {}
    monkeypatch.setattr(requests, "post", lambda url, **k: seen.update(url=url, **k) or R(201, [{"a": 1}]))
    assert apify.run_actor({"zip": "32202"}) == [{"a": 1}]
    assert "crawloop~walmart-scraper/run-sync-get-dataset-items" in seen["url"]
    assert seen["headers"]["Authorization"] == "Bearer t" and "token" not in seen["params"]


def test_background_start_records_result(make_user, monkeypatch):
    import time
    c, u = make_user("32202")
    milk = _product(c, "Milk")
    monkeypatch.setattr(apify, "run_actor", lambda payload: [item("41", "Great Value Whole Milk, Gallon", 3.1)])
    log_id = apify.start("32202", 30.3322, -81.6557, [milk["id"]])
    assert log_id
    for _ in range(50):
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM sync_log WHERE id = ?", (log_id,)).fetchone()
        if row["state"] != "running":
            break
        time.sleep(0.1)
    assert row["state"] == "done" and row["suggestions"] >= 0
