"""Where prices and offers come from, and how much to trust them.

Nothing here invents data. Prices come from the `prices` table (shopper reports,
the optional scraper, or clearly marked sample data) and offers come from the
`offers` table, which stays empty until a real feed fills it, either through the
admin import endpoint or a provider you add below.

To connect a retailer feed later: write a class with `key`, `label`, `chains` and a
`fetch(zip5)` method that returns a list of offer dicts (see `OFFER_FIELDS`), then
add it to PROVIDERS and call `import_offers()` on a schedule. Until you do, the app
reports the chain as "not connected" instead of showing made-up deals.
"""
import os
from datetime import date

from .db import get_conn

OFFER_FIELDS = ("chain", "product_id", "kind", "title", "sale_price", "amount_off", "min_spend",
                "max_qty", "requires_account", "conditions", "starts_on", "expires_on",
                "source", "source_url")
OFFER_KINDS = ("sale", "coupon", "loyalty")
STALE_DAYS = 14


def price_quality(row):
    """sample = demo data; verified = from a store site or confirmed by another shopper;
    reported = a single unconfirmed shopper report."""
    if row.get("store_source") == "demo":
        return "sample"
    if row.get("source") == "apify":
        return "online"
    if row.get("source") == "scrape" or (row.get("verified_count") or 0) > 0:
        return "verified"
    return "reported"


QUALITY_TEXT = {
    "verified": "Verified",
    "online": "Online price from the store's website",
    "reported": "Shopper report, not yet confirmed",
    "sample": "Sample data",
}


class NotConnected:
    """Placeholder for a retailer feed that has not been wired up."""

    def __init__(self, key, label):
        self.key, self.label = key, label

    def status(self):
        return {"key": self.key, "label": self.label, "connected": False,
                "detail": "No official price or ad feed is connected. Retailers do not offer a public "
                          "one, so prices here come from shoppers until a feed is added."}


def _counts():
    with get_conn() as c:
        scraped = c.execute("SELECT COUNT(*) FROM prices WHERE source = 'scrape'").fetchone()[0]
        shopper = c.execute("SELECT COUNT(*) FROM prices WHERE source = 'user' AND user_id IN "
                            "(SELECT id FROM users WHERE email NOT LIKE '%@example.invalid')").fetchone()[0]
        offers = c.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    return scraped, shopper, offers


def status():
    scraped, shopper, offers = _counts()
    out = [
        {"key": "shoppers", "label": "Shopper reports", "connected": True,
         "detail": f"{shopper} price report{'s' if shopper != 1 else ''} from shoppers. Reports show as "
                   "verified once another shopper confirms them."},
        {"key": "scraper", "label": "Store websites (experimental)", "connected": scraped > 0,
         "detail": f"{scraped} price{'s' if scraped != 1 else ''} collected." if scraped
         else "No prices collected yet. The scraper has to be configured per store page."},
        {"key": "apify", "label": "Walmart.com prices (Apify)", "connected": bool(os.environ.get("APIFY_TOKEN")),
         "detail": ("Connected. Prices are for your ZIP as shown on walmart.com, for items you confirmed under Me."
                    if os.environ.get("APIFY_TOKEN") else "Not set up. Needs an APIFY_TOKEN on the server.")},
        {"key": "offers", "label": "Weekly ads and coupons", "connected": offers > 0,
         "detail": f"{offers} offer{'s' if offers != 1 else ''} loaded from an imported feed." if offers
         else "No offers loaded. They appear here once a feed is imported."},
    ]
    for key, label in (("winndixie", "Winn-Dixie"), ("publix", "Publix"), ("walmart", "Walmart")):
        out.append({**NotConnected(key, f"{label} live feed").status(), "chain": key})
    return out


def active_offers(conn, today=None):
    today = (today or date.today()).isoformat()
    cur = conn.execute(
        "SELECT o.*, p.name AS product FROM offers o LEFT JOIN products p ON p.id = o.product_id "
        "WHERE (o.expires_on IS NULL OR o.expires_on >= ?) AND (o.starts_on IS NULL OR o.starts_on <= ?) "
        "ORDER BY o.expires_on IS NULL, o.expires_on", (today, today))
    return [dict(r) for r in cur.fetchall()]


def import_offers(items, replace_source=None):
    """Validate and store offers from a feed. Returns (saved, errors)."""
    saved, errors = 0, []
    with get_conn() as c:
        if replace_source:
            c.execute("DELETE FROM offers WHERE source = ?", (replace_source,))
        for i, o in enumerate(items):
            from .brands import BY_KEY
            problems = []
            if o.get("chain") not in BY_KEY:
                problems.append("chain must be winndixie, publix or walmart")
            if o.get("kind") not in OFFER_KINDS:
                problems.append("kind must be sale, coupon or loyalty")
            if not (o.get("title") or "").strip():
                problems.append("title is required")
            if not (o.get("source") or "").strip():
                problems.append("source is required (where the offer was published)")
            if o.get("product_id") is not None and o.get("sale_price") is None and o.get("amount_off") is None:
                problems.append("an offer for a product needs sale_price or amount_off")
            if problems:
                errors.append({"index": i, "problems": problems})
                continue
            o = {**o, "requires_account": 1 if o.get("requires_account") else 0,
                 "conditions": o.get("conditions") or ""}
            vals = [o.get(f) for f in OFFER_FIELDS]
            c.execute(f"INSERT INTO offers ({', '.join(OFFER_FIELDS)}) VALUES ({', '.join('?' * len(OFFER_FIELDS))})", vals)
            saved += 1
    return saved, errors
