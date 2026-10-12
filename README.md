# Shelf Scout

A grocery price app for friends and family. Build a shopping list, and see which store
within 5, 10 or 15 miles of your ZIP code charges the least. Shoppers report the prices
they see, confirm each other's reports, and earn points for it.

It's an installable web app (PWA): it runs in any browser and can be added to a phone's
home screen. Stack: Python, Flask, SQLite, plain JavaScript. No build step.

## What it does

- **Accounts.** Sign up with a name, email, password and ZIP code.
- **My list.** Add items, set quantities, tick off what you've bought. Each item shows the
  cheapest nearby price on a yellow "shelf tag".
- **Where to shop.** The cheapest single store for the whole list, plus the best 2-stop and
  3-stop plans and how much each one saves.
- **Report a price.** Pick a store, an item and a price. Stores and items can be added by
  anyone.
- **Confirm prices.** A feed of nearby prices to mark "still right" or correct.
- **Rewards.** Points for reports, confirmations and reviews. Spend them on in-app perks
  (badges, a theme, a gold name). No real money is involved.
- **Reviews and feedback.** Star ratings and comments for each store, and a feedback form.
- **Price alerts.** Get a flag when an item drops to your target price nearby.

## Run it

```bash
pip install -r requirements.txt
python seed.py                   # optional: sample stores and prices (clearly marked "sample")
python -m app.main               # http://localhost:5000
```

On Windows, for a server that other devices can reach:

```
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\waitress-serve --listen=0.0.0.0:5000 app.main:app
```

With Docker, see [DOCKER.md](DOCKER.md). On a Linux home server, see [DEPLOY.md](DEPLOY.md).

Upgrading from the first (single-user) version? Your database is upgraded automatically.
Prices and items are kept. The old shared list and alerts are dropped because lists are
per user now. Run `python seed.py` once to replace the old sample stores with ones that
have locations.

### Real store locations

Sample stores are fake. To load real supermarkets for an area from OpenStreetMap
(free, needs internet):

```bash
python -m scripts.import_osm_stores 32202 --miles 15
```

Prices start empty for real stores and fill in as people report them. You can also add
stores in the app from the Report tab.

### Settings (environment variables)

| Variable | What it does |
|---|---|
| `GROCERY_DB` | Path to the SQLite file (default `grocery.db`). The session secret is stored next to it. |
| `SECRET_KEY` | Signs login cookies. Optional: one is generated and saved next to the database. |
| `COOKIE_SECURE` | Set to `1` when serving over HTTPS. |
| `APP_PASSWORD` | Optional extra password for the whole site (any username). Handy before you invite anyone. |
| `ADMIN_TOKEN` | Enables `POST /api/admin/scrape` with header `X-Admin-Token`. |
| `SIGNUP_LIMIT` | Sign-ups allowed per hour per network (default 30). |

## How the rewards work

| You | Points |
|---|---|
| Report a price | +5 |
| Confirm someone's price | +1 |
| Someone confirms your price | +2 (up to 3 times per price) |
| Write your first review of a store (20+ characters) | +3 |

Guardrails: one rewarded report per item per store per day, 10 rewarded reports and 20
rewarded confirmations per 24 hours, no confirming your own price, and prices more than
2 weeks old can't be confirmed. A price that is under half or over double the usual
price needs two confirmations from other people before it counts or earns points.
Confirming a price also refreshes its "last seen" date.

## Data and privacy

- The display name you choose is shown on your reviews and reports. Emails are never shown.
- Passwords are stored as hashes. Login attempts are rate limited.
- ZIP codes are looked up with the free Zippopotam.us service and cached. Fonts load from
  Google Fonts.
- Prices come from shoppers (and the optional scraper), so they can be wrong or stale.

## Optional: the price scraper

`app/scraper.py` can read prices from store web pages you point it at (a `url` and CSS
`selector` on a row in the `listings` table). It follows robots.txt and waits between
requests. Many chains load prices with JavaScript or block bots, so treat it as an
experiment and check each site's terms. Run it with `python -m app.scraper`.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

## Project layout

```
app/        Flask API (main.py), database (db.py), points rules (scoring.py),
            basket optimizer (basket.py), ZIP lookup (geo.py), scraper
static/     The app: index.html, app.css, app.js, manifest, service worker, icons
scripts/    OpenStreetMap importer, icon generator
tests/      API and unit tests
deploy/     systemd files for a Linux home server
```

## Next steps

- Moderation tools (hide a bad report, ban a spammer) before opening it to strangers
- Push or email notifications for price alerts
- Unit prices (per ounce or pound) so different package sizes compare fairly
- Wrap the web app with Capacitor if you want App Store and Google Play versions

## Personal shopping assistant features (v3)

- **Top navigation:** List, Deals, Rewards, Me. Reporting prices lives under Rewards.
- **Stores:** Winn-Dixie, Publix and Walmart are compared side by side. Badges show each chain's name in its brand color. The app does not ship the chains' logos; to use official ones you are licensed to use, drop `winndixie.svg`, `publix.svg` and `walmart.svg` (or `.png`) into `static/brands/`.
- **Removing items:** "Did you buy this item?" Yes records the purchase (optionally store and price paid), No just removes it. Both can be undone; purchase history is kept.
- **Restock reminders:** learned from purchase history (median gap between purchases), always labeled as estimates. Set your own interval and quantity, snooze, dismiss, mark restocked, or turn reminders off.
- **Comparison:** per item and per list, with lowest cost, fewest stores, or best value (extra stops must beat your minimum saving after estimated driving).
- **Honest data:** every price is labeled Verified, Unconfirmed or Sample, and missing data is shown as missing. Deals and coupons appear only if real offers are loaded; none are included. Offers are loaded with `POST /api/admin/offers` (needs `ADMIN_TOKEN`) or by adding a provider in `app/providers.py`. No store feed is connected yet.

### Fast price sharing
- **Check items off with a price:** in "Did you buy this item?", pick the store and enter the price paid. If a real (non-sample) store of that chain is within your search distance, the price is shared as a report and earns points. The store is remembered for next time.
- **Scan a receipt** (List tab, or Rewards > Report a price): paste the receipt text, or take a photo. You review the matches and prices, then save. Saving shares the prices, adds them to purchase history (so restock estimates learn faster), and takes those items off your list.
- Photo reading needs `ANTHROPIC_API_KEY` on the server and sends the photo to Anthropic's API; it has not been tested against the live API. Pasted text needs no setup.
