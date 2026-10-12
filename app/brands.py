"""The three supported grocery chains and how they are shown.

Colors approximate each brand's primary color. This module does NOT ship the
chains' logos: trademarked artwork has to come from the owners. To show an official
logo, drop a file named after the chain key into static/brands/ (winndixie.svg,
publix.svg or walmart.svg; .png and .webp also work). Until then the app shows the
chain's name in its brand color, which is clearly labeled text, not a fake logo.
"""
import os
import re

STATIC_BRANDS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static", "brands"))

BRANDS = [
    {"key": "winndixie", "name": "Winn-Dixie", "color": "#D32027", "ink": "#FFFFFF"},
    {"key": "publix", "name": "Publix", "color": "#2F7D2A", "ink": "#FFFFFF"},
    {"key": "walmart", "name": "Walmart", "color": "#0071CE", "ink": "#FFFFFF"},
]
KEYS = [b["key"] for b in BRANDS]
BY_KEY = {b["key"]: b for b in BRANDS}


def chain_key(chain):
    """'Winn-Dixie' -> 'winndixie'. Returns None for chains we don't compare."""
    flat = re.sub(r"[^a-z]", "", (chain or "").lower())
    for k in KEYS:
        if flat.startswith(k):
            return k
    return None


def logo_url(key):
    for ext in ("svg", "png", "webp"):
        if os.path.exists(os.path.join(STATIC_BRANDS, f"{key}.{ext}")):
            return f"/static/brands/{key}.{ext}"
    return None


def brands_json():
    return [{**b, "logo": logo_url(b["key"])} for b in BRANDS]
