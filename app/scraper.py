"""Polite price scraper.

For each listing that has a URL it:
  1. checks robots.txt and skips disallowed pages,
  2. fetches the page with an identifying User-Agent,
  3. extracts a price from the listing's CSS selector, falling back to
     schema.org JSON-LD "offers.price" and then <meta> price tags,
  4. stores it in the prices table.

Notes: many grocery sites render prices with JavaScript or block bots. For those,
this plain-HTTP approach will return nothing; you would need a headless browser,
an official API, or user-submitted prices. Check each store's terms of service.
"""
import json
import re
import time
from urllib import robotparser
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from .db import get_conn

USER_AGENT = "FLGroceryTracker/0.1 (personal price tracker)"
REQUEST_DELAY_SECONDS = 3.0
_robots_cache = {}


def allowed_by_robots(url):
    parts = urlparse(url)
    base = f"{parts.scheme}://{parts.netloc}"
    if base not in _robots_cache:
        rp = robotparser.RobotFileParser()
        try:
            resp = requests.get(base + "/robots.txt", timeout=10,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
            else:
                rp.parse([])  # no robots.txt -> allowed
        except requests.RequestException:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    if rp is None:
        return False  # can't verify, so be conservative
    return rp.can_fetch(USER_AGENT, url)


def parse_price(text):
    if text is None:
        return None
    m = re.search(r"\d[\d,]*\.?\d*", str(text).replace("$", ""))
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return value if 0 < value < 10000 else None


def _walk_jsonld(node):
    if isinstance(node, dict):
        offers = node.get("offers")
        if offers is not None:
            for o in offers if isinstance(offers, list) else [offers]:
                if isinstance(o, dict):
                    price = parse_price(o.get("price") or o.get("lowPrice"))
                    if price:
                        return price
        for v in node.values():
            found = _walk_jsonld(v)
            if found:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _walk_jsonld(v)
            if found:
                return found
    return None


def extract_price(html, selector=None):
    soup = BeautifulSoup(html, "html.parser")
    if selector:
        el = soup.select_one(selector)
        if el:
            price = parse_price(el.get("content") or el.get_text(" ", strip=True))
            if price:
                return price
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        price = _walk_jsonld(data)
        if price:
            return price
    for prop in ("product:price:amount", "og:price:amount"):
        meta = soup.find("meta", attrs={"property": prop})
        if meta and meta.get("content"):
            price = parse_price(meta["content"])
            if price:
                return price
    return None


def scrape_all(delay=REQUEST_DELAY_SECONDS):
    """Scrape every listing that has a URL. Returns a summary dict."""
    summary = {"ok": 0, "skipped": 0, "failed": 0, "details": []}
    with get_conn() as conn:
        listings = conn.execute(
            "SELECT id, url, selector FROM listings WHERE url IS NOT NULL AND url != ''"
        ).fetchall()
        for row in listings:
            url = row["url"]
            if not allowed_by_robots(url):
                summary["skipped"] += 1
                summary["details"].append({"listing_id": row["id"], "status": "blocked by robots.txt"})
                continue
            try:
                resp = requests.get(url, timeout=20, headers={"User-Agent": USER_AGENT})
                resp.raise_for_status()
                price = extract_price(resp.text, row["selector"])
            except requests.RequestException as exc:
                summary["failed"] += 1
                summary["details"].append({"listing_id": row["id"], "status": f"error: {exc}"})
                time.sleep(delay)
                continue
            if price is None:
                summary["failed"] += 1
                summary["details"].append({"listing_id": row["id"], "status": "no price found"})
            else:
                conn.execute(
                    "INSERT INTO prices (listing_id, price, source) VALUES (?, ?, 'scrape')",
                    (row["id"], price),
                )
                summary["ok"] += 1
            time.sleep(delay)
    return summary


if __name__ == "__main__":
    print(json.dumps(scrape_all(), indent=2))
