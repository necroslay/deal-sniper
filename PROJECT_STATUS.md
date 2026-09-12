# Project Status — Deal Sniper / Resale Valuation Engine

> **Purpose of this file:** a single, honest map of what actually works, what is
> stubbed, and how to prove it. Written so you can answer "what does your system
> do today?" in an interview without overstating anything.
>
> **Last verified:** 2026-09-11, by running the code (not by reading intent docs).

---

## 1. One-paragraph summary

A comp-driven resale valuation pipeline for second-hand marketplaces. It scrapes
live listings (Kijiji, Craigslist), resolves each title to a canonical product,
aggregates **sold** comps into an outlier-trimmed price band, computes a Fair
Market Value and an Expected Resale Value net of fees, then scores the deal and
gates it for alerting. Every decision is persisted with its full payload as a
future ML training row. **Two of four sources are live; alerting and the ML layer
are not yet wired.**

---

## 2. Component status

Legend: ✅ works end-to-end · ⚠️ works with caveats · 🟡 partial / stub · ❌ not built

| Component | Status | Evidence / caveat |
|---|---|---|
| Kijiji scraper | ✅ | Live: 754 listings ingested in the last run |
| Craigslist scraper | ✅ | Live: 48 listings, via Playwright (see §5) |
| eBay scraper | 🟡 | Code exists, adapter loads; **never produced listings** — gated/anti-bot |
| Facebook scraper | 🟡 | Code exists, adapter loads; `check_health()` returns `unknown` — login-gated |
| Product resolver | ✅ | Regex waterfall, 52 products, unit tested |
| Condition classifier | ✅ | Keyword+weight, unit tested |
| Comp engine (IQR bands) | ✅ | Unit tested incl. explicit outlier fixture |
| FMV / ERV / Deal score | ✅ | Unit tested |
| SQLite persistence | ✅ | `app/resale_engine.db`, 802 listings / 869 decisions / 265 comps |
| FastAPI service | ⚠️ | Endpoints work, but **`/feedback` only logs — does not persist** |
| Streamlit dashboard | ⚠️ | `dashboard.py`; deps (streamlit/plotly/pandas) verified installed and DB path resolves — but not launched this session |
| Discord / Telegram notifiers | 🟡 | Coded; **no webhook configured, never fired live** |
| Celery + Redis scheduling | 🟡 | `scheduler/tasks.py` functions are documented **no-ops** |
| PostgreSQL / SQLAlchemy | ⚠️ | `migrations.sql` + async session exist; pipeline actually uses **SQLite** |
| ML price model (LightGBM) | ❌ | Not started |
| MLflow backtesting | ❌ | Not started |

---

## 3. Verified numbers (last full pipeline run)

From `app/resale_engine.db`, queried directly, **after the fixes described in §6**:

```
raw_listings          698      (kijiji 652, craigslist 46)
valuation_decisions   867
sold_comps            265      (seeded baseline table, not live sold data)
decisions with FMV>0  122      (14%)
alert_sent = 1          2
```

Confidence distribution across 867 decisions:

```
SUPPRESS          527   (61%)
REVIEW            263   (30%)
ALERT_CAUTION      77    (9%)
ALERT_IMMEDIATE     0    (0%)
```

**Read this honestly:** the system suppresses 91% of listings. That is the
*correct* behaviour for a system whose comps are a seeded baseline table covering
26 products — most listings have no comp coverage for their exact product, so
`fmv_cad = 0` and they are suppressed. See §7.

---

## 4. How to run it

```bash
# Unit tests (no network)
cd app && python -m pytest -q                        # 151 passed
cd craigslist-scraper && python -m pytest tests -q   # 39 passed

# Full pipeline: seed comps -> scrape live -> value -> persist -> report
cd app && python run_full_pipeline.py

# API
uvicorn app.main:app --port 8000

# Review UI
streamlit run app/dashboard.py
```

`run_full_pipeline.py` **deletes `app/resale_engine.db` on every run** so each
report is self-contained. That is deliberate for a demo script, but it means the
DB is a scratch artifact, not an accumulating store.

**Verified environment** (checked directly, not assumed):

```
Python 3.11.4
playwright    present          Chrome installed (channel="chrome" resolves)
bs4, cloudscraper, requests, fastapi, sqlalchemy   present
streamlit, plotly, pandas                          present   (dashboard deps)
celery, lightgbm, xgboost, mlflow, sklearn         NOT installed
```

Note the last line: the ML/scheduling dependencies aren't installed because
those layers aren't built yet — which is consistent with §2 rather than a broken
setup.

---

## 5. Scraper reality check (the part most worth explaining)

