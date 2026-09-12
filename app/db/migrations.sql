-- =============================================================================
-- Resale Valuation Engine — PostgreSQL Schema
-- =============================================================================
-- Run: psql -d resale_engine -f db/migrations.sql

-- ── Extensions ──────────────────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- fuzzy text matching
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";     -- UUID generation


-- ── Listings (immutable raw payloads) ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS listings (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source              TEXT NOT NULL CHECK (source IN ('ebay','kijiji','facebook','craigslist')),
    source_listing_id   TEXT NOT NULL,
    title               TEXT NOT NULL,
    price_cad           NUMERIC(10,2) NOT NULL,
    url                 TEXT NOT NULL,
    location            TEXT NOT NULL DEFAULT '',
    description         TEXT NOT NULL DEFAULT '',
    date_posted         TIMESTAMPTZ,
    image_urls          JSONB DEFAULT '[]'::jsonb,
    raw_payload         JSONB,                          -- immutable raw source data
    scraped_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_listing_id)
);

CREATE INDEX idx_listings_source ON listings(source);
CREATE INDEX idx_listings_date_posted ON listings(date_posted DESC);
CREATE INDEX idx_listings_price ON listings(price_cad);
CREATE INDEX idx_listings_source_id ON listings(source, source_listing_id);


-- ── Canonical Products ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS canonical_products (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    standard_name       TEXT NOT NULL UNIQUE,
    category            TEXT NOT NULL DEFAULT 'electronics',
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_canonical_name ON canonical_products(standard_name);


-- ── Market Comps (eBay sold aggregations) ────────────────────────────────

CREATE TABLE IF NOT EXISTS market_comps (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    standard_name       TEXT NOT NULL REFERENCES canonical_products(standard_name),
    condition           TEXT NOT NULL DEFAULT 'unknown',
    count               INTEGER NOT NULL DEFAULT 0,
    median_price_cad    NUMERIC(10,2),
    mean_price_cad      NUMERIC(10,2),
    p25_price_cad       NUMERIC(10,2),
    p75_price_cad       NUMERIC(10,2),
    min_price_cad       NUMERIC(10,2),
    max_price_cad       NUMERIC(10,2),
    std_dev             NUMERIC(10,2) DEFAULT 0,
    window_days         INTEGER NOT NULL DEFAULT 90,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (standard_name, condition, window_days)
);

CREATE INDEX idx_comps_name ON market_comps(standard_name);
CREATE INDEX idx_comps_condition ON market_comps(condition);
CREATE INDEX idx_comps_computed ON market_comps(computed_at DESC);


-- ── Valuation Decisions (traceability log) ───────────────────────────────

CREATE TABLE IF NOT EXISTS valuation_decisions (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    listing_id          UUID NOT NULL REFERENCES listings(id),
    canonical_product   TEXT NOT NULL,
    fmv_cad             NUMERIC(10,2),
    erv_cad             NUMERIC(10,2),
    deal_score          REAL,
    confidence_level    TEXT,
    alert_sent          BOOLEAN NOT NULL DEFAULT FALSE,
    decision_payload    JSONB,                          -- full ValuationOutput
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_decisions_listing ON valuation_decisions(listing_id);
CREATE INDEX idx_decisions_score ON valuation_decisions(deal_score DESC);
CREATE INDEX idx_decisions_alert ON valuation_decisions(alert_sent);


-- ── Feedback Events (ML training set) ────────────────────────────────────

CREATE TABLE IF NOT EXISTS feedback_events (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    decision_id         UUID NOT NULL REFERENCES valuation_decisions(id),
    outcome             TEXT NOT NULL CHECK (outcome IN (
                            'Good Lead','Bad Match','Too Risky',
                            'Already Sold','Bought','Resold'
                        )),
    actual_sold_price_cad NUMERIC(10,2),
    notes               TEXT,
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_feedback_decision ON feedback_events(decision_id);
CREATE INDEX idx_feedback_outcome ON feedback_events(outcome);


-- ── Source Health Monitors ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS source_health (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source              TEXT NOT NULL UNIQUE CHECK (source IN ('ebay','kijiji','facebook','craigslist')),
    last_success_at     TIMESTAMPTZ,
    last_failure_at     TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    rate_limit_remaining INTEGER,
    is_blocked          BOOLEAN NOT NULL DEFAULT FALSE,
    checked_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);