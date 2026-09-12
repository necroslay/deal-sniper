# GitHub Copilot Instructions — Deal Sniping & Resale Valuation Engine

> This file is the single source of truth for AI coding agents working in this repository.
> Place it at `.github/copilot-instructions.md` so GitHub Copilot loads it automatically.

## 1. What This Project Is

A **comp-driven resale valuation operating system** for second-hand marketplace deal hunting. It ingests marketplace listings (eBay, Kijiji, Facebook Marketplace, Craigslist), normalizes them to canonical products, values them against **completed sold transactions**, scores deal profitability, and alerts a human who makes the final call.

It is **not** a loose scraper, **not** an autonomous buying bot, and **not** an AI price-guesser. Market: Canada. Default currency: `CAD`.

## 2. Golden Principles (Non-Negotiable)

1. **Comp-driven, not AI-guessed.** Valuations are grounded in completed transactions (eBay sold comps, Swappa, PriceCharting). Active asking prices measure supply competition and liquidity — they are **never** ground-truth value.
2. **Deterministic-first.** Rules, regex dictionaries, and statistical aggregation handle the core pipeline. LLMs are strictly bounded to unstructured-text enrichment (messy titles, bundle components, vague condition descriptions) and must always return validated, structured JSON. LLMs never do pricing math.
3. **Valuation ≠ Deal Scoring.** The valuation engine determines what an item realistically sells for net of realization costs. Deal scoring separately decides whether acting on it makes economic sense (fees, travel, capital holding time, fraud risk).
4. **Human-in-the-loop execution.** Alerting only. Never implement autonomous checkout, automated seller messaging, account automation, or un-gated marketplace actions.
5. **Traceability & feedback telemetry from day one.** Log inputs, outputs, token usage, risk signals, confidence scores, and realized purchase/resale outcomes. This log is the future ML training set.

## 3. Tech Stack

| Concern | Choice |
|---|---|
| Runtime / pipelines | Python 3.11+ |
| API / backend | FastAPI |
| Task queue & scheduling | Celery + Redis (RQ / Dramatiq acceptable alternatives) |
| Primary database | PostgreSQL (Supabase or managed Postgres on Render/Fly.io) |
| Search / deduplication | Postgres full-text search + `pg_trgm` first; dense vector embeddings later for fuzzy multi-item matching |
| LLM layer | Bounded extraction with strict Pydantic schemas (OpenAI/DeepSeek) |
| Alerting | Discord webhooks / Telegram Bot API; Twilio SMS reserved for high-margin (≥ $100) **and** high-confidence (≥ 0.85) leads |
| Dashboard / review UI | Streamlit for fastest MVP; Next.js + TypeScript if a production UI is needed |

### Coding conventions

- Type hints everywhere; Pydantic v2 models for all API schemas and **all** LLM inputs/outputs (validate strictly, retry on invalid JSON).
- All thresholds, multipliers, fees, and score weights live in `app/config.py` — never hard-code them in engine logic.
- Wrap every marketplace source in an isolated adapter module so a policy or markup change in one source cannot break the others.
- Engine functions (resolver, comps, valuation, scoring) are pure/deterministic — cover them with pytest unit tests.
- Structured (JSON) logging for every pipeline decision.

## 4. Repository Layout & Build Order

```text
app/
├── config.py                 # thresholds, multipliers, fees, weights, source settings
├── models/
│   └── schema.py             # Pydantic models: canonical listing, comp request, valuation output
├── db/
│   ├── migrations.sql        # tables from §6
│   └── session.py            # SQLAlchemy / SQLModel session management
├── engine/
│   ├── resolver.py           # deterministic product resolver (§5, tiers 1–3 + LLM fallback)
│   ├── comps.py              # sold-comp retrieval, IQR filter, median aggregation
│   ├── valuation.py          # FMV / ERV / expected profit / liquidation (§7)
│   └── scoring.py            # deal score + confidence bands (§7–8)
├── adapters/
│   ├── ebay.py               # official Browse API + sold-comps provider, rate-limited
│   └── local.py              # Kijiji / FB / Craigslist — compliance-gated, human-in-the-loop
└── api/
    ├── alerts.py             # Discord/Telegram/SMS dispatch
    └── feedback.py           # feedback capture endpoints
```

