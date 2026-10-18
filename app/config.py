"""Central configuration for the deal-sniper scraper + viewer.

Deliberately small. This project now does one job: scrape second-hand computer
parts and let you browse them. The full valuation engine — and its much larger
config (fees, condition multipliers, score weights, alert gate) — lives at git
tag ``full-engine-v1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Repository root, used to anchor ML data/model paths regardless of the CWD the
# CLI or dashboard is launched from.
_REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ScraperConfig:
    """Scrape volume and pacing, per source."""
    # Kijiji is swept Canada-wide (see kijiji-scraper/config.yaml `region`), so
    # the corpus is now capped by pagination depth rather than the location
    # filter. Override per run with ``--pages``.
    kijiji_pages: int = 8
    craigslist_pages: int = 2
    # r/CanadianHardwareSwap: pages of 100 newest posts. Reddit hands anonymous
    # clients ~1 request per rate window, so each extra page costs ~40s of waiting.
    reddit_pages: int = 2
    kijiji_delay_seconds: float = 2.0
    craigslist_delay_seconds: float = 1.5


@dataclass
class LifecycleConfig:
    """How many consecutive scrapes a listing may be absent before it is
    presumed gone (sold or withdrawn). Drives the 'gone' status in the viewer."""
    misses_before_gone: int = 3


@dataclass
class MLConfig:
    """Machine-learning layer: data paths, split/eval knobs, routing guardrails.

    Disabled by default, and every path is anchored to the repository root, so
    the scraper + viewer keep running with no ML dependencies installed. All
    thresholds live here — never hard-coded in engine code.
    """

    enabled: bool = False
    seed: int = 42
    # Labels with fewer examples than this are collapsed to ``other_label`` so a
    # handful of one-off titles cannot dominate macro-averaged metrics.
    min_class_support: int = 3
    other_label: str = "OTHER"
    # The label the resolver baseline emits for Tier-3 (unresolved) titles.
    unknown_label: str = "__none__"
    # The hybrid only trusts a model prediction above these confidences.
    # The resolver threshold is deliberately conservative: on the real labeled
    # set, resolving an already-correct rule abstention (a laptop, a service ad)
    # into a product costs more than the long-tail wins it recovers, so below
    # ~0.8 the hybrid degrades the metric. Conservative-by-default means the
    # hybrid can never do worse than the rules it wraps.
    resolver_min_confidence: float = 0.8
    guard_reject_threshold: float = 0.5
    # Recall the guard threshold is chosen to hit, trading precision for it.
    guard_target_recall: float = 0.95
    # Directory layout (all relative to the repository root).
    data_dir: Path = field(default_factory=lambda: _REPO_ROOT / "data")
    reports_dir: Path = field(default_factory=lambda: _REPO_ROOT / "reports")
    models_dir: Path = field(default_factory=lambda: _REPO_ROOT / "models")

    # Price model: share of the *newest* listings held out for testing, since the
    # split is temporal (train on older posts, test on newer ones).
    price_test_fraction: float = 0.25

    @property
    def labels_dir(self) -> Path:
        """Where hand-labeled CSVs live (committed; the DB itself is not)."""
        return self.data_dir / "labels"

    @property
    def retail_path(self) -> Path:
        """Hand-maintained new-retail prices, used to cap the predicted band.

        The ceiling is optional: when this file is absent the price model runs on
        peer data alone.
        """
        return self.data_dir / "retail_prices.csv"


@dataclass
class AlertConfig:
    """When a listing is worth a notification, and how many to send.

    "Deal" always means *underpriced relative to comparable asking prices* for the
    same product (see ``app.ml.price_model``) — never a claim about market value.
    The webhook URL is a secret and is read from the environment, not stored here.
    """
    webhook_env: str = "DISCORD_WEBHOOK_URL"
    # A product needs this many comparable listings before its band is trusted.
    min_comps: int = 8
    # Alert only when the price is at or below the product's P25 *and* at least
    # this far under its median — P25 alone would flag a quarter of all listings.
    min_discount: float = 0.20
    # Prices under this fraction of the median are almost always scams, parts-only
    # ads or placeholders, not bargains.
    min_price_ratio: float = 0.35
    # Only listings first seen this recently are alerted, so the first run on a
    # large backlog cannot flood the channel.
    max_listing_age_hours: float = 72.0
    max_per_run: int = 10
    # You don't want to travel: only alert on listings the seller will ship or
    # deliver. Set False to also see pickup-only / unknown ones.
    require_shippable: bool = True
    # Treat "not stated" as shippable? Only ~16% of Kijiji ads say either way and
    # many sellers will ship if asked, so True (the alert is marked "ask seller"
    # and carries extra risk). Explicit pickup-only listings are always dropped.
    unknown_counts_as_shippable: bool = True
    # Skip alerts riskier than this (0 safe .. 100 scam-shaped). 55 is where the
    # score turns "high", so high-risk listings never alert.
    max_risk: int = 55
    # Laptops are priced against looser comparables (family + CPU class), so they
    # must be further under the median than a GPU to count.
    laptop_min_discount: float = 0.30
    # Optional narrowing (set in watchlist.yaml): substring matches on the
    # product key, and a ceiling on what you are willing to spend.
    include: list = field(default_factory=list)
    exclude: list = field(default_factory=list)
    max_landed_cad: float = 0.0          # 0 = no ceiling
    # Resale ("flip") route: alert when the expected net profit clears both bars,
    # even if the price isn't far below the median.
    min_profit_cad: float = 40.0
    min_roi: float = 0.15


@dataclass
class LogisticsConfig:
    """What it costs to get an item to you, and to send it on if you resell.

    Estimates, not quotes: tracked + insured Canada Post-style flat rates by item
    class. Override per category as your real rates become known.
    """
    shipping_cad: dict = field(default_factory=lambda: {
        "gpu": 28.0, "laptop": 40.0, "cpu": 15.0, "memory": 12.0,
        "storage": 12.0, "console": 35.0, "mobile": 20.0, "default": 30.0})
    # Shipping the seller would charge scales with value (insurance).
    insured_value_rate: float = 0.01


@dataclass
class ProfitConfig:
    """Resale maths. Comparable *asking* prices overstate what items sell for."""
    # Expected sale price as a fraction of the comparable median ask.
    ask_to_sold: float = 0.92
    # Marketplace fee as a fraction of the sale price (eBay.ca-style final value
    # fee + payment processing), plus a fixed per-order fee.
    resale_fee_rate: float = 0.135
    resale_fixed_fee_cad: float = 0.30
    # Money lost to dead-on-arrival items, returns and scams, as a share of the
    # purchase price.
    risk_reserve_rate: float = 0.04


@dataclass
class AppConfig:
    """Top-level config aggregating all sub-configs."""
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    alerts: AlertConfig = field(default_factory=AlertConfig)
    logistics: LogisticsConfig = field(default_factory=LogisticsConfig)
    profit: ProfitConfig = field(default_factory=ProfitConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    currency: str = "CAD"


CFG = AppConfig()
