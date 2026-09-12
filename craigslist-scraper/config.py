"""
Configuration loader for Craigslist Canada Scraper.

Reads settings from ``config.yaml`` and provides typed access via a
dataclass.  Call ``load_config()`` once at startup.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install with: pip install pyyaml")
    sys.exit(1)


@dataclass
class SearchConfig:
    base_url: str = "https://toronto.craigslist.org"
    url_template: str = "https://{location}.craigslist.org/search/{category}?query={search_term}&sort=date"
    default_search_term: str = "graphics card"
    default_location: str = "toronto"
    default_category: str = "cpd"
    default_pages: int = 2
    listings_per_page: int = 120
    # Categories searched when no explicit category is given. Craigslist GPU/PC
    # listings live mostly under "cpd" (computer parts) and "sys" (computers).
    categories: list[str] = field(default_factory=lambda: ["cpd", "sys", "elec"])

    category_map: dict[str, str] = field(default_factory=lambda: {
        "elec": "Electronics",
        "cpd": "Computer Parts",
        "sys": "Computers",
        "msg": "Video Gaming",
    })


@dataclass
class BrowserConfig:
    """Headless-browser settings (Craigslist search pages require JS)."""
    enabled: bool = True
    headless: bool = True
    channel: str = "chrome"          # use the locally-installed Chrome
    timeout_ms: int = 60000
    settle_ms: int = 1500
    wait_for_selector: str = "div.cl-search-result"
    block_images: bool = True
    # If the browser is unavailable, fall back to a plain HTTP GET (which
    # returns the JS shell — useful only for diagnostics).
    fallback_to_requests: bool = True



@dataclass
class RequestConfig:
    page_delay: float = 1.5
    timeout: int = 30
    headers: dict[str, str] = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.7,en;q=0.3",
        "Referer": "https://www.craigslist.org/",
        "DNT": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })


@dataclass
class ScraperConfig:
    search: SearchConfig = field(default_factory=SearchConfig)
    request: RequestConfig = field(default_factory=RequestConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)


_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_config_cache: ScraperConfig | None = None


def load_config(path: str | os.PathLike | None = None) -> ScraperConfig:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    p = Path(path) if path else _CONFIG_PATH

    if not p.exists():
        print(f"WARNING: Config file not found at {p}, using defaults.")
        _config_cache = ScraperConfig()
        return _config_cache

    with open(p, "r", encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    search_raw = raw.get("search", {})
    request_raw = raw.get("request", {})
    browser_raw = raw.get("browser", {})

    cfg = ScraperConfig(
        search=SearchConfig(**{k: v for k, v in search_raw.items() if k in SearchConfig.__dataclass_fields__}),
        request=RequestConfig(**{k: v for k, v in request_raw.items() if k in RequestConfig.__dataclass_fields__}),
        browser=BrowserConfig(**{k: v for k, v in browser_raw.items() if k in BrowserConfig.__dataclass_fields__}),
    )

    _config_cache = cfg
    return cfg