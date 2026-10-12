"""Brands, removal + purchase history, restock estimates, three-store comparison,
deals and preferences."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

from app import brands, db, providers, replenish
from app.compare import build_compare
from app.db import get_conn
from tests.conftest import H, delete, patch, post

HERE = (30.0, -81.0)
TODAY = date(2026, 10, 12)


# ---------- helpers ----------

def prow(pid, sid, chain, price, dist_lat, source="user", verified=0, store_source="user"):
    return {"product_id": pid, "store_id": sid, "store": f"{chain} #{sid}", "chain": chain, "price": price,
            "lat": HERE[0] + dist_lat, "lng": HERE[1], "store_source": store_source, "source": source,
            "price_id": pid * 100 + sid, "verified_count": verified, "seen_at": "2026-10-10 12:00:00"}


def item(pid, name, qty=1, checked=0):
    return {"item_id": pid, "product_id": pid, "name": name, "qty": qty, "checked": checked}


def prefs(**kw):
    base = {"preferred_chains": [], "programs": [], "strategy": "value", "min_savings": 2.0,
            "cost_per_mile": 0.0, "verified_only": False, "budget": None}
    base.update(kw)
    return base


def offer(chain, pid, **kw):
    o = {"id": 1, "chain": chain, "product_id": pid, "kind": "sale", "title": "Sale", "sale_price": None,
         "amount_off": None, "min_spend": None, "max_qty": None, "requires_account": 0, "conditions": "",
         "starts_on": None, "expires_on": None, "source": "test ad", "source_url": None}
    o.update(kw)
    return o


def pid(c, q):
    return c.get(f"/api/products?q={q}").get_json()[0]["id"]


def add(c, product_id, qty=1):
    assert post(c, "/api/list", product_id=product_id, qty=qty).status_code == 201


def list_items(c):
    return c.get("/api/list").get_json()


def backdate(user_id, product_id, days_list):
    """Insert purchases that happened N days ago."""
    with get_conn() as c:
        for d in days_list:
            ts = (datetime.utcnow() - timedelta(days=d)).strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO purchases (user_id, product_id, qty, bought_at) VALUES (?, ?, 1, ?)",
                      (user_id, product_id, ts))


# ---------- brands ----------

def test_chain_keys_and_order():
    assert [b["name"] for b in brands.BRANDS] == ["Winn-Dixie", "Publix", "Walmart"]
    assert brands.chain_key("Winn-Dixie") == "winndixie"
    assert brands.chain_key("Walmart Supercenter") == "walmart"
    assert brands.chain_key("Aldi") is None
    assert all(b["logo"] is None for b in brands.brands_json())   # no logo files shipped


def test_me_includes_brands(make_user):
    c, _ = make_user()
    assert len(c.get("/api/me").get_json()["brands"]) == 3


# ---------- restock estimates ----------

def test_estimate_learns_median_interval():
    hist = [{"bought_at": f"2026-09-{d:02d} 10:00:00", "qty": 2} for d in (1, 8, 15, 22, 29)]
    est = replenish.estimate(hist, None, today=date(2026, 10, 5))
    assert est["interval_days"] == 7 and est["runs_out"] == "2026-10-06"
    assert est["usual_qty"] == 2 and est["confidence"] == "good" and est["due"]
    assert replenish.estimate(hist[:1], None, today=TODAY) is None          # one purchase: nothing to learn
    assert replenish.estimate(hist[:1], {"interval_days": 14}, today=TODAY)["manual"]
    assert replenish.estimate(hist[:2], None, today=TODAY)["confidence"] == "low"


def test_same_day_purchases_do_not_shrink_interval():
    hist = [{"bought_at": "2026-09-01 08:00:00", "qty": 1}, {"bought_at": "2026-09-01 18:00:00", "qty": 1},
            {"bought_at": "2026-09-15 09:00:00", "qty": 1}]
    assert replenish.estimate(hist, None, today=TODAY)["interval_days"] == 14


# ---------- removal and purchase history ----------

def test_remove_not_bought_records_nothing(make_user):
    c, _ = make_user()
    add(c, pid(c, "Milk"))
    item_id = list_items(c)[0]["id"]
    r = post(c, f"/api/list/{item_id}/remove", bought=False)
    assert r.status_code == 200 and r.get_json()["purchase_id"] is None
    assert list_items(c) == []
    assert c.get("/api/purchases").get_json()["total"] == 0


def test_remove_bought_records_purchase_and_undo_restores(make_user):
    c, _ = make_user()
    add(c, pid(c, "Eggs"), qty=3)
    item_id = list_items(c)[0]["id"]
    r = post(c, f"/api/list/{item_id}/remove", bought=True, chain="publix", paid=3.49)
    d = r.get_json()
    assert d["purchase_id"] and list_items(c) == []
    hist = c.get("/api/purchases").get_json()
    assert hist["total"] == 1 and hist["purchases"][0]["qty"] == 3
    assert hist["purchases"][0]["chain_name"] == "Publix" and hist["purchases"][0]["paid"] == 3.49
    assert post(c, f"/api/purchases/{d['purchase_id']}/undo").status_code == 200
    assert c.get("/api/purchases").get_json()["total"] == 0
    assert list_items(c)[0]["qty"] == 3


def test_remove_validation_and_ownership(make_user):
    c, _ = make_user()
    other, _ = make_user()
    add(c, pid(c, "Milk"))
    item_id = list_items(c)[0]["id"]
    assert post(c, f"/api/list/{item_id}/remove").status_code == 400                   # must say yes or no
    assert post(c, f"/api/list/{item_id}/remove", bought=True, chain="aldi").status_code == 400
    assert post(other, f"/api/list/{item_id}/remove", bought=False).status_code == 404  # someone else's item
    assert list_items(c)                                                              # untouched
    pu = post(c, f"/api/list/{item_id}/remove", bought=True).get_json()["purchase_id"]
    assert post(other, f"/api/purchases/{pu}/undo").status_code == 404
    assert delete(other, f"/api/purchases/{pu}").status_code == 200                   # no-op for non-owner
    assert c.get("/api/purchases").get_json()["total"] == 1


def test_history_survives_when_item_readded_and_removed(make_user):
    c, _ = make_user()
    milk = pid(c, "Milk")
    for _ in range(2):
        add(c, milk)
        post(c, f"/api/list/{list_items(c)[0]['id']}/remove", bought=True)
    assert c.get("/api/purchases").get_json()["total"] == 2


# ---------- reminders ----------

def test_reminders_due_snooze_dismiss_complete_add(make_user):
    c, u = make_user()
    milk, eggs = pid(c, "Milk"), pid(c, "Eggs")
    backdate(u["id"], milk, [21, 14, 7])          # weekly, last bought a week ago -> due
    backdate(u["id"], eggs, [30, 20, 10])         # every 10 days, last bought 10 days ago -> due
    d = c.get("/api/reminders").get_json()
    assert {r["name"] for r in d["due"]} >= {"Milk, whole, 1 gal", "Eggs, large, 1 dozen"}
    milk_est = next(t for t in d["tracked"] if t["product_id"] == milk)["estimate"]
    assert milk_est["interval_days"] == 7 and milk_est["confidence"] == "fair"

    assert post(c, f"/api/reminders/{milk}", action="snooze", days=3).status_code == 200
    assert milk not in [r["product_id"] for r in c.get("/api/reminders").get_json()["due"]]
    assert post(c, f"/api/reminders/{eggs}", action="dismiss").status_code == 200
    assert eggs not in [r["product_id"] for r in c.get("/api/reminders").get_json()["due"]]
    # a new purchase after dismissing brings the reminder back for the next cycle
    backdate(u["id"], eggs, [0])
    assert next(t for t in c.get("/api/reminders").get_json()["tracked"] if t["product_id"] == eggs)["state"] == "later"

    assert post(c, f"/api/reminders/{milk}", action="add").status_code == 200
    assert [i["product_id"] for i in list_items(c)] == [milk]
    assert milk not in [r["product_id"] for r in c.get("/api/reminders").get_json()["due"]]   # already on list
    assert post(c, f"/api/reminders/{milk}", action="bogus").status_code == 400


def test_reminder_complete_records_a_purchase(make_user):
    c, u = make_user()
    milk = pid(c, "Milk")
    backdate(u["id"], milk, [14, 7])
    before = c.get("/api/purchases").get_json()["total"]
    r = post(c, f"/api/reminders/{milk}", action="complete")
    assert r.status_code == 200 and r.get_json()["purchase_id"]
    assert c.get("/api/purchases").get_json()["total"] == before + 1
    assert milk not in [x["product_id"] for x in c.get("/api/reminders").get_json()["due"]]


def test_reminders_can_be_disabled_globally_and_per_item(make_user):
    c, u = make_user()
    milk = pid(c, "Milk")
    backdate(u["id"], milk, [14, 7])
    assert c.get("/api/reminders").get_json()["due"]
    assert patch(c, f"/api/products/{milk}/prefs", reminders=False).status_code == 200
    assert not c.get("/api/reminders").get_json()["due"]
    patch(c, f"/api/products/{milk}/prefs", reminders=True)
    assert c.get("/api/reminders").get_json()["due"]
    patch(c, "/api/prefs", reminders_on=False)
    d = c.get("/api/reminders").get_json()
    assert d["enabled"] is False and d["due"] == []


def test_custom_interval_and_validation(make_user):
    c, u = make_user()
    milk = pid(c, "Milk")
    backdate(u["id"], milk, [3])
    assert c.get(f"/api/products/{milk}/usage").get_json()["estimate"] is None     # one purchase, no estimate
    assert patch(c, f"/api/products/{milk}/prefs", interval_days=5, usual_qty=2).status_code == 200
    est = c.get(f"/api/products/{milk}/usage").get_json()["estimate"]
    assert est["interval_days"] == 5 and est["manual"] and est["usual_qty"] == 2 and est["days_left"] == 2
    assert patch(c, f"/api/products/{milk}/prefs", interval_days=0).status_code == 400
    assert patch(c, f"/api/products/{milk}/prefs", interval_days=9999).status_code == 400
    patch(c, f"/api/products/{milk}/prefs", interval_days=None)
    assert c.get(f"/api/products/{milk}/usage").get_json()["estimate"] is None


# ---------- preferences ----------

def test_prefs_validation_and_roundtrip(make_user):
    c, _ = make_user()
    r = patch(c, "/api/prefs", preferred_chains=["publix"], budget=120, household=4, strategy="fewest",
              min_savings=3, cost_per_mile=0.5, verified_only=True, avoid_terms="peanut, pork",
              fav_brands="Chobani", diet_note="vegetarian-ish")
    p = r.get_json()["user"]["prefs"]
    assert p["preferred_chains"] == ["publix"] and p["budget"] == 120 and p["household"] == 4
    assert p["strategy"] == "fewest" and p["verified_only"] is True and p["avoid_terms"] == "peanut, pork"
    for bad in ({"preferred_chains": ["aldi"]}, {"strategy": "cheapest"}, {"household": 0},
                {"budget": -5}, {"cost_per_mile": 99}, {"min_savings": "abc"}):
        assert patch(c, "/api/prefs", **bad).status_code == 400, bad


def test_excluded_and_avoided_products_are_not_suggested(make_user):
    c, _ = make_user()
    milk = pid(c, "Milk")
    add(c, milk)
    patch(c, f"/api/products/{milk}/prefs", excluded=True)
    assert list_items(c) == []                                                       # leaves the list too
    assert all(p["id"] != milk for p in c.get("/api/products?q=Milk").get_json())
    assert c.get("/api/reminders").get_json()["excluded"][0]["product_id"] == milk
    patch(c, f"/api/products/{milk}/prefs", excluded=False)
    assert any(p["id"] == milk for p in c.get("/api/products?q=Milk").get_json())
    patch(c, "/api/prefs", avoid_terms="eggs")
    assert not c.get("/api/products?q=Eggs").get_json()


def test_favorites_sort_first(make_user):
    c, _ = make_user()
    rice = pid(c, "Rice")
    patch(c, f"/api/products/{rice}/prefs", favorite=True)
    assert c.get("/api/products?q=").get_json()[0]["id"] == rice


# ---------- comparison ----------

def test_compare_three_chains_picks_lowest_and_explains():
    items = [item(1, "Milk"), item(2, "Eggs", qty=2)]
    rows = [prow(1, 1, "Winn-Dixie", 3.79, 0.02, "scrape"), prow(1, 2, "Publix", 3.99, 0.03, "scrape"),
            prow(1, 3, "Walmart", 3.49, 0.04, "scrape"),
            prow(2, 1, "Winn-Dixie", 2.99, 0.02, "scrape"), prow(2, 2, "Publix", 3.49, 0.03, "scrape"),
            prow(2, 3, "Walmart", 2.79, 0.04, "scrape")]
    r = build_compare(items, rows, [], HERE, 10, prefs())
    milk = r["items"][0]
    assert milk["best"] == "walmart" and milk["save_vs_next"] == 0.30 and milk["save_vs_highest"] == 0.50
    assert "lowest price at $3.49" in milk["reason"] and "Winn-Dixie $3.79" in milk["reason"]
    assert milk["prices"]["walmart"]["quality"] == "verified"
    eggs = r["items"][1]
    assert eggs["save_vs_next"] == 0.40 and eggs["save_vs_highest"] == 1.40          # per qty of 2
    totals = {s["chain"]: s["total"] for s in r["singles"]}
    assert totals == {"walmart": 3.49 + 5.58, "winndixie": 3.79 + 5.98, "publix": 3.99 + 6.98}
    assert r["singles"][0]["chain"] == "walmart" and r["singles"][0]["vs_priciest"] == round(10.97 - 9.07, 2)
    assert r["flags"] == {"sample": False, "unconfirmed": False, "stale": False, "online": False}


def test_compare_never_invents_prices():
    items = [item(1, "Milk"), item(2, "Saffron")]
    rows = [prow(1, 1, "Publix", 3.99, 0.02, "scrape")]
    r = build_compare(items, rows, [], HERE, 10, prefs())
    assert [u["name"] for u in r["unpriced"]] == ["Saffron"]
    saffron = r["items"][1]
    assert saffron["best"] is None and all(v is None for v in saffron["prices"].values())
    assert "No price data" in saffron["reason"]
    milk = r["items"][0]
    assert milk["prices"]["walmart"] is None and "Only Publix" in milk["reason"]
    assert any("No Walmart store" in n for n in r["notes"])


def test_quality_labels_and_verified_only():
    items = [item(1, "Milk")]
    rows = [prow(1, 1, "Winn-Dixie", 3.0, 0.02, "user", 0), prow(1, 2, "Publix", 3.5, 0.03, "user", 2),
            prow(1, 3, "Walmart", 2.0, 0.04, "demo", 0, store_source="demo")]
    r = build_compare(items, rows, [], HERE, 10, prefs())
    q = {k: v["quality"] for k, v in r["items"][0]["prices"].items()}
    assert q == {"winndixie": "reported", "publix": "verified", "walmart": "sample"}
    assert r["flags"]["sample"] and r["flags"]["unconfirmed"]
    assert any("sample data" in n for n in r["notes"])
    v = build_compare(items, rows, [], HERE, 10, prefs(verified_only=True))
    assert v["items"][0]["prices"]["publix"] and v["items"][0]["prices"]["walmart"] is None
    assert any("Verified-only" in n for n in v["notes"])


def test_stale_prices_are_flagged():
    rows = [prow(1, 1, "Publix", 3.0, 0.02, "scrape")]
    rows[0]["seen_at"] = "2026-08-01 12:00:00"
    r = build_compare([item(1, "Milk")], rows, [], HERE, 10, prefs(), now=datetime(2026, 10, 12, tzinfo=timezone.utc))
    assert r["items"][0]["prices"]["publix"]["stale"] and r["flags"]["stale"]


def test_offers_apply_once_and_only_when_usable():
    items = [item(1, "Milk", qty=3)]
    rows = [prow(1, 1, "Winn-Dixie", 4.00, 0.02, "scrape"), prow(1, 2, "Publix", 3.80, 0.03, "scrape")]
    two = [offer("winndixie", 1, id=1, sale_price=3.00), offer("winndixie", 1, id=2, amount_off=0.50)]
    r = build_compare(items, rows, two, HERE, 10, prefs())
    w = r["items"][0]["prices"]["winndixie"]
    assert w["effective"] == 3.00 and w["offer"]["id"] == 1                   # best single offer, no stacking
    assert w["offer"]["saves"] == 3.00
    single = next(s for s in r["singles"] if s["chain"] == "winndixie")
    assert single["total"] == 9.00 and single["offer_savings"] == 3.00        # counted exactly once
    assert r["items"][0]["best"] == "winndixie"

    capped = build_compare(items, rows, [offer("winndixie", 1, sale_price=3.00, max_qty=1)], HERE, 10, prefs())
    assert next(s for s in capped["singles"] if s["chain"] == "winndixie")["total"] == 11.00   # 3 + 4 + 4

    # an offer that needs a store account is not applied until the shopper says they have one
    acct = [offer("winndixie", 1, sale_price=3.00, requires_account=1)]
    no = build_compare(items, rows, acct, HERE, 10, prefs())
    assert no["items"][0]["prices"]["winndixie"]["offer"] is None
    assert no["items"][0]["prices"]["winndixie"]["locked_offer"]["program"] == "winndixie"
    yes = build_compare(items, rows, acct, HERE, 10, prefs(programs=["winndixie"]))
    assert yes["items"][0]["prices"]["winndixie"]["offer"]["saves"] == 3.00

    # a sale price that is not lower than the shelf price does nothing
    flat = build_compare(items, rows, [offer("winndixie", 1, sale_price=4.50)], HERE, 10, prefs())
    assert flat["items"][0]["prices"]["winndixie"]["offer"] is None


def test_min_spend_offer_needs_the_subtotal():
    rows = [prow(1, 1, "Publix", 5.00, 0.02, "scrape"), prow(2, 1, "Publix", 10.00, 0.02, "scrape")]
    o = [offer("publix", 1, amount_off=1.00, min_spend=12.0)]
    both = build_compare([item(1, "A"), item(2, "B")], rows, o, HERE, 10, prefs())
    assert both["singles"][0]["total"] == 14.00                                # $15 subtotal >= $12 minimum
    only = build_compare([item(1, "A")], rows, o, HERE, 10, prefs())
    assert only["singles"][0]["total"] == 5.00                                 # $5 subtotal, offer not met


def test_strategies_and_travel_cost():
    # Walmart is cheapest for milk, Publix for eggs; Winn-Dixie covers both at a middle price.
    items = [item(1, "Milk", qty=2), item(2, "Eggs", qty=2)]
    rows = [prow(1, 1, "Winn-Dixie", 4.00, 0.02, "scrape"), prow(2, 1, "Winn-Dixie", 4.00, 0.02, "scrape"),
            prow(1, 2, "Walmart", 3.00, 0.03, "scrape"), prow(2, 2, "Walmart", 5.00, 0.03, "scrape"),
            prow(1, 3, "Publix", 5.00, 0.04, "scrape"), prow(2, 3, "Publix", 2.50, 0.04, "scrape")]
    free = build_compare(items, rows, [], HERE, 10, prefs(cost_per_mile=0.0, min_savings=1.0))
    # single stores: W-D 16, Walmart 16, Publix 15; best split = milk@Walmart (6) + eggs@Publix (5) = 11
    assert free["recs"]["fewest"]["total"] == 15.0 and free["recs"]["fewest"]["stops"] == 1
    assert free["recs"]["lowest"]["total"] == 11.0 and free["recs"]["lowest"]["stops"] == 2
    assert free["recs"]["value"]["stops"] == 2
    assert free["multi_verdict"]["worth"] and free["multi_verdict"]["gross"] == 4.0

    # make driving expensive: ~1.4 mi each way x $5/mi makes the extra stop a loss
    costly = build_compare(items, rows, [], HERE, 10, prefs(cost_per_mile=5.0, min_savings=1.0))
    assert costly["recs"]["lowest"]["stops"] == 1 and costly["recs"]["value"]["stops"] == 1
    assert not costly["multi_verdict"]["worth"] and "cancels it out" in costly["multi_verdict"]["text"]

    # a high minimum-savings threshold keeps "best value" at one store but not "lowest cost"
    picky = build_compare(items, rows, [], HERE, 10, prefs(cost_per_mile=0.0, min_savings=10.0))
    assert picky["recs"]["lowest"]["stops"] == 2 and picky["recs"]["value"]["stops"] == 1
    assert "below your" in picky["multi_verdict"]["text"]


def test_preferred_chains_and_budget():
    items = [item(1, "Milk")]
    rows = [prow(1, 1, "Winn-Dixie", 4.0, 0.02, "scrape"), prow(1, 2, "Publix", 3.0, 0.03, "scrape"),
            prow(1, 3, "Walmart", 2.0, 0.04, "scrape")]
    r = build_compare(items, rows, [], HERE, 10, prefs(preferred_chains=["winndixie", "publix"], budget=3.5))
    assert [c["key"] for c in r["chains"]] == ["winndixie", "publix"]
    assert r["recs"]["fewest"]["chains"] == ["publix"]
    assert r["budget"] == {"budget": 3.5, "total": 3.0, "over": -0.5}


def test_checked_items_are_left_out_and_empty_list_is_safe():
    rows = [prow(1, 1, "Publix", 3.0, 0.02, "scrape"), prow(2, 1, "Publix", 9.0, 0.02, "scrape")]
    r = build_compare([item(1, "A"), item(2, "B", checked=1)], rows, [], HERE, 10, prefs())
    assert r["singles"][0]["total"] == 3.0 and r["scored_count"] == 1
    empty = build_compare([], [], [], HERE, 10, prefs())
    assert empty["recs"] == {} and empty["items"] == []


def test_outside_radius_is_ignored():
    rows = [prow(1, 1, "Publix", 3.0, 1.0, "scrape")]         # about 69 miles away
    r = build_compare([item(1, "Milk")], rows, [], HERE, 10, prefs())
    assert r["unpriced"] and r["items"][0]["best"] is None


def test_compare_endpoint_with_sample_data(make_user):
    c, _ = make_user("32202")
    add(c, pid(c, "Milk"))
    add(c, pid(c, "Eggs"))
    d = c.get("/api/compare").get_json()
    assert [x["key"] for x in d["chains"]] == ["winndixie", "publix", "walmart"]
    assert d["flags"]["sample"]
    assert any("sample data" in n for n in d["notes"])
    assert any("No weekly ads" in n for n in d["notes"])
    assert d["recs"]["value"] and d["chosen"] == "value"
    for it in d["items"]:
        for v in it["prices"].values():
            assert v is None or v["quality"] in ("sample", "verified", "reported")


# ---------- deals and the provider layer ----------

def test_deals_empty_until_a_feed_is_loaded(make_user):
    c, _ = make_user()
    d = c.get("/api/deals").get_json()
    assert d["offers"] == [] and d["list_savings"] == {}
    live = [p for p in d["providers"] if p.get("chain")]
    assert len(live) == 3 and all(not p["connected"] for p in live)
    assert any(p["key"] == "offers" and not p["connected"] for p in d["providers"])


def test_admin_offer_import_validates_and_requires_token(make_user, monkeypatch):
    c, _ = make_user()
    body = {"offers": [{"chain": "publix", "kind": "sale", "title": "Weekly ad", "source": "Publix weekly ad"}]}
    assert c.post("/api/admin/offers", json=body).status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "t0ken")
    assert c.post("/api/admin/offers", json=body, headers={"X-Admin-Token": "wrong"}).status_code == 403
    ok = c.post("/api/admin/offers", json=body, headers={"X-Admin-Token": "t0ken"}).get_json()
    assert ok["saved"] == 1
    bad = c.post("/api/admin/offers", headers={"X-Admin-Token": "t0ken"}, json={"offers": [
        {"chain": "aldi", "kind": "sale", "title": "x", "source": "s"},
        {"chain": "publix", "kind": "magic", "title": "x", "source": "s"},
        {"chain": "publix", "kind": "sale", "title": "x", "source": "s", "product_id": 1}]}).get_json()
    assert bad["saved"] == 0 and len(bad["errors"]) == 3
    with get_conn() as conn:
        conn.execute("DELETE FROM offers")


def test_deals_show_list_savings_from_real_offers_only(make_user, monkeypatch):
    c, _ = make_user("32202")
    milk = pid(c, "Milk")
    add(c, milk, qty=2)
    with get_conn() as conn:
        store = conn.execute("INSERT INTO stores (name, chain, city, zip, lat, lng, source) VALUES "
                             "('Publix Test', 'Publix', 'Jacksonville', '32202', 30.3322, -81.6557, 'user')").lastrowid
        lid = conn.execute("INSERT INTO listings (product_id, store_id) VALUES (?, ?)", (milk, store)).lastrowid
        conn.execute("INSERT INTO prices (listing_id, price, source) VALUES (?, 4.00, 'scrape')", (lid,))
        conn.execute("INSERT INTO offers (chain, product_id, kind, title, sale_price, source, expires_on) "
                     "VALUES ('publix', ?, 'sale', 'Milk sale', 3.00, 'test ad', ?)",
                     (milk, (date.today() + timedelta(days=3)).isoformat()))
        conn.execute("INSERT INTO offers (chain, product_id, kind, title, sale_price, source, expires_on) "
                     "VALUES ('publix', ?, 'sale', 'Expired sale', 1.00, 'test ad', '2020-01-01')", (milk,))
    try:
        d = c.get("/api/deals").get_json()
        assert [o["title"] for o in d["offers"]] == ["Milk sale"]               # expired offers never show
        assert d["offers"][0]["on_list"] and d["offers"][0]["saves"] == 2.00     # ($4 - $3) x 2
        assert d["list_savings"] == {"publix": 2.0}
        cmp_ = c.get("/api/compare").get_json()
        pub = next(i for i in cmp_["items"] if i["product_id"] == milk)["prices"]["publix"]
        assert pub["offer"]["saves"] == 2.00 and pub["effective"] == 3.00
    finally:
        with get_conn() as conn:
            conn.execute("DELETE FROM offers")
            conn.execute("DELETE FROM stores WHERE id = ?", (store,))


# ---------- schema upgrade ----------

def test_migration_from_v2_adds_new_tables_and_columns(tmp_path, monkeypatch):
    path = str(tmp_path / "v2.db")
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE stores (id INTEGER PRIMARY KEY, name TEXT NOT NULL, chain TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '', zip TEXT, lat REAL, lng REAL,
            source TEXT NOT NULL DEFAULT 'user', created_by INTEGER, UNIQUE(name, zip, address));
        CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, category TEXT, unit TEXT);
        INSERT INTO products VALUES (1, 'Milk', 'Dairy', 'gal');
        PRAGMA user_version=2;
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    db.init_db()
    conn = sqlite3.connect(path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"brand", "package_size", "image_url"} <= cols
    assert {"purchases", "item_prefs", "user_prefs", "offers"} <= tables
    assert conn.execute("SELECT name FROM products").fetchone()[0] == "Milk"
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn.close()
