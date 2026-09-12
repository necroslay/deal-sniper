"""
Central configuration for the comp-driven resale valuation engine.

Every threshold, multiplier, fee rate, weight, and adjustment lives here.
Never hard-code magic numbers in engine logic — always read from config.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PlatformFees:
    """Fee structures per resale platform (as % of sale price or flat)."""
    ebay_canada_pct: float = 0.1325
    ebay_international_pct: float = 0.153
    ebay_per_order_fixed_cad: float = 0.45
    facebook_marketplace_pct: float = 0.00
    kijiji_pct: float = 0.00
    craigslist_pct: float = 0.00


@dataclass
class ShippingCosts:
    """Estimated shipping costs by item category (CAD)."""
    gpu_standard: float = 20.0
    gpu_express: float = 35.0
    laptop_standard: float = 25.0
    desktop_standard: float = 50.0
    monitor_standard: float = 40.0
    console_standard: float = 18.0
    phone_tablet_standard: float = 15.0
    small_accessory: float = 10.0


@dataclass
class ConditionMultipliers:
    """M_condition — FMV multiplier by condition grade."""
    brand_new: float = 1.05
    open_box_like_new: float = 0.95
    used_good: float = 0.82
    acceptable: float = 0.68
    for_parts: float = 0.30


@dataclass
class ReturnReserve:
    """Buffer for second-hand return risk, as % of FMV."""
    electronics_min_pct: float = 0.02
    electronics_max_pct: float = 0.05
    default_pct: float = 0.03


@dataclass
class TravelCosts:
    """In-person pickup friction costs (CAD)."""
    flat_base: float = 10.0
    per_km_beyond: float = 0.75
    free_radius_km: float = 5.0
    max_travel_km: float = 100.0
@dataclass
class HoldingCosts:
    """Labor and capital lockup costs."""
    hourly_labor_rate_cad: float = 20.0
    testing_hours_default: float = 1.0
    cleaning_hours: float = 0.5
    photo_listing_hours: float = 0.5
    annual_capital_cost_pct: float = 0.08


@dataclass
class ItemAdjustments:
    """Specific item-level value adjustments (CAD)."""
    missing_charger: float = -30.0
    battery_health_below_80: float = -60.0
    missing_original_box: float = -15.0
    scratched_screen: float = -50.0
    no_accessories: float = -20.0
    no_cables: float = -10.0
    cosmetic_damage_minor: float = -25.0
    cosmetic_damage_major: float = -75.0


@dataclass
class ScoreWeights:
    """Component weights for deal_score = w_m·S_margin + w_l·S_liquidity + ..."""
    margin_weight: float = 0.50
    liquidity_weight: float = 0.20
    urgency_weight: float = 0.15
    confidence_weight: float = 0.15


@dataclass
class PenaltyConfig:
    """Subtractions from deal score for risk factors."""
    scam_risk: float = -20.0
    condition_uncertainty: float = -15.0
    transit_friction_high: float = -10.0
    transit_friction_medium: float = -5.0
    long_holding_time: float = -10.0
    vague_bundle: float = -25.0
    unrealistic_price: float = -30.0


@dataclass
class CompConfig:
    """Parameters for sold comps aggregation."""
    comp_window_days: int = 90
    min_comps_for_valuation: int = 5
    iqr_multiplier: float = 1.5
    max_comps_per_product: int = 200
    recency_weight_days: int = 30


@dataclass
class AlertGate:
    """MVP alert gate thresholds (all must pass)."""
    min_expected_profit_cad: float = 50.0
    min_margin_pct: float = 0.15
    min_confidence: float = 0.75
    min_comps: int = 5
    confidence_alert_immediate: float = 0.85
    confidence_alert_caution: float = 0.65
    confidence_review: float = 0.40
    confidence_suppress: float = 0.00


@dataclass
class ScraperSourceConfig:
    """Per-source scrape intervals."""
    kijiji_pages_default: int = 3
    kijiji_delay_seconds: float = 2.0
    ebay_pages_default: int = 5
    ebay_delay_seconds: float = 2.0
    ebay_sold_pages_default: int = 3
    facebook_pages_default: int = 5
    facebook_delay_seconds: float = 3.0
    craigslist_pages_default: int = 3
    craigslist_delay_seconds: float = 1.5


@dataclass
class AppConfig:
    """Top-level config aggregating all sub-configs."""
    platform_fees: PlatformFees = field(default_factory=PlatformFees)
    shipping_costs: ShippingCosts = field(default_factory=ShippingCosts)
    condition_multipliers: ConditionMultipliers = field(default_factory=ConditionMultipliers)
    return_reserve: ReturnReserve = field(default_factory=ReturnReserve)
    travel_costs: TravelCosts = field(default_factory=TravelCosts)
    holding_costs: HoldingCosts = field(default_factory=HoldingCosts)
    item_adjustments: ItemAdjustments = field(default_factory=ItemAdjustments)
    score_weights: ScoreWeights = field(default_factory=ScoreWeights)
    penalties: PenaltyConfig = field(default_factory=PenaltyConfig)
    comp: CompConfig = field(default_factory=CompConfig)
    alert_gate: AlertGate = field(default_factory=AlertGate)
    scraper_sources: ScraperSourceConfig = field(default_factory=ScraperSourceConfig)
    currency: str = "CAD"
    country: str = "CA"


CFG = AppConfig()


def load_config() -> AppConfig:
    """Return the global config singleton. Future: load from YAML/env."""
    return CFG