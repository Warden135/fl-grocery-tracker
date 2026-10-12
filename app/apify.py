"""Walmart.com prices through Apify (https://apify.com), a paid scraping platform.

How it works, and why it is built this way:
- Prices are for the shopper's ZIP as shown on walmart.com. They are stored against an
  "online" pseudo-store at the ZIP's center, labeled "Online price", never as a shelf price.
- Search results can be a different brand or size, so a search only produces *suggested*
  matches. A price is saved only after a person confirms the match (equal brand and size).
- Needs APIFY_TOKEN. The actor defaults to crawloop/walmart-scraper (override with
  APIFY_WALMART_ACTOR). It was written against that actor's documented input and output;
  it has not been run against the live service. Apify bills per run, so syncs are limited.
"""
import os
import re
import threading
from datetime import datetime

from . import receipts
from .db import get_conn

BASE = "https://api.apify.com/v2"
CHAIN = "walmart"
MAX_KEYWORD_SEARCHES = 6      # per sync, to keep the bill small
RESULTS_PER_SEARCH = 6
_lock = threading.Lock()


class ApifyError(Exception):
    pass


def configured():
    return bool(os.environ.get("APIFY_TOKEN"))


def actor_id():
    return os.environ.get("APIFY_WALMART_ACTOR", "crawloop/walmart-scraper").replace("/", "~")


def run_actor(payload):
    """Run the actor and wait for its results (Apify's run-sync endpoint). Returns a list of items."""
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise ApifyError("APIFY_TOKEN isn't set on the server.")
    import requests
    try:
        r = requests.post(f"{BASE}/acts/{actor_id()}/run-sync-get-dataset-items",
                          headers={"Authorization": f"Bearer {token}"},
                          params={"timeout": 240, "clean": "1"}, json=payload, timeout=270)
    except requests.RequestException:
        raise ApifyError("Couldn't reach Apify.")
    if r.status_code in (401, 403):
        raise ApifyError("Apify rejected the token.")
    if r.status_code == 402:
        raise ApifyError("Apify says the account is out of credit.")
    if r.status_code >= 400:
        raise ApifyError(f"Apify returned an error ({r.status_code}).")
    data = r.json()
    return data if isinstance(data, list) else []


def parse_items(items):
    """Keep priced product rows. Error rows and rows without an id, title or price are dropped."""
    out = []
    for it in items or []:
        if not isinstance(it, dict) or it.get("recordType") == "error" or it.get("error"):
            continue
        try:
            price = round(float(it.get("price")), 2)
        except (TypeError, ValueError):
            continue
        ext = str(it.get("usItemId") or it.get("itemId") or "").strip()
        title = str(it.get("title") or "").strip()
        if not ext or not title or not 0.05 <= price <= 999:
            continue
        out.append({"external_id": ext, "title": title[:200], "brand": (it.get("brand") or None),
                    "price": price, "url": it.get("url") or None})
    return out


def keyword_for(product_name):
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 ]+", " ", product_name)).strip()


def online_store(c, zip5, lat, lng):
    """The pseudo-store that holds this ZIP's online prices."""
    name = f"Walmart.com prices (ZIP {zip5})"
    c.execute("INSERT OR IGNORE INTO stores (name, chain, city, address, zip, lat, lng, source) "
              "VALUES (?, 'Walmart', '', 'online', ?, ?, ?, 'apify')", (name, zip5, lat, lng))
    return c.execute("SELECT id FROM stores WHERE name = ? AND zip = ? AND address = 'online'",
                     (name, zip5)).fetchone()["id"]


def save_price(c, store_id, product_id, price, seen_at=None):
    """Store an online price unless the same price was already saved in the last 6 hours."""
    c.execute("INSERT OR IGNORE INTO listings (product_id, store_id) VALUES (?, ?)", (product_id, store_id))
    lid = c.execute("SELECT id FROM listings WHERE product_id = ? AND store_id = ?",
                    (product_id, store_id)).fetchone()["id"]
    last = c.execute("SELECT price, scraped_at FROM prices WHERE listing_id = ? ORDER BY scraped_at DESC, id DESC LIMIT 1",
                     (lid,)).fetchone()
    if last and last["price"] == price and c.execute(
            "SELECT ? >= datetime('now', '-6 hours')", (last["scraped_at"],)).fetchone()[0]:
        return False
    if seen_at:
        c.execute("INSERT INTO prices (listing_id, price, source, scraped_at) VALUES (?, ?, 'apify', ?)", (lid, price, seen_at))
    else:
        c.execute("INSERT INTO prices (listing_id, price, source) VALUES (?, ?, 'apify')", (lid, price))
    return True