### Kijiji — no browser needed
Kijiji is a Next.js app. Listing data is available two ways: embedded in the
`__NEXT_DATA__` script tag, and via an internal Apollo GraphQL endpoint at
`/anvil/api`. **The GraphQL endpoint is the primary path** because the
`__NEXT_DATA__` route is capped (the code comments note 40); GraphQL paginates
deeper and is used for every page, with `__NEXT_DATA__` kept as a page-1 fallback.
`cloudscraper` is used instead of `requests` to get past Cloudflare.

Depth is driven by `search.max_listings` and `search.search_terms` in
`config.yaml`. Note that `default_pages` is **derived, not configured**:
`default_pages = max(1, max_listings // listings_per_page)` — so raising
`max_listings` automatically widens pagination. `scrape_terms()` walks the term
list and unions the results, de-duplicating by URL. Sweeping nine GPU terms
instead of one broad query is what took the corpus from ~183 to 754 listings.

### Craigslist — a browser is mandatory
This is the interesting one, and the story is:

1. **Attempt 1 — plain HTTP + BeautifulSoup.** Returns an ~8 KB JavaScript shell
   with **zero result rows**. The old selector (`li.cl-static-search-result`)
   matched nothing because the *markup itself changed* to `div.cl-search-result`.
2. **Attempt 2 — the RSS escape hatch.** `?format=rss` used to expose search
   results as a feed. It now returns **404 / 403**. Dead end.
3. **Attempt 3 — headless browser.** Playwright renders the page, executes the
   JS, and the real DOM appears. This is the shipping path.

Implementation notes worth knowing:
- `craigslist-scraper/browser.py` wraps Playwright's **sync** API and keeps
  **one browser process alive** across page loads, so a 3-page sweep doesn't pay
  Chromium start-up three times.
- It launches with `channel="chrome"` — the already-installed Chrome — and
  **falls back to bundled Chromium** if that channel is unavailable. This avoids
  forcing a ~150 MB `playwright install chromium` download on a machine that
  already has a browser.
- **Images are blocked at the context level** (route interception): they're
  irrelevant to parsing and dominate page weight.