**Implement in this order:**

- [ ] **Step 1 — Configuration & models:** `app/config.py`, `app/models/schema.py`
- [ ] **Step 2 — Database initialization:** `app/db/migrations.sql`, `app/db/session.py` (tables in §6)
- [ ] **Step 3 — Deterministic product resolver:** `app/engine/resolver.py` (alias dictionary + regex for the initial 20–50 tech SKUs)
- [ ] **Step 4 — Comps retrieval & statistical engine:** `app/engine/comps.py` (rolling window query, IQR p25–p75 filter, median)
- [ ] **Step 5 — Valuation & deal scorer:** `app/engine/valuation.py`, `app/engine/scoring.py` (FMV, ERV, expected profit, confidence bands)
- [ ] **Step 6 — Ingestion adapters & compliance gate:** `app/adapters/ebay.py`, `app/adapters/local.py` (strict rate limiting, raw payload archiving)
- [ ] **Step 7 — Alert dispatcher & feedback API:** `app/api/alerts.py`, `app/api/feedback.py`

## 5. Core Pipeline

```text
Raw Listings
  → Compliance Gate       (rate limits, ToS rules, per-source policy)
  → Scheduler / Queue     (Celery + Redis, deduplication by source + listing ID + URL hash)
  → Product Normalizer    (canonical SKU resolution — waterfall below)
  → Sold-Comp Valuation   (rolling 30–90d sold comps, IQR p25–p75 outlier strip, median)
  → Adjustment Layer      (condition, platform fees, shipping, travel, missing parts)
  → Deal Scoring          (ERV, expected profit, risk penalties, confidence bands)
  → Action Router         (alert immediately / alert with caution / review dashboard / suppress)
  → Feedback Loop         (log human decisions + realized margins → ML training / calibration)
```

### Product normalizer — 4-tier resolution waterfall

1. **Exact dictionary match** — alias table maps known strings (`4070S`, `RTX 4070 Super`, `GeForce 4070 Super`) to a canonical SKU.
2. **Regex / rule spec extraction** — pulls VRAM, chipset, model variant, storage, generation.
3. **Structured marketplace identifiers** — MPN, GTIN, ePID when the source provides them.
4. **LLM parsing (fallback only)** — for messy titles, multi-item bundles, and vague condition text; output must validate against a strict Pydantic JSON schema.

### Comparable selection rules

- Use **sold/completed** transactions only (eBay Product Research or an approved sold-comp provider, Swappa, PriceCharting) — never active asking prices.
- Filter to same spec + same condition grade within a rolling **30–90 day** window (default `comp_window_days = 60`).
- Strip outliers with the interquartile range (keep p25–p75), then take the median. Track p25/p50/p75.
- Condition is modeled on **two axes**: cosmetic grade and functional grade, plus battery health and missing accessories.

## 6. Database Schema

Immutable raw evidence, standardized valuation outputs, and full lifecycle outcome tracking.

### `raw_listings` — raw ingestion payloads (audit + reprocessing without re-scraping)

```sql
CREATE TABLE raw_listings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source VARCHAR(64) NOT NULL,            -- 'ebay', 'kijiji', 'facebook', 'craigslist'
    source_listing_id VARCHAR(128) NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    asking_price NUMERIC(10, 2) NOT NULL,
    currency VARCHAR(8) DEFAULT 'CAD',
    location VARCHAR(128),
    seller_signals_json JSONB,              -- rating, account age, review history
    raw_payload JSONB NOT NULL,
    seen_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT uq_source_listing UNIQUE (source, source_listing_id)
);
```

### `canonical_products` + `product_aliases` — SKU catalog and lookup aliases