def sync(zip5, lat, lng, product_ids, runner=None):
    """Refresh prices for confirmed links and look for matches for the rest.
    Returns (prices_saved, suggestions_added)."""
    run = runner or run_actor
    saved = suggested = 0
    with get_conn() as c:
        products = {r["id"]: dict(r) for r in c.execute(
            f"SELECT id, name FROM products WHERE id IN ({','.join('?' * len(product_ids))})", list(product_ids))} \
            if product_ids else {}
        links = [dict(r) for r in c.execute(
            "SELECT * FROM product_links WHERE chain = ? AND status = 'confirmed' AND product_id IN (%s)"
            % ",".join("?" * len(products)), [CHAIN, *products]) ] if products else []
        have_any = {r["product_id"] for r in c.execute(
            "SELECT product_id FROM product_links WHERE chain = ? AND status IN ('confirmed', 'suggested')", (CHAIN,))}
        rejected = {(r["product_id"], r["external_id"]) for r in c.execute(
            "SELECT product_id, external_id FROM product_links WHERE chain = ? AND status = 'rejected'", (CHAIN,))}
    # 1. exact items the shopper confirmed: one run for all of them
    if links:
        items = parse_items(run({"zip": zip5, "itemIds": sorted({l["external_id"] for l in links}),
                                 "scrapeMode": "overview", "maxItems": len(links) + 5}))
        by_ext = {i["external_id"]: i for i in items}
        with get_conn() as c:
            store = online_store(c, zip5, lat, lng)
            for l in links:
                it = by_ext.get(l["external_id"])
                if not it:
                    continue
                saved += save_price(c, store, l["product_id"], it["price"])
                c.execute("UPDATE product_links SET price_seen = ?, seen_zip = ?, seen_at = datetime('now') WHERE id = ?",
                          (it["price"], zip5, l["id"]))
    # 2. items with no match yet: one small search each, results become suggestions only
    todo = [p for pid, p in products.items() if pid not in have_any][:MAX_KEYWORD_SEARCHES]
    for p in todo:
        items = parse_items(run({"zip": zip5, "keywords": [keyword_for(p["name"])], "scrapeMode": "overview",
                                 "maxItems": RESULTS_PER_SEARCH, "maxPages": 1}))
        cands = []
        for it in items:
            if (p["id"], it["external_id"]) in rejected:
                continue
            m = receipts.match(it["title"], [{"id": p["id"], "name": p["name"], "brand": None}], limit=1)
            if m:
                cands.append((m[0][0], it))
        cands.sort(key=lambda x: -x[0])
        with get_conn() as c:
            for score, it in cands[:3]:
                cur = c.execute(
                    "INSERT OR IGNORE INTO product_links (product_id, chain, external_id, title, brand, url, price_seen, "
                    "seen_zip, seen_at, score, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, 'suggested')",
                    (p["id"], CHAIN, it["external_id"], it["title"], it["brand"], it["url"], it["price"], zip5, score))
                suggested += cur.rowcount
    return saved, suggested


def confirm_link(c, link_id, user_zip, lat, lng):
    """Confirm a suggested match and save its price right away (it was fetched for this ZIP)."""
    l = c.execute("SELECT * FROM product_links WHERE id = ?", (link_id,)).fetchone()
    if not l:
        return None
    c.execute("UPDATE product_links SET status = 'confirmed' WHERE id = ?", (link_id,))
    c.execute("UPDATE product_links SET status = 'rejected' WHERE product_id = ? AND chain = ? AND id != ? "
              "AND status = 'suggested'", (l["product_id"], l["chain"], link_id))
    if l["price_seen"] and l["seen_zip"] == user_zip:
        save_price(c, online_store(c, user_zip, lat, lng), l["product_id"], l["price_seen"], l["seen_at"])
    return dict(l)


def start(zip5, lat, lng, product_ids):
    """Run a sync in the background (Apify runs take a while). Returns the sync_log id, or None if one is running."""
    if not _lock.acquire(blocking=False):
        return None
    with get_conn() as c:
        log_id = c.execute("INSERT INTO sync_log (chain, zip, state) VALUES (?, ?, 'running')", (CHAIN, zip5)).lastrowid

    def work():
        try:
            saved, sug = sync(zip5, lat, lng, product_ids)
            state, msg = "done", ""
        except ApifyError as e:
            saved = sug = 0
            state, msg = "error", str(e)
        except Exception:
            saved = sug = 0
            state, msg = "error", "Something went wrong during the sync."
        finally:
            with get_conn() as c:
                c.execute("UPDATE sync_log SET state = ?, message = ?, prices = ?, suggestions = ?, "
                          "finished_at = datetime('now') WHERE id = ?", (state, msg, saved, sug, log_id))
            _lock.release()
    threading.Thread(target=work, daemon=True).start()
    return log_id
