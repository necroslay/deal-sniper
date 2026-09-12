# Craigslist Scraper — Canada

Scrapes classified listings from Craigslist Canada (Toronto / GTA) using **free methods** (no account or API key). Part of the comp-driven resale valuation engine.

## How It Works

Craigslist search results are **rendered client-side**: a plain HTTP GET returns an
~8 KB JavaScript shell with zero result rows, and the legacy `?format=rss` feed now
returns 404 / 403. A real browser is therefore required. This scraper:

1. Renders the search page with Playwright, preferring the **locally-installed Chrome**
   (`channel="chrome"`) and falling back to Playwright's bundled Chromium
2. Reuses one browser process across pages so multi-page sweeps stay fast
3. Parses the rendered DOM with BeautifulSoup (`div.cl-search-result` cards,
   with the legacy `li.cl-static-search-result` layout as a fallback)
4. Extracts title, price, location, images and posted date — Craigslist shows a
   relative label ("5h ago") for fresh posts and a year-less `M/D` (or `09-10`)
   for older ones, both of which are normalised to ISO-8601
5. Normalises titles to canonical product names
6. Filters, scores, and stores results for the valuation pipeline

## Quick Start

```bash
pip install -r requirements.txt
playwright install chromium   # skip if a local Chrome is present

# Scrape the computer-parts section (where GPUs actually are)
python scraper.py --search "graphics card"

# Sweep every configured category (cpd, sys, elec)
python scraper.py --search "graphics card" --pages 2

# Also open each listing page for the full description (slower, browser-driven)
python scraper.py --search "graphics card" --fetch-details

# Save to file
python scraper.py --search "graphics card" --output results.json
```

> Note: the `elec` (Electronics) category returns no results for GPU queries —
> GPU/PC-parts listings live under `cpd` (Computer Parts), which is the default.


## Output

```json
{
  "scraped_at": "2026-08-23T12:00:00.000000",
  "search_term": "graphics card",
  "total_listings": 54,
  "listings": [
    {
      "title": "ASUS ROG Strix RTX 4070 Ti 12GB",
      "price_cad": 650.0,
      "url": "https://www.craigslist.org/view/d/downtown-toronto-.../2kQuBPZ5m3LGEkBoL48sno",
      "location": "city of toronto",
      "description": "",
      "date_posted": "2026-08-23T04:12:00+00:00",
      "listing_id": "7951348061",
      "image_urls": ["https://images.craigslist.org/..."],
      "scraped_at": "2026-08-23T12:00:00.000000"
    }
  ]
}
```

(Result URLs use Craigslist's `/view/d/<slug>/<token>` form, and `listing_id` is the
card's `data-pid` — neither is derivable from the old `/<id>.html` URL pattern.)

## Craigslist Categories

| Code | Category     |
|------|-------------|
| elec | Electronics |
| cpd  | Computer Parts |
| sys  | Computers  |
| msg  | Video Gaming |

## Next Steps

- Add persistent SQLite dedup storage
- Add LLM-based extraction for messy titles
- Add Discord/Telegram alerting for high-value deals
- Integrate with the comp valuation engine from `copilotinstructions.md`

> The old RSS-feed idea is a dead end: Craigslist's `?format=rss` endpoint now
> returns 404 / 403, which is why the headless-browser path is the supported one.