```sql
CREATE TABLE canonical_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_key VARCHAR(128) UNIQUE NOT NULL, -- 'gpu_rtx_4070_super'
    category VARCHAR(64) NOT NULL,            -- 'gpu', 'laptop', 'console'
    brand VARCHAR(64) NOT NULL,
    model VARCHAR(128) NOT NULL,
    specs_json JSONB NOT NULL,                -- {"vram": "12GB", "chipset": "AD104"}
    base_retail_msrp NUMERIC(10, 2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE TABLE product_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID REFERENCES canonical_products(id) ON DELETE CASCADE,
    alias_pattern TEXT NOT NULL,              -- '4070S', 'RTX 4070 Super', 'GeForce 4070 Super'
    is_regex BOOLEAN DEFAULT FALSE
);
```

### `sold_comps` — historical completed transactions

```sql
CREATE TABLE sold_comps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_product_id UUID REFERENCES canonical_products(id) ON DELETE CASCADE,
    source VARCHAR(64) NOT NULL,            -- 'ebay_product_research', 'swappa', 'pricecharting'
    source_item_id VARCHAR(128),
    sold_price NUMERIC(10, 2) NOT NULL,
    shipping_price NUMERIC(10, 2) DEFAULT 0.00,
    total_paid NUMERIC(10, 2) GENERATED ALWAYS AS (sold_price + shipping_price) STORED,
    currency VARCHAR(8) DEFAULT 'CAD',
    condition_grade VARCHAR(32) NOT NULL,   -- 'brand_new', 'like_new', 'used_good', 'parts_only'
    sold_date DATE NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
CREATE INDEX idx_sold_comps_prod_date ON sold_comps(canonical_product_id, sold_date DESC);
```

### `valuations` — calculated outputs per listing

```sql
CREATE TABLE valuations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES raw_listings(id) ON DELETE CASCADE,
    canonical_product_id UUID REFERENCES canonical_products(id),
    fair_market_value NUMERIC(10, 2) NOT NULL,
    expected_resale_value NUMERIC(10, 2) NOT NULL,
    liquidation_value NUMERIC(10, 2) NOT NULL,
    max_buy_price NUMERIC(10, 2) NOT NULL,
    confidence NUMERIC(4, 3) NOT NULL,      -- 0.000 to 1.000
    comp_count INT NOT NULL,
    comp_window_days INT DEFAULT 60,
    adjustments_json JSONB NOT NULL,        -- breakdown of condition, fee, travel deductions
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

### `deal_decisions` — decisions, alert state, realized outcomes

```sql
CREATE TABLE deal_decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    listing_id UUID REFERENCES raw_listings(id) ON DELETE CASCADE,
    valuation_id UUID REFERENCES valuations(id) ON DELETE CASCADE,
    deal_score NUMERIC(5, 2) NOT NULL,
    expected_profit NUMERIC(10, 2) NOT NULL,
    risk_flags TEXT[],                      -- ARRAY['missing_charger', 'low_seller_rating', 'no_proof']
    recommendation VARCHAR(32) NOT NULL,    -- 'ALERT_IMMEDIATE', 'ALERT_CAUTION', 'REVIEW', 'SUPPRESS'
    alerted BOOLEAN DEFAULT FALSE,
    alert_channel VARCHAR(32),              -- 'discord', 'telegram', 'sms'
    human_decision VARCHAR(32),             -- 'good_lead', 'bad_match', 'too_risky', 'already_sold', 'bought', 'rejected'
    purchase_price NUMERIC(10, 2),
    resale_price NUMERIC(10, 2),
    realized_profit NUMERIC(10, 2),
    outcome_notes TEXT,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## 7. Valuation Math

Every listing produces three price points plus a purchase ceiling:

| Output | Definition | Primary use |
|---|---|---|
| **Fair Market Value (FMV)** | Clean peer-to-peer transaction price derived from sold comps | Baseline reference |
| **Expected Resale Value (ERV)** | Net realizable price after platform fees, shipping, and return risk | Profit calculations |
| **Liquidation Value** | Fast-flip pricing floor | Downside safety check |
| **Max Buy Price** | Highest acquisition price that still clears the minimum profit/margin thresholds | Negotiation ceiling |

