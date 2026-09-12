# Facebook Marketplace Scraper — Canada

Scrapes product listings from Facebook Marketplace GTA (Toronto / Canada) using **free methods** — no paid APIs. Part of the comp-driven resale valuation engine.

## How It Works

Facebook Marketplace loads listing data via its GraphQL API. This scraper:

1. Authenticates via a session cookie (manual browser export — no account automation)
2. Queries the Marketplace search GraphQL endpoint
3. Extracts listing title, price, location, description, images, and posted date
4. Normalises titles to canonical product names
5. Filters, scores, and stores results for the valuation pipeline

## Quick Start

```bash
pip install -r requirements.txt

# Export your Facebook session cookie to fb_cookies.json first.
# Then scrape GTA marketplace for "graphics card"
python scraper.py

# Search for specific terms
python scraper.py --search "gaming-pc" --pages 5

# Save to file
python scraper.py --search "macbook" --output results.json
```

## Session Cookie Setup

Facebook requires authentication to browse Marketplace. Export your browser cookies:

1. Install a cookie exporter extension (e.g., "Get cookies.txt" or "Cookie-Editor")
2. Log into Facebook in your browser
3. Export cookies to `fb_cookies.json` in this directory
4. The scraper reads these to authenticate requests

## Output

```json
{
  "scraped_at": "2026-08-23T12:00:00.000000",
  "search_term": "graphics-card",
  "total_listings": 15,
  "listings": [
    {
      "title": "RTX 4090 Founders Edition",
      "price_cad": 2000.0,
      "url": "https://www.facebook.com/marketplace/item/...",
      "location": "Toronto, ON",
      "description": "Like new, used for 3 months...",
      "date_posted": "2026-08-22T10:30:00.000Z",
      "listing_id": "123456789",
      "image_urls": ["..."],
      "scraped_at": "2026-08-23T12:00:00.000000"
    }
  ]
}
```

## Next Steps

- Add persistent SQLite dedup storage
- Add LLM-based extraction for messy titles
- Add Discord/Telegram alerting for high-value deals
- Integrate with the comp valuation engine from `copilotinstructions.md`