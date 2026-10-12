"""Estimate when a shopper will need to restock, from their own purchase history.

All dates are estimates. With a single purchase there is nothing to learn from, so no
estimate is made until the shopper sets their own interval or buys the item again.
"""
from datetime import datetime, timedelta
from statistics import median


LEAD_DAYS = 1        # remind this many days before the estimated run-out date
MIN_INTERVAL = 1
MAX_INTERVAL = 365


def _d(ts):
    return datetime.strptime(ts[:10], "%Y-%m-%d").date()


def estimate(purchases, pref=None, today=None):
    """purchases: [{bought_at, qty}] oldest first. pref: item_prefs row or None.
    Returns None when there is nothing to base an estimate on."""
    today = today or datetime.utcnow().date()
    pref = pref or {}
    days = sorted({_d(p["bought_at"]) for p in purchases})
    last = days[-1] if days else None
    qty_hist = [p["qty"] for p in purchases]
    learned = None
    gaps = [(b - a).days for a, b in zip(days, days[1:]) if (b - a).days >= MIN_INTERVAL]
    if gaps:
        learned = max(MIN_INTERVAL, round(median(gaps[-5:])))
    manual = pref.get("interval_days")
    interval = manual or learned
    if not interval or not last:
        return None
    due = last + timedelta(days=interval)
    n = len(gaps)
    confidence = "set by you" if manual else ("low" if n < 2 else "fair" if n < 4 else "good")
    return {
        "interval_days": interval, "learned_days": learned, "manual": bool(manual),
        "last_bought": last.isoformat(), "runs_out": due.isoformat(),
        "days_left": (due - today).days, "due": (due - today).days <= LEAD_DAYS,
        "confidence": confidence, "based_on": n if not manual else None,
        "usual_qty": pref.get("usual_qty") or (max(1, round(median(qty_hist[-5:]))) if qty_hist else 1),
    }


def reminder_state(est, pref, today=None):
    """Return 'due', 'snoozed', 'dismissed' or 'later' for an estimate."""
    if not est or not est["due"]:
        return "later"
    pref = pref or {}
    now = datetime.utcnow()
    if pref.get("snoozed_until") and pref["snoozed_until"] > now.strftime("%Y-%m-%d %H:%M:%S"):
        return "snoozed"
    if pref.get("dismissed_after") and pref["dismissed_after"] >= est["last_bought"]:
        return "dismissed"
    return "due"