### 7.1 Fair Market Value

```text
FMV = median_30-90d(sold_comps) × M_condition × M_trend ± Σ adjustments
```

**Condition multiplier `M_condition`:**

| Grade | Multiplier |
|---|---|
| Brand new | 1.05 |
| Open box / like new | 0.95 |
| Used good | 0.82 |
| Acceptable | 0.68 |
| For parts | 0.30 |

**Trend multiplier `M_trend`:** directional macro retention/decay factor (e.g., PCPartPicker Canada price trends, Swappa annual retention curves).

**Adjustments** (example defaults — keep in config): missing charger −$30, battery health < 80% −$60, missing original box −$15.

### 7.2 Expected Resale Value

```text
ERV = FMV − platform_fees − shipping_subsidy − return_reserve
```

- `platform_fees`: eBay Canada final value fees range **2.5%–15.3% + per-order fee** by category; local cash pickup = 0%.
- `return_reserve`: buffer for second-hand return risk, **2%–5% of FMV**.

### 7.3 Expected Profit

```text
expected_profit = ERV − asking_price − travel_cost − refurb_holding_cost
```

- `travel_cost`: in-person pickup penalty, e.g. flat **$10 + $0.75/km beyond 5 km**.
- `refurb_holding_cost`: bench testing, cleaning, and capital lockup over expected days-to-sell.

### 7.4 Deal Score

```text
deal_score = w_m·S_margin + w_l·S_liquidity + w_u·S_urgency + w_c·S_confidence − Σ penalties

S_margin    = min(100, expected_profit / ERV × 100)
S_liquidity = normalized 30-day sell-through volume
```

Penalties apply for: scam risk, condition uncertainty, transit friction, long expected holding time. All weights `w_*` live in config.

## 8. Confidence & Triage

Confidence is derived from product-match accuracy, comp volume/recency, and condition clarity.

| Confidence | Meaning | Action |
|---|---|---|
| ≥ 0.85 | Exact product match, ≥ 5 recent comps, clear condition/photos | **ALERT_IMMEDIATE** (Discord; SMS if profit ≥ $100) |
| 0.65 – 0.84 | Minor uncertainty, solid margin | **ALERT_CAUTION** (Telegram/Discord) |
| 0.40 – 0.64 | High ambiguity, missing condition info | **REVIEW** (dashboard only, no push alert) |
| < 0.40 | Extreme ambiguity or outlier pricing | **SUPPRESS** / auto-archive |

**MVP alert gate (conservative, all must pass):** expected profit ≥ $50–$100, margin ≥ 15%–25%, confidence ≥ 0.75, sold comps ≥ 5.

## 9. Phased Development Roadmap

### Phase 0 — Compliance, Source Viability & Policy Isolation

- Build a source matrix: acquisition method, auth requirements, storage rights, rate limits, personal-data exposure, ToS constraints per source. Complete this **before** engineering.
- eBay: official **Browse API** for active listings; **eBay Product Research** (or an approved sold-comp provider) for historical sold data.
- Kijiji / Facebook / Craigslist: strictly human-in-the-loop. **Do not build** autonomous browser automation or automated messaging bots (account bans, legal risk).
- Isolate every ingestor behind an adapter module so one source's policy change never disrupts the rest.

### Phase 1 — Proof of Concept (electronics core)

- Watchlist of **20–50 high-liquidity SKUs**: RTX 3060/3070/3080/4070, Ryzen 5600/5800X3D/7800X3D, DDR5 kits, PS5, Switch OLED, M1/M2 MacBooks, iPhone 13/14/15.
- Build the eBay sold-comps engine: query completed sales, clean titles, IQR-strip outliers (p25–p75), compute median sold prices.
- Build the deterministic product resolver (tiers 1–3 of the §5 waterfall).
- Done when: the three core components (normalization, sold-comp pricing, alerts) work end-to-end on the watchlist.

