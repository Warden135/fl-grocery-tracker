"""ZIP code to coordinates, and distances.

ZIP lookups use the free Zippopotam.us API the first time a ZIP is seen and are
cached in the zip_cache table afterwards. If the lookup service can't be reached,
a small built-in table of ZIP centers (approximate) covers the demo areas.
Set GEOCODER=off to skip network lookups (used by the tests).
"""
import math
import os

import requests

from .db import get_conn

USER_AGENT = "ShelfScout/0.2 (personal price tracker)"


class ZipNotFound(Exception):
    pass


class GeoUnavailable(Exception):
    pass


# Approximate ZIP centers, only used when the online lookup is unavailable.
OFFLINE_ZIPS = {
    "32202": (30.3322, -81.6557, "Jacksonville", "FL"),
    "32207": (30.3030, -81.6370, "Jacksonville", "FL"),
    "32246": (30.2680, -81.5250, "Jacksonville", "FL"),
    "32250": (30.2870, -81.4000, "Jacksonville Beach", "FL"),
    "33101": (25.7743, -80.1937, "Miami", "FL"),
    "33133": (25.7380, -80.2480, "Miami", "FL"),
    "33139": (25.7900, -80.1340, "Miami Beach", "FL"),
    "33180": (25.9560, -80.1410, "Aventura", "FL"),
    "32801": (28.5420, -81.3790, "Orlando", "FL"),
    "32803": (28.5620, -81.3510, "Orlando", "FL"),
    "32819": (28.4520, -81.4710, "Orlando", "FL"),
    "33602": (27.9500, -82.4600, "Tampa", "FL"),
    "33609": (27.9430, -82.5040, "Tampa", "FL"),
    "33647": (28.1260, -82.3500, "Tampa", "FL"),
}


def haversine_miles(lat1, lng1, lat2, lng2):
    r = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def lookup_zip(zip5):
    """Return {'zip','lat','lng','city','state'} or raise ZipNotFound / GeoUnavailable."""
    with get_conn() as c:
        row = c.execute("SELECT * FROM zip_cache WHERE zip = ?", (zip5,)).fetchone()
        if row:
            return dict(row)

    if os.environ.get("GEOCODER", "online") != "off":
        try:
            resp = requests.get(f"https://api.zippopotam.us/us/{zip5}", timeout=5,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code == 404:
                raise ZipNotFound(zip5)
            resp.raise_for_status()
            place = resp.json()["places"][0]
            loc = {"zip": zip5, "lat": float(place["latitude"]), "lng": float(place["longitude"]),
                   "city": place.get("place name", ""), "state": place.get("state abbreviation", "")}
            with get_conn() as c:
                c.execute("INSERT OR REPLACE INTO zip_cache (zip, lat, lng, city, state) "
                          "VALUES (:zip, :lat, :lng, :city, :state)", loc)
            return loc
        except ZipNotFound:
            raise
        except (requests.RequestException, KeyError, IndexError, ValueError):
            pass  # fall through to the offline table

    if zip5 in OFFLINE_ZIPS:
        lat, lng, city, state = OFFLINE_ZIPS[zip5]
        return {"zip": zip5, "lat": lat, "lng": lng, "city": city, "state": state}
    raise GeoUnavailable(zip5)
