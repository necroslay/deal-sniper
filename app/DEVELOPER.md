# Developer Documentation -- Resale Valuation Engine

A comp-driven resale valuation engine that evaluates marketplace listings (eBay, Kijiji, Facebook Marketplace, Craigslist) to identify profitable resale deals. Built with Python 3.11+, FastAPI, Pydantic v2, and SQLite/PostgreSQL.

> **See also (repository root):** `PROJECT_STATUS.md` for a verified
> works-vs-stubbed map and known tech debt, and `DECISIONS.md` for the rationale
> behind each design choice (with rejected alternatives). This document is the
> module-by-module reference; those two are the *why* and the *what's real*.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start](#2-quick-start)
3. [Project Structure](#3-project-structure)
4. [Configuration System](#4-configuration-system)
5. [Data Models & Schema](#5-data-models--schema)
6. [Module Reference](#6-module-reference)
7. [Valuation Math](#7-valuation-math)
8. [API Reference](#8-api-reference)
9. [Testing Guide](#9-testing-guide)
10. [Deployment](#10-deployment)
11. [Development Guide](#11-development-guide)
12. [Glossary](#12-glossary)

---

## 1. Architecture Overview

The system is a **multi-stage pipeline**:

```
Marketplace -> Source Adapter
  -> Pre-Filter (skip? -> zero output)
  -> Product Resolution + Condition Classification
  -> Comp Engine (IQR outlier removal, percentiles)
  -> Fair Market Value (median * M_condition +/- adj)
  -> Expected Resale Value (FMV - fees - shipping - reserve)
  -> Deal Score (weighted components - penalties)
  -> Alert Gate (profit >= $50 AND margin >= 15%?)
  -> Notifier (Discord / Telegram)
```

**Key design principles:**
- **Comp-driven, not ML-driven.** Uses actual sold market data.
- **Configuration in one place.** Everything in `config.py`.
- **IQR outlier removal.** Prices outside Q1-1.5*IQR / Q3+1.5*IQR stripped.
- **All prices in CAD.** Canadian dollars only.

---

## 2. Quick Start

### Prerequisites: Python 3.11+, pip

```bash
cd app
pip install -r requirements.txt
python -m pytest
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### First API Call
```bash
curl -X POST http://localhost:8000/valuate \
  -H "Content-Type: application/json" \
  -d '{"source":"kijiji","title":"RTX 3080",\
    "price_cad":550,"url":"https://kijiji.ca/test",\
    "comps":[{"source_listing_id":"ebay_1",\
      "title":"RTX 3080","sold_price_cad":800,\
      "sold_date":"2026-07-15T12:00:00+00:00",\
      "condition":"used_good"}]}'
```

**Important:** Sold comp dates must be timezone-aware (`+00:00` or `Z`). Naive datetimes produce FMV=$0.

---

## 3. Project Structure

```
app/
+-- adapters/sources.py       # Source adapters
+-- alerting/notifier.py      # Discord & Telegram alerts
+-- db/
|   +-- migrations.sql        # PostgreSQL schema (6 tables)
|   +-- session.py            # SQLAlchemy async session
|   +-- store.py              # SQLite persistence
+-- engines/
|   +-- comp_engine.py        # Comp aggregation & IQR
|   +-- condition.py          # Condition classifier
|   +-- resolver.py           # Product normalizer
|   +-- listing_guard.py      # Rejects accessories/lots before valuation
|   +-- adjustments.py        # Item-level deductions (missing box, damage)
|   +-- lifecycle.py          # Inferred comps from listing disappearance
+-- models/schema.py          # Pydantic v2 models
+-- pipeline/ingest.py        # Pipeline orchestrator
+-- pipeline/lifecycle_cycle.py # Observe/retire/infer cycle (shared)
+-- valuation/
|   +-- fair_value.py         # FMV computation
|   +-- realize.py            # ERV computation
|   +-- score.py              # Deal score computation
+-- tests/ (15 files, 180 tests)
+-- config.py                 # Central config (16 sections)
+-- main.py                   # FastAPI app (5 endpoints)
+-- dashboard.py              # Streamlit dashboard
+-- run_full_pipeline.py      # Bulk runner
+-- docker-compose.yml        # PostgreSQL + Redis + API
```

---

## 4. Configuration System

**File:** `config.py`

Singleton pattern using Python dataclasses. Every numeric constant reads from `CFG`.

```python
from app.config import CFG
CFG.currency  # "CAD"
CFG.country   # "CA"
CFG.platform_fees.ebay_canada_pct  # 0.1325
CFG.condition_multipliers.used_good  # 0.82
```

### Sub-configs

| Sub-config | Key fields | Purpose |
|---|---|---|
| platform_fees | ebay 13.25%+$0.45, others 0% | Platform fee rates |
| shipping_costs | GPU $20, laptop $25, console $18 | Category shipping |
| condition_multipliers | new 1.05, used 0.82, parts 0.30 | FMV multipliers |
| return_reserve | electronics 5%, default 3% | Return risk buffer |
| travel_costs | $10 + $0.75/km after 5km free | Pickup friction |
| holding_costs | $20/hr labor, 8%/yr capital | Labor + capital |
| item_adjustments | missing charger -$30, scratch -$50 | Item adjustments |
| score_weights | margin 0.50, liquidity 0.20, urgency 0.15, conf 0.15 | Weights |
| penalties | scam -20, bundle -25, price -30 | Risk subtractions |
| comp | window 90d, min 5 comps, IQR 1.5x | Aggregation params |
| alert_gate | profit $50, margin 15%, min 5 comps | Alert thresholds |
| scraper_sources | pages/source, delays 1.5-3s | Scrape config |
| lifecycle | 3 misses, 5% haircut, inferred off | Disappearance-inferred comps |

### Changing config at runtime

```python
from app.config import CFG
CFG.comp.min_comps_for_valuation = 3
```

The autouse `reset_config` fixture resets CFG before every test.

---

## 5. Data Models & Schema

**File:** `models/schema.py` -- Pydantic v2 models.

### Enums

| Enum | Values |
|---|---|
| ConditionGrade | brand_new, open_box_like_new, used_good, acceptable, for_parts, unknown |
| ConfidenceLevel | ALERT_IMMEDIATE, ALERT_CAUTION, REVIEW, SUPPRESS |
| Platform | ebay, kijiji, facebook, craigslist |

### RawListing
Raw marketplace listing. Fields: source (Platform), source_listing_id (str, aliased listing_id), title (str), price_cad (float), url (str), location (Optional[str]), description (Optional[str]), date_posted (Optional[str]), image_urls (list[str]), raw_payload (Optional[dict]), scraped_at (str auto).

### SoldComp
Sold listing for comp valuation. Fields: source (Platform, default ebay), source_listing_id (str), title (str), sold_price_cad (float), sold_date (str, must be timezone-aware), condition (str).

### CanonicalProduct
Resolved product: standard_name (str), category (str, e.g. gpu/console), resolved_from (str).

### CompBand
Stats: count (int), median_price_cad (float), mean_price_cad (float), p25_price_cad (float), p75_price_cad (float), min/max_price_cad (float), std_dev (float), window_days (int default 90).

### FairValueEstimate
FMV: fmv_cad (float), fmv_confidence_interval (tuple[float,float]), condition (str), condition_confidence (float 0-1), comps_used (int), comp_band (Optional[CompBand]).

### RealizedValue
Net after costs: fmv_cad (float), platform_fees_cad (float), shipping_cost_cad (float), return_reserve_cad (float), erv_cad (float).

### DealScore
Score 0-100: score (float), margin/liquidity/urgency/confidence_score (float), penalty_total (float), expected_profit_cad (float), margin_pct (float), confidence_level (ConfidenceLevel), asking_price_cad/fmv_cad/erv_cad (float), risk_flags (list[str]), recommendations (list[str]).

### ValuationOutput
Complete output: raw_listing + canonical_product + fair_value + realized_value + deal_score + pipeline_version (default "1.0.0").

### FeedbackEvent
Feedback: decision_id (str), outcome (str: Good Lead/Bad Match/Too Risky/Already Sold/Bought/Resold), actual_sold_price_cad (Optional[float]).

---

## 6. Module Reference

### 6.1 engines/resolver.py -- Product Resolver

Maps raw titles to canonical product names. Waterfall:
- Tier 1: Regex match against known aliases (43 products)
- Tier 2: Fuzzy substring match (SequenceMatcher > 0.8)
- Tier 3: Fallback -- returns title as-is

Products: NVIDIA RTX 50/40/30-series, AMD RX 7000-series, Ryzen CPUs, PS5/Switch, MacBooks, iPhones.
Category guessed via _guess_category(): gpu, cpu, console, laptop, mobile, memory, storage, electronics.

```python
def resolve_product(title: str) -> CanonicalProduct
```

### 6.2 engines/condition.py -- Condition Classifier

Keyword-based grading from title + description.

| Grade | Sample keywords (weight) |
|---|---|
| brand_new | "brand new" 1.0, "BNIB" 0.95, "sealed" 0.95 |
| open_box_like_new | "open box" 1.0, "like new" 0.95, "mint" 0.95 |
| used_good | "good condition" 0.90, "used" 0.80, "tested" 0.85 |
| acceptable | "acceptable" 0.70, "scratched" 0.50 |
| for_parts | "for parts" 1.0, "broken" 0.95, "as is" 0.85 |

Confidence boosted by multiple matches (+0.10), long desc (+0.20), never mined (+0.05). Penalized by uncertainty (-0.20), short desc (-0.15).

```python
def classify_condition(title, description="") -> ConditionResult
```

### 6.3 engines/comp_engine.py -- Comp Engine

Aggregates sold listings into price bands with IQR outlier removal.

Algorithm: filter by window (90d), sort prices, compute Q1/Q3/IQR, remove outliers outside [Q1-1.5*IQR, Q3+1.5*IQR], fall back if all removed, compute median/mean/P25/P75/min/max/std_dev.

```python
def compute_comp_band(comps, standard_name, condition="unknown", window_days=None) -> CompBand
def has_sufficient_comps(band) -> bool
def comp_volume_score(band) -> float
```

**Requirement:** `sold_date` must be timezone-aware. Naive datetimes are silently dropped.

### 6.4 valuation/fair_value.py -- Fair Market Value

**FMV = median_price x M_condition x M_trend +/- sum(adjustments)**

> **Note:** `adjustments` is populated by `engines/adjustments.py`, which detects
> missing box/charger, battery and cosmetic-damage deficiencies from the listing
> text and maps them to `CFG.item_adjustments`. Detection is negation-aware
> ("no scratches" is not penalised) and applies at most one deduction per family.

M_condition: brand_new=1.05, open_box=0.95, used_good=0.82, acceptable=0.68, for_parts=0.30, unknown=1.0.
M_trend default 1.0. CI = [P25 x M_condition, P75 x M_condition].
Empty comps -> FMV=$0, comps_used=0.

```python
def compute_fmv(comp_band, condition, trend_multiplier=1.0, adjustments=None) -> FairValueEstimate
```

### 6.5 valuation/realize.py -- Expected Resale Value

**ERV = FMV - platform_fees - shipping - return_reserve**

Fees: eBay 13.25%+$0.45, Kijiji/FB/CL 0%. Return reserve: electronics 5%, default 3%.

```python
def compute_erv(fmv_cad, platform=ebay, shipping_cost_cad=0.0, category="electronics") -> RealizedValue
```

### 6.6 valuation/score.py -- Deal Score

**Score = 0.50*S_margin + 0.20*S_liquidity + 0.15*S_urgency + 0.15*S_confidence - penalties**

S_margin = min(100, max(0, profit/ERV*100))
S_liquidity = 0 if comps < 5, 100 if >= 50, linear between
S_urgency: <=1d=90, <=3d=75, <=7d=60, <=14d=40, <=30d=25, >30d=10
S_confidence: base (90/80/65/40 by comp count) x condition_confidence

Profit = ERV - asking - travel - holding. Travel: $0 <=5km, $10+($dist-5)*$0.75 beyond. Holding: 2h*$20/hr + FMV*0.08/12.

Penalties: scam_risk -20, condition_uncertainty -15, vague_bundle -25, unrealistic_price -30, travel>30km -10, travel>10km -5.

Confidence: >=0.85 + comps>=5 + margin>=20% -> ALERT_IMMEDIATE; >=0.65 + comps>=5 -> ALERT_CAUTION; >=0.40 -> REVIEW; else -> SUPPRESS.

```python
def compute_deal_score(asking_price_cad, fair_value, realized_value, comp_band, ...) -> DealScore
```

### 6.7 pipeline/ingest.py -- Ingestion Pipeline

Central orchestrator. Stages: 1) pre-filter (price/title/contact/trade/service checks), 2) product resolution, 3) condition classification, 4) comp aggregation (pre-computed, from list, from DB, or empty), 5) FMV, 6) ERV, 7) deal score.

Alert gate: profit >= $50 AND margin >= 15% AND confidence in (ALERT_IMMEDIATE, ALERT_CAUTION) AND comps_used >= 5.

Pre-filter skip reasons: price_too_low (<$10), price_too_high (>$100k), title_too_short (<5 chars), contact_for_price, trade_post, service_not_product.

```python
pipeline = IngestionPipeline(store=Store("resale_engine.db"))
output = pipeline.process(raw_listing, sold_comps=comps)
if pipeline.should_alert(output): pipeline.notify(output)
```

### 6.8 adapters/sources.py -- Source Adapters

Abstract adapter pattern wrapping external scraper projects (kijiji-scraper, ebay-scraper, facebook-marketplace-scraper, craigslist-scraper). Dynamic import from sibling directories.

Registration: @register_source(Platform.X). Retrieval: get_adapter(Platform.X). Each adapter: search() -> list[RawListing], fetch_detail(url), check_health().

Craigslist: search pages are JavaScript-rendered (a plain HTTP GET returns an empty shell), so the adapter drives a headless browser via `craigslist-scraper/browser.py` (Playwright, preferring the locally-installed Chrome).

### 6.9 alerting/notifier.py -- Alert Dispatchers

DiscordNotifier: color-coded embeds (green/yellow/orange/red by confidence), fields for asking/FMV/profit/score/condition/location/source/comps, thumbnail, recommendations.
TelegramNotifier: Markdown message with key metrics.
Notifier (composite): sends to all configured channels. Returns True if any succeeded.

```python
notifier = Notifier(discord_url="...", telegram_token="...", telegram_chat="...")
notifier.send_alert(output)
```

### 6.10 db/store.py -- SQLite Store

Raw SQL persistence. Auto-creates tables: raw_listings, valuation_decisions, sold_comps, source_health. UNIQUE(source, source_listing_id) prevents duplicates.

```python
store = Store("resale_engine.db")
store.connect()
store.insert_listing(raw_listing)  # True/False
store.insert_sold_comp(comp)       # True/False
store.insert_decision(output)      # UUID string
store.get_sold_comps("RTX 3080")  # list of SoldComp
store.record_source_success/failure(platform)
store.close()
```

### 6.11 db/session.py -- SQLAlchemy Session

Async session factory for PostgreSQL. Default URL: `postgresql+asyncpg://postgres:postgres@localhost:5432/resale_engine`. Override via DATABASE_URL env var.

Functions: get_session() (FastAPI async generator), create_session() (standalone), init_db() (runs migrations.sql).

### 6.12 db/migrations.sql -- PostgreSQL Schema

5 tables: listings (UUID PK, JSONB payload, TIMESTAMPTZ), canonical_products (standard_name UNIQUE, TEXT[] aliases), market_comps (FK to canonical), valuation_decisions (FK to listings, JSONB payload), feedback_events (FK to decisions, CHECK outcome), source_health (UNIQUE source). Includes pg_trgm, uuid-ossp extensions, indexes. Run: `psql -d resale_engine -f db/migrations.sql`

### 6.13 main.py -- FastAPI Application

Endpoints: GET / (service info), GET /health (12 config sections, currency, country), POST /valuate (runs pipeline, accepts comps or comp_band), POST /feedback (records outcome), POST /alert/{id} (re-send stub). OpenAPI docs at /docs. ValuationRequest: source, title, price_cad (required), url, location, description, date_posted, listing_id, image_urls, travel_distance_km, comps (list of dicts), comp_band (pre-computed dict).

### 6.14 dashboard.py -- Streamlit Dashboard

Filters: product, confidence level, score range. Visuals: score histogram (Plotly), deals dataframe, top 20 by profit, source health monitor. Summary: total decisions, alerts sent, avg score, unique products/sources. Refresh button. Run: `streamlit run dashboard.py`

### 6.15 run_full_pipeline.py -- Bulk Pipeline Runner

Seeds 260 baseline comps across 27 products, scrapes ~200 Kijiji listings, processes all through pipeline, outputs breakdown: unique products, condition distribution, confidence levels, alertable deals, price stats, top profitable deals, DB stats. Requires kijiji-scraper project at ../kijiji-scraper/.

---

## 7. Valuation Math

### Complete Formula Chain

1. **CompBand**: median(clean_prices_after_IQR), P25/P75 by integer division
2. **FMV** = median x M_condition x M_trend + sum(adjustments)
3. **FMV CI** = [P25 x M_condition x M_trend + adj, P75 x M_condition x M_trend + adj]
4. **ERV** = FMV - (FMV x fee_rate) - shipping - (FMV x return_reserve_pct)
5. **Expected Profit** = ERV - asking - travel_cost - holding_cost
6. **Margin %** = Profit / ERV x 100
7. **Deal Score** = 0.50*S_margin + 0.20*S_liquidity + 0.15*S_urgency + 0.15*S_confidence - penalties

### Worked Example: RTX 3080 (used_good)

Inputs: 12 comps, median=$825, used_good (M=0.82), asking=$550, Kijiji (0% fees), local pickup, posted <=1d ago.

| Step | Calc | Result |
|---|---|---|
| FMV | $825 x 0.82 | $676 |
| CI | [$780x0.82, $870x0.82] | [$640, $713] |
| Return reserve | $676 x 5% | $34 |
| ERV | $676 - $0 - $0 - $34 | $643 |
| Holding | (1+0.5+0.5)x$20 + $676x0.08/12 | $45 |
| Profit | $643 - $550 - $0 - $45 | $48 |
| Margin | $48/$643 | 7.5% |
| S_margin | min(100, max(0, 48/643*100)) | 7.5 |
| S_liquidity | linear(12, min=5, max=50) | ~22 |
| S_urgency | <=1d old | 90 |
| S_confidence | 80 (>=10 comps) x 0.85 (condition conf) | 68 |
| Deal score | 0.50x7.5 + 0.20x22 + 0.15x90 + 0.15x68 - 0 | 22.2 |
| Confidence | avg ~0.70, comps>=5, margin<20% | REVIEW |

---

## 8. API Reference

### GET /

Service info. Response: `{"service": "Resale Valuation Engine", "version": "1.0.0", "status": "ok"}`

### GET /health

Health check. Response: `{"status": "healthy", "currency": "CAD", "country": "CA", "config_sections": ["platform_fees", "shipping_costs", "condition_multipliers", "return_reserve", "travel_costs", "holding_costs", "item_adjustments", "score_weights", "penalties", "comp", "alert_gate", "scraper_sources"]}`

### POST /valuate

Run full pipeline on a listing. Request body:

```json
{
  "source": "kijiji",
  "title": "RTX 3080 Used Good",
  "price_cad": 550.0,
  "url": "https://kijiji.ca/test",
  "location": "Toronto",
  "description": "Used for gaming",
  "date_posted": "2026-08-27T12:00:00+00:00",
  "listing_id": "kijiji_1",
  "image_urls": [],
  "travel_distance_km": 5.0,
  "comps": [
    {"source_listing_id": "ebay_1", "title": "RTX 3080",
     "sold_price_cad": 800.0, "sold_date": "2026-07-15T12:00:00+00:00",
     "condition": "used_good"}
  ],
  "comp_band": null
}
```

Alternative: provide pre-computed `comp_band` dict instead of `comps` list.
Response: full ValuationOutput JSON. Error: 400 for invalid source.

### POST /feedback

Record user feedback. Request body:

```json
{"decision_id": "abc-123", "outcome": "Good Lead", "actual_sold_price_cad": null, "notes": null}
```

Response: `{"status": "recorded", "decision_id": "abc-123", "outcome": "Good Lead"}`

Outcomes: Good Lead, Bad Match, Too Risky, Already Sold, Bought, Resold.

### POST /alert/{decision_id}

Re-send alert for previous decision. Currently returns `{"status": "not_implemented"}`.

---

## 9. Testing Guide

### Run all tests

```bash
cd app
python -m pytest
python -m pytest -v --tb=short  # verbose with short traceback
```

180 tests across 15 files.

### Run specific file

```bash
python -m pytest tests/test_resolver.py -v
python -m pytest tests/test_fair_value.py -v --tb=short
```

### Test summary

| File | Tests | Validates |
|---|---|---|
| test_adjustments.py | 14 | Item deductions: detection, negation guard, no double-counting |
| test_comp_engine.py | 8 | Median, IQR outlier removal, empty/full comps, volume scoring |
| test_condition.py | 10 | All 6 grades, confidence boosts/penalties, unknown fallback |
| test_config.py | 8 | All config defaults, weights sum check |
| test_fair_value.py | 7 | FMV formula, all multipliers, adjustments, trend, empty fallback |
| test_gate.py | 9 | Alert gate conditions; store's `alert_sent` agrees with the gate |
| test_listing_guard.py | 29 | Accessory/lot/host-item rejection; legitimate listings still pass |
| test_models.py | 11 | Pydantic validation, required fields, enums, feedback outcomes |
| test_pipeline.py | 6 | E2E RTX 3080/4090/PS5, unknown product, alert gate, pre-computed band |
| test_realize.py | 6 | eBay fees, 0% fee platforms, shipping, return reserve by category |
| test_resolver.py | 17 | All 3 tiers, fuzzy/garbage/empty input, registry ordering invariant |
| test_score.py | 9 | Good/bad deals, risk penalties, travel, holding cost, bounds |
| test_store.py | 17 | Exact comp matching, `product_key` migration, `max_days_old` |

### Fixtures (tests/conftest.py)

- `reset_config` (autouse): Resets CFG to defaults before every test
- `rtx_3080_sold_comps`: 20 deterministic comps
- `rtx_3080_outlier_comps`: 12 comps with extreme outliers
- `rtx_4090_sold_comps`: 15 deterministic comps
- `ps5_sold_comps`: 10 deterministic comps
- `rtx_3080_listing`: Kijiji listing at $750
- `rtx_4090_listing`: Facebook listing at $2000
- `ps5_listing`: Craigslist listing at $380

### Adding tests

- Place in appropriate file in tests/ following existing patterns
- Use shared fixtures from tests/conftest.py
- Use pytest.approx() for float comparisons
- Reset CFG via autouse fixture if modifying config

### Smoke test checklist

- [ ] `python -m pytest` -- all 180 tests pass
- [ ] `python -c "from app.main import app"` -- app loads
- [ ] POST /valuate with timezone-aware comps returns FMV > 0
- [ ] POST /health returns healthy
- [ ] POST /valuate with invalid source returns 400
- [ ] Store(":memory:").connect() succeeds

---

## 10. Deployment

### Docker Compose (PostgreSQL + Redis + API)

```bash
docker-compose up --build
```

Services: redis (port 6379), postgres (port 5432, db: resale_engine), api (port 8000). Environment: DATABASE_URL, REDIS_URL.

### SQLite Standalone (default)

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

No database server needed. SQLite DB created at app/resale_engine.db.

### Switching to PostgreSQL

1. Set DATABASE_URL env var (e.g. `postgresql+asyncpg://user:pass@host:5432/resale_engine`)
2. Run migrations: `psql -d resale_engine -f db/migrations.sql`
3. Use db/session.py async session instead of db/store.py

### Production considerations

- **Config:** Currently hard-coded in config.py. Future: YAML/env overrides.
- **Rate limiting:** Scraper delays in CFG.scraper_sources. Add Redis rate limiting for multi-worker.
- **Alert throttling:** Add dedup by listing URL hash.
- **Feedback persistence:** /feedback endpoint logs to console only. Wire to DB for ML training.
- **Craigslist:** Uses a headless browser (Playwright). Run `playwright install chromium` (or provide a local Chrome) wherever it executes.

---

## 11. Development Guide

### Coding conventions

- All prices in CAD. All datetimes timezone-aware UTC.
- Config values only in config.py -- no magic numbers.
- Pydantic v2 for all data validation. Use @field_validator for custom logic.
- Google-style docstrings on all public functions (Args/Returns sections).
- Logging via logging.getLogger(__name__). Levels: debug, info, warning, error.
- Tests via pytest with shared fixtures in tests/conftest.py.

### Extending the product registry

Add entries to PRODUCT_REGISTRY in engines/resolver.py:

```python
PRODUCT_REGISTRY.append({
    "standard_name": "RTX 5070",
    "aliases": ["RTX 5070", "5070", "GeForce RTX 5070"],
})
```

Each entry must have standard_name (str) and aliases (list[str]). Run registry integrity tests to validate no duplicates.

### Adding a condition grade

1. Add to ConditionGrade enum in models/schema.py
2. Add multiplier to ConditionMultipliers in config.py
3. Add keyword rules to CONDITION_KEYWORDS in engines/condition.py
4. Add tests to test_condition.py

### Adding a platform

1. Add to Platform enum in models/schema.py
2. Add fee config to PlatformFees in config.py
3. Create SourceAdapter subclass with @register_source(Platform.new) in adapters/sources.py
4. Add scraper import to _import_scraper()

### IQR outlier removal example

Prices: [100, 120, 450, 480, 500, 520, 540, 560, 580, 600, 2000, 2500]
Q1=450, Q3=580, IQR=130. Lower=450-1.5*130=255, Upper=580+1.5*130=775. Removes 100, 120, 2000, 2500. Result: 8 comps, median ~530.

### Config quick reference

| Parameter | Default | Effect |
|---|---|---|
| comp_window_days | 90 | How far back to look for sold comps |
| min_comps_for_valuation | 5 | Minimum comps for valuation |
| iqr_multiplier | 1.5 | Wider = fewer outliers removed |
| used_good multiplier | 0.82 | FMV = 82% of median sold price |
| ebay_canada_pct | 0.1325 | 13.25% fee on FMV |
| margin_weight | 0.50 | 50% of deal score from margin |
| min_expected_profit_cad | 50.0 | Minimum profit for alert |
| min_margin_pct | 0.15 | 15% minimum margin for alert |
| lifecycle.misses_before_gone | 3 | Consecutive absent scrapes before a listing is presumed sold |
| lifecycle.inferred_comp_haircut_pct | 0.05 | Discount applied to the last asking price for an inferred comp |
| lifecycle.use_inferred_comps | False | Whether inferred comps may enter valuation (off by default) |

---

## 12. Glossary

| Term | Definition |
|---|---|
| Comp | Comparable -- a sold listing used for valuation |
| CompBand | Statistical price band (median, mean, percentiles) for product+condition |
| FMV | Fair Market Value -- estimated resale price in used-good condition |
| ERV | Expected Resale Value -- FMV minus fees, shipping, return reserve |
| M_condition | Condition multiplier adjusting FMV (0.30-1.05) |
| M_trend | Trend multiplier for macro market factor (reserved, default 1.0) |
| IQR | Interquartile Range = Q3 - Q1, used for outlier removal |
| Deal Score | 0-100 weighted composite score indicating deal quality |
| Alert Gate | Min thresholds (profit, margin, confidence) for sending alert |
| Canonical Product | Normalized product name resolved from raw title |
| Return Reserve | Buffer % deducted from FMV for return risk |
| Holding Cost | Labor + capital lockup between purchase and resale |
| Liquidity Score | Comp count measure -- higher = easier to resell |
| Urgency Score | Higher for recently posted listings (time-sensitive) |