### Phase 2 — Functional MVP (ingestion, storage, feedback loop)

- Stand up PostgreSQL with the §6 tables; deploy the Celery/Redis ingestion queue with hash-based deduplication (source listing ID + URL).
- Build the scoring & alert router with the §8 MVP gate; push qualified leads to Discord/Telegram with actionable metadata.
- Add feedback capture (webhooks or UI buttons): `[Good Lead] [Bad Match] [Too Risky] [Already Sold] [Bought] [Resold]` — this builds the future ML training set.

### Phase 3 — Production Hardening & Observability

- Resilience: circuit breakers, per-source rate budgets, dead-letter queues, source-health monitors, alert throttling, audit logs.
- False-positive suppression: repetitive commercial/spam sellers with zero margin, vague bundles without itemized breakdowns, unrealistic prices ($1 / "contact for price"), recurring scam patterns, suspected counterfeits.
- Metrics dashboard: precision, accepted-alert rate, realized vs. estimated margin, cost-per-lead.

### Phase 4 — ML Valuation & Fine-Tuning Layer

Only after the feedback loop has logged real outcomes:

- **Tabular ML (LightGBM/XGBoost):** predict prices and calibrate condition multipliers/realization discounts. Features: SKU category, days since release, cosmetic condition, seller feedback score, comp density, text sentiment.
- **LLM fine-tuning (SFT/LoRA):** fine-tune a lightweight model on marketplace listing syntax to extract obscure specs, bundled accessories, and cosmetic flaws into strict JSON.
- **Computer vision (ViT/CNN):** classifier for stock photos, physical damage (cracked glass/hardware), and counterfeit/fraud indicators in listing images.

### Phase 5 — Modular Category Expansion

Expand only categories whose alerts prove profitable, in order of identity strength and liquidity:

| Order | Category | Identity / pricing sources | Notes |
|---|---|---|---|
| 1 | Gaming consoles & peripherals | eBay sold comps, PriceCharting | Reuses electronics condition logic |
| 2 | Video games & LEGO | PriceCharting API, BrickEconomy (UPC, ePID, set #, minifig ID) | Strong canonical identifiers |
| 3 | Trading cards (TCG/sports) | TCGplayer Market Price, PSA graded guides | Narrow watchlists; require high confidence — mistakes are costly |
| 4 | Cameras & musical gear / tools | MPB, KEH, Reverb, tool-specific eBay comps | Needs category-specific inspection checklists |
| 5 | Discogs / vintage media & board games | Discogs release history | Optional, interest-driven; board games need manual completeness checks |
| 6 | Watches & sneakers | WatchCharts, Chrono24, StockX | **Deferred** until authentication workflows and capital-risk modeling are mature |

### Phase 6 — Agentic Tooling & Multi-Agent Orchestration

Expose the engine as deterministic tool endpoints for higher-level AI agents:

```text
get_canonical_product(title, text)
get_market_comps(product_key, condition)
calculate_deal_valuation(asking_price, product_key, condition)
record_feedback_outcome(decision_id, outcome)
```

Multi-agent pipelines (poll → parse → judge → notify) must treat the valuation engine as the **authoritative source of market truth**.

## 10. Hard Rules for the Coding Agent

**Always:**

- Store raw payloads immutably before any transformation.
- Validate every LLM output against a Pydantic schema; reject and retry on mismatch.
- Read thresholds, fees, multipliers, and weights from config.
- Rate-limit and archive at every ingestion adapter; respect each source's ToS.
- Log every decision with enough context to audit and to train on later.

**Never:**

- Price from active/asking listings.
- Let an LLM compute or override a price, score, or threshold.
- Build autonomous purchasing, checkout, seller messaging, or marketplace account automation.
- Bypass the compliance gate for Kijiji, Facebook Marketplace, or Craigslist.
- Hard-code magic numbers in engine code.

