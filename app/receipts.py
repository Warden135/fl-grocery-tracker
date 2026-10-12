"""Turn a grocery receipt (photo or pasted text) into price reports.

Reading a photo needs a vision model: set ANTHROPIC_API_KEY on the server. Without it,
people can paste the receipt text instead (many store apps and e-receipts have it). Both
paths end in the same parser, and the shopper always reviews the result before anything
is saved. Nothing is guessed silently: a line that can't be matched to an item is left out.
"""
import os
import re

from . import brands

SKIP = re.compile(r"\b(sub\s*total|total|tax|change|cash|visa|master\s*card|amex|debit|credit|balance|"
                  r"savings|you saved|discount|coupon|card|tender|points|rewards|thank|approved|"
                  r"auth|ref|acct|account|items? sold|tel|phone|store|cashier|reg|manager)\b", re.I)
PRICE_END = re.compile(r"^(?P<name>.*?\S)[\s.\-*]+\$?(?P<price>\d{1,3}\.\d{2})\s*[A-Za-z]{0,2}\s*$")
MULTI = re.compile(r"(?P<qty>\d{1,2})\s*(?:@|x)\s*\$?(?P<unit>\d{1,3}\.\d{2})", re.I)
ABBREV = {"whl": "whole", "gal": "gallon", "lg": "large", "lrg": "large", "dz": "dozen", "doz": "dozen",
          "org": "organic", "chkn": "chicken", "chk": "chicken", "brst": "breast", "bttr": "butter",
          "bnls": "boneless", "bnl": "boneless", "grnd": "ground", "bf": "beef", "bnna": "bananas",
          "banana": "bananas", "oj": "orange juice", "jc": "juice", "cof": "coffee", "cfe": "coffee",
          "spag": "spaghetti", "tom": "tomatoes", "tomato": "tomatoes", "pk": "pack", "wht": "white",
          "bd": "bread", "brd": "bread", "eggs": "eggs", "egg": "eggs"}
STOP = {"the", "and", "of", "a", "lb", "oz", "ct", "pk", "pack", "f", "t", "fs", "ea"}


def tokens(text):
    out = []
    for t in re.findall(r"[a-z]+", text.lower()):
        t = ABBREV.get(t, t)
        out.extend(t.split())
    return [t for t in out if t not in STOP and len(t) > 1]


def parse_text(text):
    """-> {"chain": key|None, "lines": [{raw, name, price, qty}]}"""
    chain, lines = None, []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^store\s*:\s*(.+)$", line, re.I)
        if m:
            chain = brands.chain_key(m.group(1)) or chain
            continue
        if chain is None and brands.chain_key(line.split()[0] if line.split() else ""):
            chain = brands.chain_key(line.split()[0])
        if SKIP.search(line) or re.search(r"-\s*\$?\d+\.\d{2}\s*[A-Za-z]{0,2}$", line):
            continue
        qty, price, name = 1, None, None
        mm = MULTI.search(line)
        if mm:
            qty, price = int(mm.group("qty")), float(mm.group("unit"))
            name = re.sub(r"\s+", " ", (line[:mm.start()] + " " + re.sub(r"\$?\d{1,3}\.\d{2}\s*[A-Za-z]{0,2}\s*$", "", line[mm.end():])).strip(" .-*"))
        else:
            m = PRICE_END.match(line)
            if m:
                name, price = m.group("name").strip(), float(m.group("price"))
        if price is None or not 0.05 <= price <= 999 or not name or not re.search(r"[A-Za-z]{2}", name):
            continue
        name = re.sub(r"^\d{6,}\s*", "", name)          # leading item codes
        lines.append({"raw": line, "name": name, "price": round(price, 2), "qty": max(1, qty)})
    return {"chain": chain, "lines": lines[:80]}


def match(name, products, limit=3):
    """Best catalog items for a receipt line: [(score, product)] with score in 0..1."""
    rt = set(tokens(name))
    if not rt:
        return []
    scored = []
    for p in products:
        pt = [t for t in tokens(p["name"] + " " + (p.get("brand") or "")) if not t.isdigit()]
        if not pt:
            continue
        hit = sum(1 for t in set(pt) if t in rt or any(len(r) >= 3 and (t.startswith(r) or r.startswith(t)) for r in rt))
        extra = len([r for r in rt if r not in pt and not any(len(r) >= 3 and (t.startswith(r) or r.startswith(t)) for t in pt)])
        s = hit / len(set(pt)) - 0.05 * extra
        if hit and s >= 0.34:
            scored.append((round(min(1.0, s), 2), p))
    scored.sort(key=lambda x: (-x[0], x[1]["name"]))
    return scored[:limit]


class NotConfigured(Exception):
    pass


PROMPT = ("Transcribe this grocery receipt. First line: 'STORE: <store name>' if visible. Then one line per "
          "purchased item as '<item name as printed>  <price>' using the price for one unit (write 'N @ price' "
          "when a quantity is shown). Skip subtotal, tax, total, payment, discounts, coupons and loyalty lines. "
          "Output plain text only.")


def read_image(b64, mime):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise NotConfigured("Photo reading isn't set up on this server.")
    import requests
    r = requests.post(
        "https://api.anthropic.com/v1/messages", timeout=60,
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": os.environ.get("RECEIPT_MODEL", "claude-sonnet-5-5"), "max_tokens": 1500,
              "messages": [{"role": "user", "content": [
                  {"type": "image", "source": {"type": "base64", "media_type": mime, "data": b64}},
                  {"type": "text", "text": PROMPT}]}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
