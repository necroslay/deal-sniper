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
class AppConfig:
    """Top-level config aggregating all sub-configs."""
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    currency: str = "CAD"


CFG = AppConfig()
