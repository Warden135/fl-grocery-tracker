"""Points, levels and the rules that keep price reports honest."""
from datetime import datetime, timezone

REPORT_POINTS = 5          # you reported a price
CONFIRM_POINTS = 1         # you confirmed someone else's price
CONFIRMED_BONUS = 2        # someone confirmed your price (up to MAX_BONUS_PER_REPORT times)
REVIEW_POINTS = 3          # first written review of a store (20+ characters)
DAILY_REPORT_CAP = 10      # rewarded reports per rolling 24 hours
DAILY_CONFIRM_CAP = 20     # rewarded confirmations per rolling 24 hours
MAX_BONUS_PER_REPORT = 3
MAX_PRICE_AGE_DAYS = 14    # older prices can't be confirmed, only re-reported

LEVELS = [(0, "Newcomer"), (50, "Scout"), (150, "Regular"), (400, "Pro")]

RULES = [
    {"points": REPORT_POINTS, "text": "Report a price you saw in a store"},
    {"points": CONFIRM_POINTS, "text": "Confirm that another shopper's price is still right"},
    {"points": CONFIRMED_BONUS, "text": "Someone confirms a price you reported (up to 3 times per price)"},
    {"points": REVIEW_POINTS, "text": "Write your first review of a store"},
]

REASON_TEXT = {
    "report": "Reported a price",
    "confirm": "Confirmed a price",
    "confirmed": "A shopper confirmed your price",
    "verified_report": "Your unusual price was confirmed",
    "review": "Reviewed a store",
    "redeem": "Unlocked a perk",
}


def award(conn, user_id, delta, reason, ref_id=None):
    conn.execute("INSERT INTO points_ledger (user_id, delta, reason, ref_id) VALUES (?, ?, ?, ?)",
                 (user_id, delta, reason, ref_id))
    conn.execute("UPDATE users SET points = points + ? WHERE id = ?", (delta, user_id))


def count_recent(conn, user_id, reason, hours=24):
    return conn.execute(
        "SELECT COUNT(*) FROM points_ledger WHERE user_id = ? AND reason = ? "
        "AND created_at >= datetime('now', ?)", (user_id, reason, f"-{hours} hours")).fetchone()[0]


def count_ref(conn, user_id, reason, ref_id):
    return conn.execute(
        "SELECT COUNT(*) FROM points_ledger WHERE user_id = ? AND reason = ? AND ref_id = ?",
        (user_id, reason, ref_id)).fetchone()[0]


def level_for(earned):
    idx = 0
    for i, (threshold, _) in enumerate(LEVELS):
        if earned >= threshold:
            idx = i
    nxt = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    return {"name": LEVELS[idx][1], "index": idx,
            "floor": LEVELS[idx][0],
            "next_name": nxt[1] if nxt else None,
            "next_at": nxt[0] if nxt else None}


def median(values):
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2


def is_plausible(price, med):
    """A price is plausible if it is within half to double the typical price."""
    if med is None:
        return True
    return 0.5 * med <= price <= 2.0 * med


def age_days(ts, now=None):
    """Age in days of a 'YYYY-MM-DD HH:MM:SS' UTC timestamp."""
    if not ts:
        return None
    then = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - then).total_seconds() / 86400)
