# Kijiji GPU Scraper

Scrapes high-end GPU listings from Kijiji GTA (Toronto) using **free methods** — no Apify, no paid APIs.

## How It Works

Kijiji is a Next.js app: listing data is exposed via an **Apollo GraphQL API** (`/anvil/api`) and also embedded server-side in a `<script id="__NEXT_DATA__">` tag. This scraper:

1. Uses `cloudscraper` to get past Cloudflare (free, no API keys needed)
2. Queries the `searchResultsPage` GraphQL endpoint for pages 1..N — this is the
   primary path and returns up to **40 listings per page** (paginated via the
   `/page-N/` URL segment)
3. Falls back to extracting the `__NEXT_DATA__` JSON blob if the API call fails
4. Parses `StandardListing` objects into `KijijiListing` models
5. Can sweep a list of search terms and union the results (`scrape_terms`)
6. Optionally filters to high-end GPU models (RTX 4090/4080/5090/5080, RX 7900 XTX/XT)

Pagination is deep: a single 8-page sweep of `graphics-card` yields 300+ unique
listings, and sweeping nine GPU terms at 2 pages each yields 440+.

## Quick Start

```bash
pip install -r requirements.txt

# Scrape 3 pages of "graphics-card" listings, show only high-end GPUs
python scraper.py

# Scrape specific search terms (deeper pagination)
python scraper.py --search "rtx-4090"
python scraper.py --search "rtx-5080" --pages 2

# See all listings (not just high-end)
python scraper.py --all

# Save to file
python scraper.py --search "rtx-4080" --output results.json
```

The term list swept by the deal-sniper pipeline (and by `scrape_terms()`) lives in
`config.yaml` under `search.search_terms`; `search.max_listings` drives the depth.

## High-End GPU Models Tracked

| Model | Typical Kijiji Price |
|-------|---------------------|
| RTX 5090 | $4,000–$10,000 |
| RTX 5080 | $1,500–$3,000 |
| RTX 4090 | $1,800–$2,500 |
| RTX 4080 Super | $1,200–$1,800 |
| RTX 4080 | $1,000–$1,600 |
| RX 7900 XTX | $800–$1,200 |
| RX 7900 XT | $700–$1,000 |

## Output

```json
{
  "scraped_at": "2026-08-20T23:19:56.205919",
  "search_term": "graphics-card",
  "total_listings": 2,
  "listings": [
    {
      "title": "ASUS GEFORCE RTX 4090",
      "price_cad": 1800.0,
      "url": "https://www.kijiji.ca/...",
      "location": "Toronto",
      "description": "...",
      "date_posted": "2026-08-19T17:02:23.000Z",
      "listing_id": "1741342205",
      "image_urls": ["..."],
      "scraped_at": "2026-08-20T23:19:56.204917"
    }
  ]
}
```

## Next Steps (Sniper Integration)

This scraper is the ingestion layer for the full **Hardware Deal Sniper**:
- Add SQLite dedup storage
- Add scam keyword pre-filter
- Add LLM-based extraction + valuation (OpenRouter)
- Add Discord alerting
- Wrap in async scheduler loop