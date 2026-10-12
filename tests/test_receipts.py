"""Receipt reading and sharing a price when an item is checked off."""
from app import receipts
from app.db import get_conn
from tests.conftest import post

RECEIPT = """PUBLIX Super Markets
MILK WHL GAL            3.49 F
EGGS LG 12CT            2.99
2 @ 1.65
BANANAS                 0.65
BTTR UNSLT 1LB          4.89
SUBTOTAL               12.02
TAX                     0.00
COUPON                 -0.50
TOTAL                  12.02
VISA ****1234          12.02
"""


def _catalog(c):
    return c.get("/api/products?q=").get_json()


def test_parse_skips_totals_and_discounts():
    r = receipts.parse_text(RECEIPT)
    assert r["chain"] == "publix"
    names = [l["name"] for l in r["lines"]]
    assert names == ["MILK WHL GAL", "EGGS LG 12CT", "BANANAS", "BTTR UNSLT 1LB"]
    assert [l["price"] for l in r["lines"]] == [3.49, 2.99, 0.65, 4.89]


def test_parse_quantity_and_store_line():
    r = receipts.parse_text("STORE: Walmart\nBANANAS 2 @ 0.65\nOUT OF RANGE 5000.00\nno price here\n")
    assert r["chain"] == "walmart"
    assert r["lines"][0]["qty"] == 2 and r["lines"][0]["price"] == 0.65 and len(r["lines"]) == 1


def test_matching_expands_abbreviations(make_user):
    c, _ = make_user()
    cat = _catalog(c)
    top = lambda name: receipts.match(name, cat)[0][1]["name"]
    assert top("MILK WHL GAL") == "Milk, whole, 1 gal"
    assert top("EGGS LG 12CT") == "Eggs, large, 1 dozen"
    assert top("BTTR UNSLT 1LB") == "Butter, 1 lb"
    assert receipts.match("PAPER TOWELS", cat) == []          # no guess when nothing fits


def test_read_text_returns_candidates_and_saves_nothing(make_user):
    c, _ = make_user()
    d = post(c, "/api/receipts/read", text=RECEIPT).get_json()
    assert d["chain"] == "publix" and len(d["lines"]) == 4
    assert d["lines"][0]["candidates"][0]["name"] == "Milk, whole, 1 gal"
    assert c.get("/api/purchases").get_json()["total"] == 0


def test_photo_needs_api_key_and_empty_input_rejected(make_user, monkeypatch):
    c, _ = make_user()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = post(c, "/api/receipts/read", image="AAAA", mime="image/jpeg")
    assert r.status_code == 503 and "Paste the receipt text" in r.get_json()["error"]
    assert post(c, "/api/receipts/read", text="  ").status_code == 400
    assert post(c, "/api/receipts/read", image="AAAA", mime="text/html").status_code == 400


def test_photo_path_uses_reader_then_same_parser(make_user, monkeypatch):
    c, _ = make_user()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(receipts, "read_image", lambda b64, mime: "STORE: Winn-Dixie\nMILK WHL GAL 3.79\n")
    big = "A" * 300_000                                      # a normal resized photo is a few hundred KB
    d = post(c, "/api/receipts/read", image=big, mime="image/jpeg").get_json()
    assert d["chain"] == "winndixie" and d["lines"][0]["price"] == 3.79 and d["ai"]
    monkeypatch.setattr(receipts, "read_image", lambda b64, mime: (_ for _ in ()).throw(RuntimeError("boom")))
    assert post(c, "/api/receipts/read", image="AAAA", mime="image/jpeg").status_code == 502


def _real_store(chain="Publix"):
    with get_conn() as conn:
        return conn.execute("INSERT INTO stores (name, chain, city, zip, lat, lng, source) VALUES "
                            "(?, ?, 'Jacksonville', '32202', 30.3322, -81.6557, 'user')",
                            (f"{chain} Receipt Test", chain)).lastrowid


def test_submit_saves_prices_history_and_clears_list(make_user):
    c, _ = make_user("32202")
    sid = _real_store()
    cat = {p["name"]: p["id"] for p in _catalog(c)}
    milk, eggs = cat["Milk, whole, 1 gal"], cat["Eggs, large, 1 dozen"]
    post(c, "/api/list", product_id=milk)
    r = post(c, "/api/receipts/submit", store_id=sid,
             items=[{"product_id": milk, "price": 3.49}, {"product_id": eggs, "price": 2.99, "qty": 2},
                    {"product_id": milk, "price": 9.99}, {"product_id": 99999, "price": 1.0},
                    {"product_id": eggs, "price": "abc"}]).get_json()
    assert r["saved"] == 2 and r["purchases"] == 2 and r["points"] == 10   # dup, unknown item, bad price skipped
    assert c.get("/api/list").get_json() == []                               # bought item leaves the list
    hist = c.get("/api/purchases").get_json()["purchases"]
    assert {h["name"] for h in hist} == {"Milk, whole, 1 gal", "Eggs, large, 1 dozen"}
    assert all(h["chain_name"] == "Publix" for h in hist)
    prices = c.get(f"/api/products/{milk}/prices").get_json()["options"]
    assert any(o["store"] == "Publix Receipt Test" and o["price"] == 3.49 for o in prices)
    # the same receipt twice does not double-record purchases
    again = post(c, "/api/receipts/submit", store_id=sid, items=[{"product_id": milk, "price": 3.49}]).get_json()
    assert again["purchases"] == 0
    assert post(c, "/api/receipts/submit", store_id=999999, items=[{"product_id": milk, "price": 1}]).status_code == 404
    assert post(c, "/api/receipts/submit", store_id=sid, items=[]).status_code == 400


def test_checking_off_with_price_shares_it_with_the_nearest_real_store(make_user):
    c, _ = make_user("32202")
    sid = _real_store("Walmart")
    milk = c.get("/api/products?q=Milk").get_json()[0]["id"]
    post(c, "/api/list", product_id=milk)
    item = c.get("/api/list").get_json()[0]["id"]
    r = post(c, f"/api/list/{item}/remove", bought=True, chain="walmart", paid=3.29).get_json()
    assert r["price_shared"]["store"] == "Walmart Receipt Test" and r["price_shared"]["points"] == 5
    with get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM prices p JOIN listings l ON l.id = p.listing_id "
                            "WHERE l.store_id = ? AND p.price = 3.29", (sid,)).fetchone()[0] == 1


def test_no_price_shared_without_chain_price_or_real_store(make_user):
    c, _ = make_user("32202")
    milk = _catalog(c)[0]["id"]
    for kw in ({}, {"chain": "publix"}, {"paid": 3.0}):
        post(c, "/api/list", product_id=milk)
        item = c.get("/api/list").get_json()[0]["id"]
        assert post(c, f"/api/list/{item}/remove", bought=True, **kw).get_json()["price_shared"] is None
    post(c, "/api/list", product_id=milk)
    item = c.get("/api/list").get_json()[0]["id"]
    # Winn-Dixie has only sample stores near this ZIP, and sample stores never take reports
    assert post(c, f"/api/list/{item}/remove", bought=True, chain="winndixie", paid=3.0).get_json()["price_shared"] is None
