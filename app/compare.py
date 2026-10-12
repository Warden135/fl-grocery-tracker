"""Compare a shopping list across Winn-Dixie, Publix and Walmart.

Only real rows are used: a price that is missing stays missing, and an offer only
counts when it is in the offers table, is current, and (if it needs a store account)
the shopper says they have one. Each item gets at most ONE offer per store, so a
discount can never be counted twice.
"""
from itertools import combinations

from . import brands
from .geo import haversine_miles
from .providers import STALE_DAYS, price_quality
from .scoring import age_days

STRATEGIES = ("lowest", "fewest", "value")


def money(n):
    return f"${n:.2f}"


def _unit_offer(offer, price):
    """Per-unit price while the offer applies, or None if it doesn't beat the shelf price."""
    if offer.get("sale_price") is not None:
        eff = offer["sale_price"]
    else:
        eff = price - (offer.get("amount_off") or 0)
    eff = round(max(0.0, eff), 2)
    return eff if eff < price else None


def _best_offer(offers, chain, pid, price, programs):
    """Best usable offer for one product at one chain. min_spend offers are handled later."""
    best = None
    for o in offers:
        if o["chain"] != chain or o["product_id"] != pid or o.get("min_spend"):
            continue
        if o.get("requires_account") and chain not in programs:
            continue
        eff = _unit_offer(o, price)
        if eff is not None and (best is None or eff < best[0]):
            best = (eff, o)
    return best


def _locked_offer(offers, chain, pid, price, programs):
    """An offer that would apply if the shopper had an account for this chain."""
    for o in offers:
        if (o["chain"] == chain and o["product_id"] == pid and o.get("requires_account")
                and chain not in programs and _unit_offer(o, price) is not None):
            return o
    return None


def _offer_view(o, eff, price, qty):
    n = min(qty, o["max_qty"]) if o.get("max_qty") else qty
    return {"id": o["id"], "title": o["title"], "kind": o["kind"], "expires_on": o["expires_on"],
            "conditions": o["conditions"], "source": o["source"], "unit_price": eff,
            "saves": round((price - eff) * n, 2), "units": n}


