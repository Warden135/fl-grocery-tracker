# Florida Grocery Price Tracker

A small web app that records grocery prices across Florida stores and lets you:

- **Compare** one product across stores
- See **price history** per store (line chart)
- Build a **shopping list** and see which store is cheapest for the whole basket
- Set **price alerts** (shows when any store is at or below your target)

Stack: Python, Flask, SQLite, vanilla JS + Chart.js. No build step.

## Run it

```bash
pip install -r requirements.txt
python seed.py            # optional: fake DEMO data so the UI isn't empty
python -m app.main        # http://localhost:5000
```

## Getting real prices

Prices come in two ways:

1. **Scraper** (`app/scraper.py`). Each product/store pair is a row in `listings`.
   Give a listing a `url` (and optionally a CSS `selector` for the price element) and run:

   ```bash
   python -m app.scraper          # or POST /api/scrape
   ```

   It honors robots.txt, waits 3 seconds between requests, and tries your selector,
   then JSON-LD `offers.price`, then `<meta>` price tags. Run it daily with cron:

   ```
   0 6 * * * cd /path/to/fl-grocery-tracker && python -m app.scraper
   ```

   Example listing setup:

   ```sql
   UPDATE listings SET url='https://example.com/product/123', selector='span.price'
   WHERE product_id=1 AND store_id=3;
   ```

2. **Manual entry**: the "Add price" tab, or `POST /api/prices`.

### Scraping caveats (read before relying on it)

- Big chains (Publix, Walmart, Winn-Dixie, etc.) often load prices with JavaScript,
  require choosing a store/ZIP, or block automated traffic. Plain HTTP scraping may
  return nothing for them. Options: a headless browser (Playwright), an official
  or paid data API, or user-submitted prices.
- Check each site's terms of service. The scraper skips pages disallowed by robots.txt.
- Prices vary by store location, so create one store row per location you track.

## Data model

`stores` · `products` · `listings` (product at a store, plus URL/selector) ·
`prices` (time series) · `alerts` · `list_items`

## Ideas for next steps

- Email/push notifications when an alert triggers
- Per-ZIP store locator and unit-price (per oz/lb) normalization
- User accounts so lists and alerts are per person
- Playwright-based scraper for JavaScript-heavy sites
