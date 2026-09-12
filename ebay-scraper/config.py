"""
Configuration loader for eBay Canada Scraper.

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
    base_url: str = "https://www.ebay.ca"
    url_template: str = "https://www.ebay.ca/sch/i.html?_nkw={search_term}&_sacat=0&LH_PrefLoc=2&rt=nc"
    sold_url_template: str = "https://www.ebay.ca/sch/i.html?_nkw={search_term}&LH_Sold=1&LH_Complete=1&LH_PrefLoc=2&rt=nc"
    default_search_term: str = "rtx 4090"
    default_pages: int = 5
    listings_per_page: int = 60


@dataclass
class RequestConfig:
    page_delay: float = 2.0
    timeout: int = 30
    max_retries: int = 3
    headers: dict[str, str] = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.7,en;q=0.3",
        "Referer": "https://www.ebay.ca/",
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

    cfg = ScraperConfig(
        search=SearchConfig(**{k: v for k, v in search_raw.items() if k in SearchConfig.__dataclass_fields__}),
        request=RequestConfig(**{k: v for k, v in request_raw.items() if k in RequestConfig.__dataclass_fields__}),
    )

    _config_cache = cfg
    return cfg