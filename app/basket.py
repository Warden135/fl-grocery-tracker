"""Shopping-list pricing: totals per store, cheapest store per item, and the
best 1, 2 or 3 stop plans for a whole list within a radius."""
from itertools import combinations

from .geo import haversine_miles
from .scoring import age_days

MAX_CANDIDATE_STORES = 25   # keeps the stop-plan search small (C(25,3) = 2300)


def _line(item, row, dist, now):
    return {
        "product_id": item["product_id"], "name": item["name"], "qty": item["qty"],
        "price": row["price"], "age_days": round(age_days(row["seen_at"], now) or 0, 1),
        "price_id": row["price_id"], "verified": row["verified_count"],
    }


def build_basket(items, price_rows, center, radius, max_stops=3, now=None):
    """items: [{product_id, name, qty}], price_rows: LATEST_PRICES_SQL rows,
    center: (lat, lng), radius: miles."""
    stores = {}
    for r in price_rows:
        if r["lat"] is None or r["lng"] is None:
            continue
        d = haversine_miles(center[0], center[1], r["lat"], r["lng"])
        if d > radius:
            continue
        s = stores.setdefault(r["store_id"], {
            "store_id": r["store_id"], "store": r["store"], "chain": r["chain"],
            "distance": round(d, 1), "sample": r["store_source"] == "demo", "prices": {}})
        s["prices"][r["product_id"]] = r

    # Items that no nearby store has a price for would make every store look "incomplete",
    # so they are set aside and reported separately.
    priced_ids = {pid for s in stores.values() for pid in s["prices"]}
    unpriced = [{"product_id": it["product_id"], "name": it["name"]}
                for it in items if it["product_id"] not in priced_ids]
    all_items = items
    items = [it for it in items if it["product_id"] in priced_ids]

    out_stores = []
    for s in stores.values():
        total, missing, lines = 0.0, 0, []
        for it in items:
            row = s["prices"].get(it["product_id"])
            if row is None:
                missing += 1
                lines.append({"product_id": it["product_id"], "name": it["name"],
                              "qty": it["qty"], "price": None})
            else:
                total += row["price"] * it["qty"]
                lines.append(_line(it, row, s["distance"], now))
        out_stores.append({
            "store_id": s["store_id"], "store": s["store"], "chain": s["chain"],
            "distance": s["distance"], "sample": s["sample"],
            "total": round(total, 2), "missing": missing, "complete": missing == 0,
            "items": lines})
    out_stores.sort(key=lambda s: (s["missing"], s["total"], s["distance"]))

    per_item = {}
    for it in all_items:
        options = []
        for s in stores.values():
            row = s["prices"].get(it["product_id"])
            if row:
                options.append({
                    "store_id": s["store_id"], "store": s["store"], "distance": s["distance"],
                    "price": row["price"], "price_id": row["price_id"],
                    "age_days": round(age_days(row["seen_at"], now) or 0, 1),
                    "verified": row["verified_count"], "sample": s["sample"]})
        options.sort(key=lambda o: (o["price"], o["distance"]))
        per_item[it["product_id"]] = {"best": options[0] if options else None,
                                      "count": len(options), "options": options[:8]}

    plans = _plans(items, stores, out_stores, max_stops)
    return {"radius": radius, "item_count": len(all_items), "priced_count": len(items),
            "unpriced": unpriced, "stores": out_stores,
            "per_item": per_item, "plans": plans,
            "best": out_stores[0] if out_stores else None}


def _plans(items, stores, out_stores, max_stops):
    if not items or not stores:
        return []
    candidates = [s["store_id"] for s in out_stores[:MAX_CANDIDATE_STORES]]
    plans = []
    for k in range(1, min(max_stops, len(candidates)) + 1):
        best = None
        for combo in combinations(candidates, k):
            picks, total, missing, used = {}, 0.0, 0, set()
            for it in items:
                choice = None
                for sid in combo:
                    row = stores[sid]["prices"].get(it["product_id"])
                    if row is None:
                        continue
                    key = (row["price"], stores[sid]["distance"])
                    if choice is None or key < choice[0]:
                        choice = (key, sid, row)
                if choice is None:
                    missing += 1
                else:
                    _, sid, row = choice
                    picks[it["product_id"]] = (sid, row)
                    used.add(sid)
                    total += row["price"] * it["qty"]
            if len(used) < k:
                continue   # a smaller plan already covers this one
            dist = sum(stores[sid]["distance"] for sid in used)
            score = (missing, round(total, 2), dist)
            if best is None or score < best[0]:
                best = (score, combo, picks, total, missing)
        if best is None:
            continue
        _, combo, picks, total, missing = best
        by_store = {}
        for it in items:
            if it["product_id"] in picks:
                sid, row = picks[it["product_id"]]
                by_store.setdefault(sid, []).append({
                    "product_id": it["product_id"], "name": it["name"], "qty": it["qty"],
                    "price": row["price"]})
        plans.append({
            "stops": k, "total": round(total, 2), "missing": missing,
            "stores": [{"store_id": sid, "store": stores[sid]["store"],
                        "distance": stores[sid]["distance"], "items": lines}
                       for sid, lines in sorted(by_store.items(),
                                                key=lambda kv: stores[kv[0]]["distance"])]})
    if plans:
        base = plans[0]
        for p in plans:
            p["savings"] = (round(base["total"] - p["total"], 2)
                            if p["missing"] == base["missing"] else None)
    return plans
