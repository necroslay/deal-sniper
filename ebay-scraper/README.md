# eBay Scraper — Canada (Comp-Driven Valuation)

Scrapes eBay Canada listings including **completed/sold items** for comp-driven resale valuation using **free methods** — no official eBay API keys needed. Part of the comp-driven resale valuation engine.

## How It Works

eBay embeds listing data in server-rendered HTML and a JavaScript state object. This scraper:

1. Uses `requests` with desktop browser headers to fetch search result pages
2. Extracts listing data from the page's structured HTML + `__INITIAL_STATE__` JSON blob
3. Supports **active listings** (current asking prices) and **completed/sold** listings (ground truth for comp valuation)
4. Filters by category, condition, and location (Canada)
5. Normalises titles to canonical product names
6. Stores results for the valuation pipeline

## Quick Start

```bash
pip install -r requirements.txt

# Scrape active listings for "RTX 4090" (Canada)
python scraper.py --search "rtx 4090"

# Scrape completed / sold listings (comp data)
python scraper.py --search "rtx 4090" --sold

# Save to file
python scraper.py --search "rtx 4090" --sold --output comps.json

# Show all categories
python scraper.py --categories
```

## Output

```json
{
  "scraped_at": "2026-08-23T12:00:00.000000",
  "search_term": "rtx 4090",
  "listing_type": "completed",
  "total_listings": 50,
  "listings": [
    {
      "title": "NVIDIA GeForce RTX 4090 Founders Edition 24GB GDDR6X",
      "price_cad": 2899.99,
      "url": "https://www.ebay.ca/itm/...",
      "condition": "Used",
      "location": "Mississauga, ON",
      "sold_date": "2026-08-20",
      "sold_price_cad": 2750.00,
      "listing_id": "123456789",
      "image_urls": ["..."],
      "scraped_at": "2026-08-23T12:00:00.000000"
    }
  ]
}
```

## Comp Valuation

The sold listings feed the valuation engine:
- Median / mean sold price per canonical product
- Price distribution by condition (brand new → for parts)
- Days-on-market estimates (active vs. sold velocity)
- Price filters by Canadian region

## Next Steps

- Add persistent SQLite dedup storage (active + sold)
- Add multi-page crawling with pagination
- Add category taxonomy mapping
- Integrate with the comp valuation engine from `copilotinstructions.md`