def build_compare(items, price_rows, offers, center, radius, prefs, now=None):
    programs = set(prefs.get("programs") or [])
    wanted = [k for k in (prefs.get("preferred_chains") or []) if k in brands.BY_KEY] or list(brands.KEYS)
    verified_only = bool(prefs.get("verified_only"))
    rate = float(prefs.get("cost_per_mile") or 0)
    min_savings = float(prefs.get("min_savings") or 0)
    notes = []

    stores = {}
    hidden = 0
    for r in price_rows:
        key = brands.chain_key(r["chain"])
        if key is None or r["lat"] is None or r["lng"] is None:
            continue
        d = haversine_miles(center[0], center[1], r["lat"], r["lng"])
        if d > radius:
            continue
        q = price_quality(r)
        if verified_only and q != "verified":
            hidden += 1
            continue
        s = stores.setdefault(r["store_id"], {"store_id": r["store_id"], "store": r["store"], "chain": key,
                                              "distance": round(d, 1), "prices": {}})
        s["prices"][r["product_id"]] = {**r, "quality": q}
    if hidden:
        notes.append(f"Verified-only is on, so {hidden} unconfirmed or sample price"
                     f"{'s are' if hidden != 1 else ' is'} left out.")

    priced_ids = {pid for s in stores.values() if s["chain"] in wanted for pid in s["prices"]}
    open_items = [i for i in items if not i["checked"]]
    unpriced = [{"product_id": i["product_id"], "name": i["name"]} for i in open_items if i["product_id"] not in priced_ids]
    scored = [i for i in open_items if i["product_id"] in priced_ids]

    # One store per chain: the one that prices the most of the list, then cheapest, then nearest.
    primary = {}
    for key in wanted:
        cands = [s for s in stores.values() if s["chain"] == key]
        if not cands:
            continue

        def rank(s):
            have = [i for i in scored if i["product_id"] in s["prices"]]
            return (len(scored) - len(have), sum(s["prices"][i["product_id"]]["price"] * i["qty"] for i in have), s["distance"])
        primary[key] = min(cands, key=rank)

    chains = []
    for b in brands.BRANDS:
        k = b["key"]
        if k not in wanted:
            continue
        chains.append({"key": k, "name": b["name"], "store": primary[k]["store"] if k in primary else None,
                       "distance": primary[k]["distance"] if k in primary else None,
                       "has_store": k in primary})
        if k not in primary:
            notes.append(f"No {b['name']} store with price data within {radius} miles of your ZIP.")

    # ---- per item ----
    out_items, has_sample, has_unconfirmed, stale_seen = [], False, False, False
    for it in items:
        pid, qty = it["product_id"], it["qty"]
        row = {"item_id": it["item_id"], "product_id": pid, "name": it["name"], "qty": qty, "checked": it["checked"],
               "brand": it.get("brand"), "package_size": it.get("package_size"), "image_url": it.get("image_url"),
               "unit": it.get("unit"), "prices": {}}
        for k in wanted:
            s = primary.get(k)
            pr = s["prices"].get(pid) if s else None
            if not pr:
                row["prices"][k] = None
                continue
            offer = _best_offer(offers, k, pid, pr["price"], programs)
            locked = _locked_offer(offers, k, pid, pr["price"], programs)
            age = round(age_days(pr["seen_at"], now) or 0, 1)
            has_sample |= pr["quality"] == "sample"
            has_unconfirmed |= pr["quality"] == "reported"
            stale_seen |= age > STALE_DAYS
            eff = offer[0] if offer else pr["price"]
            row["prices"][k] = {
                "price": pr["price"], "effective": eff, "quality": pr["quality"], "age_days": age,
                "stale": age > STALE_DAYS, "price_id": pr["price_id"], "store": s["store"],
                "distance": s["distance"], "offer": _offer_view(offer[1], eff, pr["price"], qty) if offer else None,
                "locked_offer": ({"title": locked["title"], "program": k} if locked else None)}
        avail = [(v["effective"], primary[k]["distance"], k) for k, v in row["prices"].items() if v]
        avail.sort()
        row["best"], row["reason"], row["save_vs_next"], row["save_vs_highest"] = None, None, 0.0, 0.0
        if not avail:
            row["reason"] = "No price data for this item at the three stores yet."
        else:
            best_eff, _, best_k = avail[0]
            row["best"] = best_k if len(avail) == 1 or avail[1][0] > best_eff else None
            nm = brands.BY_KEY[best_k]["name"]
            if len(avail) == 1:
                row["best"] = best_k
                row["reason"] = f"Only {nm} has price data for this item, so there is nothing to compare yet."
            elif row["best"] is None:
                tied = [brands.BY_KEY[a[2]]["name"] for a in avail if a[0] == best_eff]
                row["reason"] = f"{' and '.join(tied)} are tied at {money(best_eff)}."
            else:
                row["save_vs_next"] = round((avail[1][0] - best_eff) * qty, 2)
                row["save_vs_highest"] = round((avail[-1][0] - best_eff) * qty, 2)
                v = row["prices"][best_k]
                why = f"{nm} has the lowest price at {money(best_eff)}"
                if v["offer"]:
                    why += f" with {v['offer']['title']} (shelf price {money(v['price'])})"
                others = ", ".join(f"{brands.BY_KEY[a[2]]['name']} {money(a[0])}" for a in avail[1:])
                why += f", versus {others}."
                if v["quality"] != "verified":
                    why += " This price is " + ("sample data." if v["quality"] == "sample" else "an unconfirmed shopper report.")
                row["reason"] = why
        out_items.append(row)

    active = [r for r in out_items if not r["checked"] and r["product_id"] in priced_ids]

    # ---- plans ----
    def eval_assignment(assign):
        """assign: {product_id: chain}. Returns totals including min-spend offers."""
        per_chain = {}
        for r in active:
            k = assign.get(r["product_id"])
            if not k:
                continue
            v = r["prices"][k]
            n = r["qty"]
            line = {"product_id": r["product_id"], "name": r["name"], "qty": n, "price": v["price"],
                    "effective": v["effective"], "offer": v["offer"], "quality": v["quality"],
                    "line_total": round(v["effective"] * n if not v["offer"] else
                                        v["price"] * n - v["offer"]["saves"], 2),
                    "regular_total": round(v["price"] * n, 2)}
            per_chain.setdefault(k, []).append(line)
        stores_out, total, saved = [], 0.0, 0.0
        for k, lines in per_chain.items():
            sub = sum(l["line_total"] for l in lines)
            extra = []
            for o in offers:   # min-spend offers apply once the store subtotal reaches the minimum
                if o["chain"] != k or not o.get("min_spend") or sub < o["min_spend"]:
                    continue
                if o.get("requires_account") and k not in programs:
                    continue
                for l in lines:
                    if o["product_id"] == l["product_id"] and not l["offer"]:
                        eff = _unit_offer(o, l["price"])
                        if eff is not None:
                            ov = _offer_view(o, eff, l["price"], l["qty"])
                            l["offer"], l["effective"] = ov, eff
                            l["line_total"] = round(l["regular_total"] - ov["saves"], 2)
                            extra.append(ov)
            sub = sum(l["line_total"] for l in lines)
            reg = sum(l["regular_total"] for l in lines)
            stores_out.append({"chain": k, "store": primary[k]["store"], "distance": primary[k]["distance"],
                               "items": lines, "subtotal": round(sub, 2), "regular_subtotal": round(reg, 2)})
            total += sub
            saved += reg - sub
        stores_out.sort(key=lambda s: s["distance"])
        travel = round(sum(2 * s["distance"] * rate for s in stores_out), 2)
        return {"stores": stores_out, "total": round(total, 2), "offer_savings": round(saved, 2),
                "travel_cost": travel}

    plans = []
    avail_chains = [k for k in wanted if k in primary]
    for n in range(1, len(avail_chains) + 1):
        for combo in combinations(avail_chains, n):
            assign, missing = {}, 0
            for r in active:
                opts = [(r["prices"][k]["effective"], primary[k]["distance"], k) for k in combo if r["prices"].get(k)]
                if opts:
                    assign[r["product_id"]] = min(opts)[2]
                else:
                    missing += 1
            used = set(assign.values())
            if len(used) < n:
                continue
            p = eval_assignment(assign)
            p.update(chains=sorted(used), missing=missing, stops=n)
            p["cost"] = round(p["total"] + p["travel_cost"], 2)
            plans.append(p)

    singles = [p for p in plans if p["stops"] == 1]
    recs, multi_verdict = {}, None
    if plans:
        best_single = min(singles, key=lambda p: (p["missing"], p["total"], p["travel_cost"]))
        best_cover = min(p["missing"] for p in plans)
        full = [p for p in plans if p["missing"] == best_cover]
        multi = [p for p in full if p["stops"] > 1]
        lowest = min(full, key=lambda p: (p["cost"], p["stops"]))
        value, best_gain = best_single, 0.0
        for p in multi:
            gain = round(best_single["cost"] - p["cost"], 2)
            if p["missing"] <= best_single["missing"] and gain >= max(min_savings, 0.01) and gain > best_gain:
                value, best_gain = p, gain
        picks = {"lowest": lowest, "fewest": best_single, "value": value}
        best_multi = min(multi, key=lambda p: p["cost"]) if multi else None
        if best_multi is not None and best_multi["missing"] <= best_single["missing"]:
            gross = round(best_single["total"] - best_multi["total"], 2)
            extra_travel = round(best_multi["travel_cost"] - best_single["travel_cost"], 2)
            net = round(gross - extra_travel, 2)
            worth = net >= max(min_savings, 0.01)
            names = " + ".join(brands.BY_KEY[k]["name"] for k in best_multi["chains"])
            if net <= 0:
                text = (f"Splitting between {names} would save {money(max(gross, 0))} on groceries, but about "
                        f"{money(max(extra_travel, 0))} of extra driving cancels it out.")
            elif worth:
                text = (f"Worth it: {names} saves {money(gross)} on groceries, about {money(net)} after "
                        f"{money(max(extra_travel, 0))} of extra driving.")
            else:
                text = (f"Probably not worth it: {names} saves {money(net)} after driving, below your "
                        f"{money(min_savings)} minimum.")
            multi_verdict = {"worth": worth, "gross": gross, "extra_travel": extra_travel, "net": net,
                             "chains": best_multi["chains"], "text": text}
        for name, p in picks.items():
            recs[name] = p
        # savings against the other single-store options with the same coverage
        comparable = [s for s in singles if s["missing"] == best_single["missing"]]
        priciest = max(comparable, key=lambda s: s["total"]) if comparable else None
    else:
        priciest = None

    def single_json(p):
        k = p["chains"][0]
        return {"chain": k, "store": p["stores"][0]["store"], "distance": p["stores"][0]["distance"],
                "total": p["total"], "offer_savings": p["offer_savings"], "missing": p["missing"],
                "travel_cost": p["travel_cost"]}
    singles_json = sorted((single_json(p) for p in singles), key=lambda s: (s["missing"], s["total"]))
    for s in singles_json:
        comp = [x for x in singles_json if x["missing"] == s["missing"]]
        s["vs_priciest"] = round(max(x["total"] for x in comp) - s["total"], 2) if len(comp) > 1 else None

    def rec_json(p):
        if not p:
            return None
        return {**p, "chain_names": [brands.BY_KEY[k]["name"] for k in p["chains"]]}

    chosen = prefs.get("strategy") if prefs.get("strategy") in STRATEGIES else "value"
    budget = prefs.get("budget")
    cp = recs.get(chosen)
    budget_info = None
    if budget and cp:
        budget_info = {"budget": budget, "total": cp["total"], "over": round(cp["total"] - budget, 2)}
    if has_sample:
        notes.append("Some prices are sample data, not real store prices. Totals are for demonstration only.")
    if has_unconfirmed:
        notes.append("Some prices are single shopper reports that nobody has confirmed yet.")
    if stale_seen:
        notes.append(f"Some prices are over {STALE_DAYS} days old and may have changed.")
    if not offers:
        notes.append("No weekly ads or coupons are loaded, so no deal savings are included.")
    return {
        "radius": radius, "chains": chains, "items": out_items, "unpriced": unpriced,
        "singles": singles_json, "recs": {k: rec_json(v) for k, v in recs.items()},
        "chosen": chosen, "multi_verdict": multi_verdict, "budget": budget_info,
        "notes": notes, "scored_count": len(active),
        "min_savings": min_savings, "cost_per_mile": rate,
        "flags": {"sample": has_sample, "unconfirmed": has_unconfirmed, "stale": stale_seen},
    }
