"""Central configuration for the deal-sniper scraper + viewer.

Deliberately small. This project now does one job: scrape second-hand computer
parts and let you browse them. The full valuation engine — and its much larger
config (fees, condition multipliers, score weights, alert gate) — lives at git
tag ``full-engine-v1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScraperConfig:
    """Scrape volume and pacing, per source."""
    kijiji_pages: int = 3
    craigslist_pages: int = 2
    kijiji_delay_seconds: float = 2.0
    craigslist_delay_seconds: float = 1.5


@dataclass
class LifecycleConfig:
    """How many consecutive scrapes a listing may be absent before it is
    presumed gone (sold or withdrawn). Drives the 'gone' status in the viewer."""
    misses_before_gone: int = 3


@dataclass
class AppConfig:
    """Top-level config aggregating all sub-configs."""
    scraper: ScraperConfig = field(default_factory=ScraperConfig)
    lifecycle: LifecycleConfig = field(default_factory=LifecycleConfig)
    currency: str = "CAD"


CFG = AppConfig()
