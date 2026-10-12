"""Import real supermarket locations from OpenStreetMap (free, no API key).

    python -m scripts.import_osm_stores 32202 --miles 15
    python -m scripts.import_osm_stores 33101 --miles 10 --dry-run

Needs internet access. Be kind to the free Overpass service: run it for a few ZIP
codes, not hundreds. Store data (c) OpenStreetMap contributors, ODbL license.
"""
import argparse
import sys

import requests

from app import geo
from app.db import get_conn, init_db

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def build_query(lat, lng, meters):
    around = f"(around:{int(meters)},{lat},{lng})"
    return (f'[out:json][timeout:60];(node["shop"="supermarket"]{around};'
            f'way["shop"="supermarket"]{around};);out center tags;')


def parse_elements(data):
    """Turn an Overpass response into store dicts, skipping unnamed or location-less entries."""
    stores = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = (tags.get("name") or tags.get("brand") or "").strip()
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lng = el.get("lon", (el.get("center") or {}).get("lon"))
        if not name or lat is None or lng is None:
            continue
        street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x)
        stores.append({
            "name": name[:80], "chain": (tags.get("brand") or name).strip()[:80],
            "address": street[:120], "zip": (tags.get("addr:postcode") or "")[:5] or None,
            "city": (tags.get("addr:city") or "")[:60], "lat": float(lat), "lng": float(lng),
        })
    return stores


def save(stores):
    """Insert stores that aren't already saved (same name within about 50 m). Returns count added."""
    init_db()
    added = 0
    with get_conn() as c:
        for s in stores:
            dup = c.execute(
                "SELECT 1 FROM stores WHERE lower(name) = lower(?) AND abs(lat - ?) < 0.0005 "
                "AND abs(lng - ?) < 0.0005", (s["name"], s["lat"], s["lng"])).fetchone()
            if dup:
                continue
            c.execute("INSERT INTO stores (name, chain, city, address, zip, lat, lng, source) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?, 'osm')",
                      (s["name"], s["chain"], s["city"], s["address"], s["zip"], s["lat"], s["lng"]))
            added += 1
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("zip", help="5-digit ZIP code at the center of the area")
    ap.add_argument("--miles", type=float, default=10, help="radius in miles (default 10)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be added")
    args = ap.parse_args(argv)
    try:
        center = geo.lookup_zip(args.zip)
    except (geo.ZipNotFound, geo.GeoUnavailable):
        sys.exit(f"Couldn't look up ZIP {args.zip}.")
    resp = requests.post(OVERPASS_URL, data={"data": build_query(center["lat"], center["lng"], args.miles * 1609.34)},
                         headers={"User-Agent": geo.USER_AGENT}, timeout=90)
    resp.raise_for_status()
    stores = parse_elements(resp.json())
    if args.dry_run:
        for s in stores:
            print(f"{s['name']:<40} {s['address']}")
        print(f"{len(stores)} supermarkets found.")
        return
    print(f"Found {len(stores)} supermarkets, added {save(stores)} new ones.")


if __name__ == "__main__":
    main()
