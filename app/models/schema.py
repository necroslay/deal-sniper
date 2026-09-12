"""
Pydantic v2 models for the valuation engine.
All API schemas and LLM input/output schemas are validated here.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums ────────────────────────────────────────────────────────────────────

class ConditionGrade(str, Enum):
    brand_new = "brand_new"
    open_box_like_new = "open_box_like_new"
    used_good = "used_good"
    acceptable = "acceptable"
    for_parts = "for_parts"
    unknown = "unknown"


class ConfidenceLevel(str, Enum):
    alert_immediate = "ALERT_IMMEDIATE"
    alert_caution = "ALERT_CAUTION"
    review = "REVIEW"
    suppress = "SUPPRESS"


class Platform(str, Enum):
    ebay = "ebay"
    kijiji = "kijiji"
    facebook = "facebook"
    craigslist = "craigslist"


# ── Raw Source Models ──────────────────────────────────────────────────────

class RawListing(BaseModel):
    """A raw listing captured directly from a marketplace source."""
    source: Platform
    source_listing_id: str = Field(alias="listing_id")
    title: str
    price_cad: float
    url: str
    location: Optional[str] = ""
    description: Optional[str] = ""
    date_posted: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    raw_payload: Optional[dict[str, Any]] = None
    scraped_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    model_config = {"populate_by_name": True}


class SoldComp(BaseModel):
    """A completed/sold listing used for comp valuation."""
    source: Platform = Platform.ebay
    source_listing_id: str
    title: str
    sold_price_cad: float
    sold_date: str
    condition: str = ""
    url: str = ""
    location: str = ""
    # Canonical product this comp belongs to. Set explicitly when the caller
    # already knows it (e.g. seeding a baseline table); when None the store
    # derives it from `title` at insert time.
    product_key: Optional[str] = None
    # True for a comp inferred from a *disappeared* listing rather than an
    # observed completed sale (see config.LifecycleConfig). Kept explicit so
    # inferred and real comps can be queried and weighted separately.
    inferred: bool = False
    scraped_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Canonical Product ──────────────────────────────────────────────────────

class CanonicalProduct(BaseModel):
    """A canonical product resolved from a raw title."""
    standard_name: str
    category: str = "electronics"
    resolved_from: str = ""


# ── Valuation Output ───────────────────────────────────────────────────────

class FairValueEstimate(BaseModel):
    """The fair market value estimate for a listing."""
    standard_name: str
    fmv_cad: float
    fmv_confidence_interval: tuple[float, float]
    condition: str
    condition_confidence: float = 0.0
    comps_used: int = 0
    comp_band: Optional[CompBand] = None


class RealizedValue(BaseModel):
    """Net value after realization costs."""
    fmv_cad: float
    platform_fees_cad: float
    shipping_cost_cad: float
    return_reserve_cad: float
    erv_cad: float


class DealScore(BaseModel):
    """Final deal score with all components."""
    score: float
    margin_score: float
    liquidity_score: float
    urgency_score: float
    confidence_score: float
    penalty_total: float
    expected_profit_cad: float
    margin_pct: float
    confidence_level: ConfidenceLevel
    asking_price_cad: float
    fmv_cad: float
    erv_cad: float
    risk_flags: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    computed_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ValuationOutput(BaseModel):
    """Complete valuation result for a single listing."""
    raw_listing: RawListing
    canonical_product: CanonicalProduct
    fair_value: FairValueEstimate
    realized_value: RealizedValue
    deal_score: DealScore
    pipeline_version: str = "1.0.0"


# ── Feedback ────────────────────────────────────────────────────────────────

class FeedbackEvent(BaseModel):
    """User feedback on a deal alert."""
    decision_id: str
    outcome: str
    actual_sold_price_cad: Optional[float] = None
    notes: Optional[str] = None
    submitted_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    @field_validator("outcome")
    @classmethod
    def valid_outcome(cls, v: str) -> str:
        allowed = {"Good Lead", "Bad Match", "Too Risky", "Already Sold", "Bought", "Resold"}
        if v not in allowed:
            raise ValueError(f"outcome must be one of {allowed}")
        return v


# ── Health ──────────────────────────────────────────────────────────────────

class SourceHealth(BaseModel):
    """Health status of a marketplace source adapter."""
    source: Platform
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    consecutive_failures: int = 0
    rate_limit_remaining: Optional[int] = None
    is_blocked: bool = False
    checked_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# ── Logging ──────────────────────────────────────────────────────────────────

class PipelineDecision(BaseModel):
    """Structured log entry for every pipeline decision."""
    decision_id: str
    raw_listing_url: str
    canonical_product: str
    fmv_cad: float
    erv_cad: float
    deal_score: float
    confidence_level: str
    alert_sent: bool = False
    token_usage: Optional[dict[str, int]] = None
    risk_signals: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
# ── Comp Aggregation ───────────────────────────────────────────────────────

class CompBand(BaseModel):
    """Price band statistics from sold comps aggregation."""
    standard_name: str
    condition: str
    count: int
    median_price_cad: float
    mean_price_cad: float
    p25_price_cad: float
    p75_price_cad: float
    min_price_cad: float
    max_price_cad: float
    std_dev: float = 0.0
    window_days: int = 90
    computed_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CompRequest(BaseModel):
    """Request to fetch comps for a product."""
    standard_name: str
    condition: Optional[str] = None
    max_age_days: int = 90
    min_comps: int = 5