- Two silent data bugs were found by testing and fixed: `listing_id` was being
  scraped from a `/(\d+)\.html` URL pattern Craigslist no longer emits (now read
  from the card's `data-pid`), and posted-dates were being dropped. Dates arrive
  in three shapes on the same site — relative (`5h ago`), short numeric (`9/10`
  **or** `09-10` depending on session), and month-name (`Sep 10`) — all three are
  now normalised to ISO-8601.

**Result: 54/54 listings acquired dates, up from 2/54.**

### eBay — completed-sales access is gated (verified 2026-09-12)

The eBay scraper is the *only* path in this repo that has never produced a
listing, and the reason is now pinned down by direct testing rather than
inferred:

| Access attempt | Result |
|---|---|
| `requests` GET, **sold** search (`LH_Sold=1&LH_Complete=1`) | HTTP 200, but a **"Sign in or Register"** interstitial — zero listing markup |
| `requests` GET, **active** search | HTTP **403**, **"Error Page"** |
| Headless **real Chrome** (Playwright, `channel="chrome"`) on the sold URL | **"Error Page"**, 1.8 KB, zero rows |
| PriceCharting search (documented fallback) | HTTP 403, **Cloudflare "Just a moment…"** — not cleared by a real-Chrome render either |
| Swappa search (documented fallback) | HTTP 200, GPU product pages exist — but **asking prices only; no sold/average prices exposed** |

**Conclusion.** There is no reachable, free, *automated* source of real GPU
**completed-sales** data today. eBay gates completed listings behind
authentication and detects headless browsers; the games/consoles fallback
(PriceCharting) sits behind a Cloudflare challenge; and the one reachable
marketplace (Swappa) exposes only asking prices, which `DECISIONS.md` D1
explicitly forbids using as ground truth.

This is why §2 lists eBay as 🟡, and it means the binding constraint is **comp
*data access***, not scraper code. Compliant options: (a) a provider interface
fed by real comps the operator supplies (CSV / manual entry), (b) eBay's official
APIs — Browse is free, but Marketplace Insights for sold data requires approval —
with operator-supplied credentials, or (c) a paid sold-comp provider. Bypassing
the anti-bot measures was **rejected**: it contradicts the compliance stance in
`copilotinstructions.md` §10.

---

## 6. Known discrepancies and tech debt

Say these before an interviewer finds them — they're all defensible choices or
known next steps, not mysteries.

1. **`valuation_decisions` (867) references listings that aren't in `raw_listings`
   (698).** Verified cause: **the skip path writes a decision but not the listing
   row.** When a listing is rejected by the pre-filter or the listing guard,
   `pipeline.process()` calls `insert_decision()` and returns early *without*
   calling `insert_listing()`. The gap is exactly the 169 rejected listings
   (103 guard + 63 price-too-low + 3 service). No duplicate decisions exist — an
   earlier draft of this file blamed missing dedup, which was **wrong**.

   Impact: `migrations.sql` declares
   `listing_id UUID NOT NULL REFERENCES listings(id)`, so migrating to Postgres
   would fail on all 169 rows. **Fix:** persist the listing before writing its
   decision (rejected listings are worth keeping for audit anyway).

2. ~~**The alert gate is implemented twice.**~~ **FIXED.** The rule now lives in
   one place, `valuation/gate.py::passes_alert_gate`, and both the pipeline and
   the store call it. Covered by `tests/test_gate.py::TestStoreUsesTheSharedGate`,
   which asserts `alert_sent` agrees with the gate for every case.

   *Correction retained:* an earlier draft claimed "9 rows alert but only 2 reach
   `ALERT_IMMEDIATE`". That compared a *gate-pass* count against a *tier* count —
   `ALERT_IMMEDIATE` is the top tier, and `ALERT_CAUTION` rows pass the gate
   legitimately. The two copies never actually disagreed.

3. **SQLite vs PostgreSQL.** `app/db/session.py` and `migrations.sql` describe the
   Postgres target, but `run_full_pipeline.py` and `db/store.py` use SQLite. The
   Postgres path is aspirational, not exercised.

4. **Celery is not wired.** The task functions and `BEAT_SCHEDULE` exist, but each
   task body logs `"... not yet wired — noop"`. There is no `@celery.task`
   decorator and no `celery_app`. Scheduling today = running a script by hand.

5. **Comps are seeded, not sold.** All 265 comps come from a hardcoded
   `BASELINE_COMPS` dict in `run_full_pipeline.py`, dated 60 days back. No eBay
   *sold* data has ever been ingested. This is the biggest gap between the
   project's stated principle ("comp-driven, not AI-guessed") and its behaviour.

6. **`/feedback` doesn't persist.** It logs and returns `{"status":"recorded"}`.
   The `feedback_events` table exists in the Postgres schema only.

7. **`condition_confidence` is flattened in FMV.** `compute_fmv()` sets it to
   `1.0` whenever the condition isn't `"unknown"`, discarding the classifier's
   graded confidence — even though `compute_deal_score()` is handed the real value.

8. ~~**Comp bands polluted by product variants.**~~ **FIXED.** `get_sold_comps()`
   now matches on an exact `product_key` column populated at insert time, instead
   of `title LIKE '%name%'`. Legacy databases are migrated and backfilled on
   connect. Covered by `tests/test_store.py::TestCompLookupIsExact`.

9. ~~**Product resolution false positives.**~~ **FIXED.** New
   `engines/listing_guard.py` rejects accessories, "X for PS5", components inside
   host items, and multi-item lots *before* valuation. Covered by
   `tests/test_listing_guard.py`.

10. ~~**`ItemAdjustments` dead config.**~~ **FIXED.** New
    `engines/adjustments.py` detects missing box/charger, battery and damage
    deficiencies from the listing text (negation-aware, one deduction per family)
    and the pipeline passes them to `compute_fmv`. Covered by
    `tests/test_adjustments.py`.

### Bugs found *while fixing the above*

11. **Registry gaps made products unvaluable.** `GTX 1070/1080/1080 Ti/1660/1660
    Super`, `RTX 2070`, `RX 580/6800/6800 XT` were in the comp seed table but
    **not** in `PRODUCT_REGISTRY`, so they fell through to the Tier-3 fallback and
    the exact-match comp lookup could never find them. Added to the registry.
    *This was a regression introduced by fix #8* — under the old `LIKE` match the
    fallback happened to still work.

12. **Registry ordering bug.** `PlayStation 5` was listed before `PS5 Pro`, and
    because resolution returns the *first* alias match, `"PS5"` swallowed
    `"PS5 Pro"` — collapsing both into one product. Variants now precede their
    base model. Guarded by
    `tests/test_resolver.py::test_every_standard_name_resolves_to_itself`, an
    invariant that checks every registry entry resolves to itself.

13. **`max_days_old` was accepted and ignored** in `get_sold_comps()` — the
    parameter did nothing. It is now applied as a `sold_date >=` cutoff.

---

## 7. The bottleneck (say this out loud)

**Comp coverage is now the binding constraint.** Only 122 of 867 decisions (14%)
produced a non-zero FMV, because the baseline comp table covers 26 products while
the scrapers return a long tail beyond that watchlist. Raising the alert count is
a **data** problem (real sold comps, wider product registry), not a
scraper-throughput problem. See `DECISIONS.md` §D6.

**Precision is no longer the problem it was.** The audit that produced
`WORKED_EXAMPLE.md` found that 6 of the 9 alerts passing the gate were junk
(a cable, a game, an accessory, two lots) — **≈33% precision**. Those defects are
now fixed (§6.8–§6.13), and the gate passes **2 alerts, both legitimate RTX 4090
listings**. See `WORKED_EXAMPLE.md` §8–§11.

That ordering mattered: fixing correctness *before* chasing volume avoided
multiplying the false-positive rate.
