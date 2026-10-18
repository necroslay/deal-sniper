# Deal Sniper — scraper + viewer

Scrapes second-hand computer parts from **Kijiji** (Canada-wide) and
**Craigslist** (GTA), stores them in SQLite, and shows them in a Streamlit
dashboard so you can browse and decide what's worth chasing.

Deliberately small: **scrape → look → decide**. No pricing model in the loop.

On top of that sits an **opt-in ML layer** — a resolver, a listing guard and a
quantile price model, consulted *only* where the deterministic rules abstain,
wired to a real evaluation harness with a CI regression gate. See
[Machine learning layer](#machine-learning-layer-opt-in),
[`PROJECT_STATUS.md`](PROJECT_STATUS.md) for an honest works-vs-doesn't map, and
[`INTERVIEW_PREP.md`](INTERVIEW_PREP.md) for the decisions, numbers and a
stage-by-stage trace of one listing.

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

### How duplicates are handled

Two layers:

1. **Within a run** — the same listing surfaces under several Kijiji search
   terms, so results are collapsed by URL before anything is stored.
2. **Across runs** — `raw_listings` has `UNIQUE(source, source_listing_id)`, so
   each scrape either inserts (counted as *new*) or updates the existing row's
   `last_seen_at` and clears its miss counter (counted as *refreshed*). A
   listing is stored **once, forever**, and accumulates its own history.

That id is the marketplace's own identifier — Kijiji's ad id, or Craigslist's
`data-pid`. Live run: `623` rows = `623` distinct ids = `623` distinct URLs, with
`new: 3, refreshed: 258`.

**Dedup is by *listing*, not by *item*.** A seller who relists, or posts one ad
across ten cities, produces ten rows — they really are ten listings. So the
counts are listing counts, not item counts.

A listing with **no id is refused** rather than stored: filing it under a blank
key would let every subsequent id-less listing overwrite it silently, collapsing
hundreds of rows into one with no error.

> **Retirement is coverage-aware.** A listing is retired after
> `CFG.lifecycle.misses_before_gone` consecutive scrapes without a sighting. A
> shallow sweep can't tell you anything about listings it never reached, so each
> source's scrape depth is remembered and **a source swept less deeply than its
> previous run is skipped entirely** — its unseen listings accrue no misses. So
> `--pages 1` after a 3-page run *pauses* retirement for that source instead of
> falsely retiring everything the deeper run had found. Depth is compared with the
> **previous** run, so normal retirement resumes on the next equal-or-deeper run.

## Scheduled scraping and deal alerts

`python -m app.scrape` scrapes, then looks for **new listings priced well under
comparable asking prices** and posts them to a Discord channel.

```bash
# 1. Create a Discord webhook, then put its URL in .env (gitignored)
copy .env.example .env          # set DISCORD_WEBHOOK_URL=...

# 2. Preview what would be sent, without sending or marking anything alerted
python -m app.scrape --dry-run-alerts

# 3. Run it daily on Windows (Task Scheduler; runs at next boot if the PC was off)
powershell -ExecutionPolicy Bypass -File scripts\register_daily_scrape.ps1 -Time 08:00
```

- **Built for buying by mail.** Every deal is judged on **landed cost** (price +
  shipping), not the sticker price. Kijiji's own `fulfillment` field says whether
  the seller ships (only ~16% of ads state it); text like "pickup only" overrides
  it, and explicit pickup-only listings never alert. Ads that don't say are
  alerted as **"ask if they ship"** with extra risk. Shipping is an estimate from
  `CFG.logistics` unless the seller states a price.
- **What counts as a deal** (`CFG.alerts`): the product has at least 8 comparable
  listings, and either
  - **keep route:** landed cost is at or below their P25 *and* at least 20% under
    the median (30% for laptops, whose comparables are looser), or
  - **flip route:** expected resale (median x 0.92) minus marketplace fees,
    outbound shipping, landed cost and a risk reserve clears $40 profit and 15% ROI.

  Under 35% of the median is treated as a scam/placeholder, not a bargain. Titles
  mentioning parts/repair/broken/locked, bundles or desktops are skipped. This is
  a statement about *asking* prices of comparable listings, not market value.
- **Risk score (0-100):** shipped sight-unseen, scam-shaped price, no/one photo,
  short description, untraceable-payment wording, cash-only seller, a prolific
  seller, shipping not stated. Anything at 55+ ("high") is dropped; the reasons
  are printed in each alert so you know what to ask.
- **Laptops and GPUs.** Laptops are keyed by what sets their price: a dedicated
  GPU (`Laptop RTX 4060`), Apple model + chip (`MacBook Pro M2 Pro`), or family +
  CPU class + RAM bucket (`Lenovo ThinkPad i7 16GB`). Titles that can't be placed
  are left unresolved rather than guessed.
- **One alert per item.** The same seller posting the same product at the same
  price (cross-posts, relists) alerts once, keeping the cheapest.
- **Tune without code:** copy `watchlist.example.yaml` to `watchlist.yaml`
  (gitignored) to set product include/exclude lists, a budget ceiling, margins,
  risk tolerance, resale fees and your real shipping rates. Typos are reported.
- **Each listing alerts once**, and only after Discord accepted the message, so an
  outage retries on the next run. At most 10 per run, and only listings first
  seen in the last 72 hours, so a big backlog cannot flood the channel.
- **Health alerts:** if a source returns nothing or fails, the same channel gets a
  "Scrape problem" message — a silently dead scraper is the usual failure here.
- **History:** every run is recorded in the `scrape_runs` table (counts, status,
  errors) and logged to `logs/scrape.log`. Each listing's price changes are kept in
  `price_history`, so price cuts are visible over time; Kijiji's own "price
  dropped" flag is shown in the alert.
- `--no-alerts` skips all of it. With no webhook configured, nothing is sent.

## The dashboard

- **Header:** total listings, active, new in 24h, median price, gone (likely sold)
- **Filters:** source, product, title keyword, price range, posted-within,
  hide accessories/lots, include/exclude gone listings
- **Charts:** price distribution, listings by source, listings first-seen per day
- **Table:** sortable, with a link to each listing, plus CSV export of the
  current filter

It also **runs the scraper for you**. Pick a source and page depth in the sidebar
and hit **▶ Run scrape now**; the results are shown in place and the view
refreshes. The scrape runs as a **subprocess** — the same `app.scrape` CLI the
terminal uses — for two reasons: the Craigslist adapter drives Playwright's
*sync* API, which dislikes sharing a process with Streamlit, and a slow or
crashing scrape can't take the viewer down with it.

So the dashboard alone is enough: **step 2 above is optional.**

## Machine learning layer (opt-in)

The deterministic engine is the product; the ML layer is a *measured*
enhancement on top of it, not a replacement. It is **off by default** — the
scraper and viewer run with no ML dependencies installed.

```bash
pip install -r app/requirements-ml.txt     # scikit-learn + friends

# 1. Build a labeling sheet from what you scraped.
#    --queue exports only the rows the rules are least sure about.
#    (*.local.csv is gitignored; the committed set is data/labels/deals.csv)
python -m app.ml.dataset --export data/labels/deals.local.csv --queue

# 2. Fill in the product_label / guard_label columns, then measure.
python -m app.eval.harness --task resolver
python -m app.eval.harness --task guard

#    The price model is scored on a temporal split instead, straight from the
#    scraped database (no labels needed).
python -m app.eval.price_harness

# 3. Train and persist the models the pipeline will use.
python -m app.ml.train --task resolver
python -m app.ml.train --task guard
```

Then set `CFG.ml.enabled = True` and the ingestion path uses them automatically.

What it adds, and why it is built this way:

- **Product resolver** — a TF-IDF (word + char n-gram) linear model. On the real
  labeled set it **beats the regex rules** (0.871 vs 0.844 macro F1, 0.867 vs
  0.733 accuracy). The *rules-first hybrid* cannot capture that, though: it only
  rewrites titles the rules abstain on, while the model's wins are on titles the
  rules resolve confidently and wrongly. So the standalone model is the upside
  and the hybrid stays conservative — see [`reports/README.md`](reports/README.md).
- **Listing guard** — a binary `keep`/`reject` model that consumes the rule
  guard's own pattern hits *as features*, so it learns the vocabulary the
  hand-written keyword list misses (`controller`, `carry case`, `charger`). Here
  the hybrid wins outright: 0.849 vs 0.790 macro F1 at higher accuracy.
- **Price model** — LightGBM **quantile regression** predicting the P25/P50/P75
  of comparable asking prices, plus a per-product empirical-quantile baseline to
  beat and an optional **new-retail ceiling**. Validated on a **temporal split**
  (`date_posted`, oldest train / newest test). On today's data the simple
  baseline wins on MAE ($284 vs $404) — the honest result, and the temporal split
  surfaces genuine drift that a random split would have hidden. Framing is strict:
  *underpriced relative to comparable listings*, never a value. See
  [`reports/README.md`](reports/README.md).
- **Evaluation harness** (`app/eval/`) — scores every predictor on a frozen,
  leakage-safe split (splits are keyed on the normalised title, so relists and
  cross-posts cannot leak) and writes a per-class markdown report plus a metrics
  JSON. **CI fails the build** if macro F1 regresses.
- **Active learning** (`app/ml/active_learning.py`) — ranks unlabeled listings by
  how unsure the rules are, so labeling effort goes where it changes the model.

Current numbers, and an honest reading of them, are in
[`reports/README.md`](reports/README.md). The single integration seam is
`app/engines/enrich.py`, which `store_listings` calls once per listing.

## Scraper details

| Source | Method | Notes |
|---|---|---|
| **Kijiji** | Internal Apollo GraphQL endpoint (`/anvil/api`), fetched with `cloudscraper` | No browser needed. Sweeps the GPU term list in `kijiji-scraper/config.yaml`; `__NEXT_DATA__` is kept as a page-1 fallback |
| **Craigslist** | Headless browser (Playwright) | Search pages are JS-rendered, so a plain HTTP GET returns an empty shell. Listing dates arrive in three different formats and are normalised to ISO-8601 |

Each scraper is a **self-contained project** with its own config and models, so a
markup change in one marketplace can't break the other.

### Verified behaviour

A single-page Kijiji sweep returns **~260 listings** and resolves them into
products (`RTX 3080` ×27, `RTX 3070` ×18, `RTX 4090` ×14, …). Running it twice
adds **0** new rows and refreshes **259** — deduplication and tracking work.

## Repository layout

```
deal-sniper/
├── app/
│   ├── scrape.py             # CLI: scrape → SQLite
│   ├── dashboard.py          # Streamlit viewer
│   ├── config.py             # scrape depth, lifecycle, ML paths/thresholds
│   ├── adapters/sources.py   # marketplace adapters
│   ├── engines/              # resolver, listing_guard, enrich (the ML seam)
│   ├── ml/                   # dataset, models, features, retail, training
│   ├── eval/                 # metrics, predictors, harness, price_harness
│   ├── models/schema.py      # RawListing
│   ├── db/store.py           # SQLite persistence
│   └── tests/                # 168 tests
├── kijiji-scraper/           # self-contained scraper project
├── craigslist-scraper/       # self-contained scraper project
├── data/labels/              # hand-labeled seed data (committed)
├── reports/                  # generated eval reports + CI baselines
├── models/                   # trained model binaries (gitignored)
├── archive/engine-docs/      # the archived valuation engine's documentation
├── .github/workflows/ci.yml  # tests + the evaluation regression gate
├── requirements.txt (in app/)
└── requirements-ml.txt (in app/, optional)
```

## Tests

```bash
cd app && python -m pytest -q      # 231 passed
```

Covers the store's persistence and disappearance tracking, the product resolver,
the listing guard, the scrape helpers, Craigslist listing-id parsing, and a real
render of the dashboard via Streamlit's `AppTest` — including clicking
**Run scrape now** and asserting it invokes the scraper CLI against the right
database.

The ML layer adds coverage for the metrics, the leakage-safe split, the
predictors and their hybrid routing, the harness and its regression gate, the
active-learning queue, the guard's threshold selection, and the exact
rules-first behaviour of `enrich` when ML is disabled. The metric, dataset,
harness and enrich tests run **without** scikit-learn; only the model tests skip
when it is absent.

## Scope and compliance

**In scope:** read-only discovery of public listings, and showing them to a human.

**Out of scope by design:** autonomous purchasing, seller messaging, marketplace
account automation, and credential-gated sources.

