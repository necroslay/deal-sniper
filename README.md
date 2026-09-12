# Deal Sniper — scraper + viewer

Scrapes second-hand computer parts from **Kijiji** and **Craigslist** (GTA), stores
them in SQLite, and shows them in a Streamlit dashboard so you can browse and
decide what's worth chasing.

Deliberately small: **scrape → look → decide**. No pricing model in the loop.

> The full comp-driven valuation engine (203 tests, FMV/ERV maths, deal scoring,
> alert gate, lifecycle inference, retail anchoring) is preserved at git tag
> **`full-engine-v1`**, with its documentation in
> [`archive/engine-docs/`](archive/engine-docs/).

## Quick start

```bash
# 1. Install
cd app
pip install -r requirements.txt

# 2. Scrape (writes app/listings.db)
cd ..
python -m app.scrape                          # Kijiji + Craigslist
python -m app.scrape --source kijiji --pages 1   # quick run

# 3. Browse
streamlit run app/dashboard.py
```

Craigslist needs a browser: that scraper drives the **Chrome already installed on
your machine** via Playwright (`channel="chrome"`), falling back to bundled
Chromium. If Playwright's package is missing: `pip install playwright`.

## How it works

```
Kijiji      ─┐
             ├─→ adapter ─→ resolve product ─→ guard flags ─→ SQLite ─→ Streamlit
Craigslist  ─┘
```

Each listing is stored **once**, keyed on `(source, listing id)`. Repeat scrapes
refresh `last_seen_at` instead of duplicating, and a listing that stops appearing
is retired to `status='gone'` — which is how the dashboard can show what's new
and what has likely sold.

Two small pieces of intelligence survive, because they're what make the listing
view usable rather than a firehose:

- **Product resolution** (`engines/resolver.py`) — "EVGA GeForce RTX 3080 FTW3
  Ultra Gaming 10GB" → `RTX 3080`, so you can filter and group by product.
- **Listing guard** (`engines/listing_guard.py`) — flags accessories, multi-item
  lots, and "X for PS5" listings so they don't pollute the view.

## The dashboard

- **Header:** total listings, active, new in 24h, median price, gone (likely sold)
- **Filters:** source, product, title keyword, price range, posted-within,
  hide accessories/lots, include/exclude gone listings
- **Charts:** price distribution, listings by source, listings first-seen per day
- **Table:** sortable, with a link to each listing, plus CSV export of the
  current filter

## Scraper details

| Source | Method | Notes |
|---|---|---|
| **Kijiji** | Internal Apollo GraphQL endpoint (`/anvil/api`), fetched with `cloudscraper` | No browser needed. Sweeps the GPU term list in `kijiji-scraper/config.yaml`; `__NEXT_DATA__` is kept as a page-1 fallback |
| **Craigslist** | Headless browser (Playwright) | Search pages are JS-rendered, so a plain HTTP GET returns an empty shell. Listing dates arrive in three different formats and are normalised to ISO-8601 |

Each scraper is a **self-contained project** with its own config and models, so a
markup change in one marketplace can't break the other.

### Verified behaviour

A single-page Kijiji sweep returns **~259 listings** and resolves them into
products (`RTX 3080` ×27, `RTX 3070` ×18, `RTX 4090` ×14, …). Running it twice
adds **0** new rows and refreshes **259** — deduplication and tracking work.

## Repository layout

```
deal-sniper/
├── app/
│   ├── scrape.py             # CLI: scrape → SQLite
│   ├── dashboard.py          # Streamlit viewer
│   ├── config.py             # scrape depth, lifecycle threshold
│   ├── adapters/sources.py   # marketplace adapters
│   ├── engines/              # resolver + listing_guard
│   ├── models/schema.py      # RawListing
│   ├── db/store.py           # SQLite persistence
│   └── tests/                # 60 tests
├── kijiji-scraper/           # self-contained scraper project
├── craigslist-scraper/       # self-contained scraper project
├── archive/engine-docs/      # the archived valuation engine's documentation
└── requirements.txt (in app/)
```

## Tests

```bash
cd app && python -m pytest -q      # 60 passed
```

Covers the store's persistence and disappearance tracking, the product resolver,
the listing guard, the scrape helpers, and a real render of the dashboard via
Streamlit's `AppTest`.

## Scope and compliance

**In scope:** read-only discovery of public listings, and showing them to a human.

**Out of scope by design:** autonomous purchasing, seller messaging, marketplace
account automation, and credential-gated sources.

