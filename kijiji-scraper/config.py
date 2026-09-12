"""
Configuration loader for Kijiji GPU Scraper.

Reads settings from ``config.yaml`` and provides typed access via a
dataclass.  Call ``load_config()`` once at startup and pass the returned
``ScraperConfig`` to components that need it.

Future improvements:
  - Environment-specific overrides (dev/prod)
  - CLI flags that can override individual config keys
  - Live reload via file-watch
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


# ── Config Dataclass ─────────────────────────────────────────────────────────

@dataclass
class SearchConfig:
    base_url: str = "https://www.kijiji.ca"
    url_template: str = "https://www.kijiji.ca/b-gta-greater-toronto-area/{search_term}/k0l1700272?page={page}"
    default_search_term: str = "graphics-card"
    search_terms: list[str] = field(default_factory=lambda: [
        "graphics-card", "gpu", "video-card",
    ])
    listings_per_page: int = 40
    max_listings: int = 200
    default_pages: int = 5


@dataclass
class RequestConfig:
    page_delay: float = 2.0
    timeout: int = 30
    browser: str = "chrome"
    platform: str = "windows"
    mobile: bool = False
    headers: dict[str, str] = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.7,en;q=0.3",
        "Referer": "https://www.kijiji.ca/",
        "DNT": "1",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    })


@dataclass
class ApolloConfig:
    listing_type: str = "StandardListing"


def _default_high_end_gpus() -> list[str]:
    return [
        "rtx 4090", "rtx 4080 super", "rtx 4080",
        "rtx 5090", "rtx 5080",
        "rx 7900 xtx", "rx 7900 xt",
    ]


@dataclass
class ScraperConfig:
    search: SearchConfig = field(default_factory=SearchConfig)
    high_end_gpus: list[str] = field(default_factory=_default_high_end_gpus)
    request: RequestConfig = field(default_factory=RequestConfig)
    apollo: ApolloConfig = field(default_factory=ApolloConfig)


# ── Loader ───────────────────────────────────────────────────────────────────

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_config_cache: ScraperConfig | None = None


def load_config(path: str | os.PathLike | None = None) -> ScraperConfig:
    """
    Load and return a :class:`ScraperConfig`.

    Results are cached after the first call.  Pass *path* to override the
    default location (``config.yaml`` next to this file).
    """
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

    # Build nested config objects, falling back to defaults for missing keys.
    search_raw = raw.get("search", {})
    request_raw = raw.get("request", {})
    apollo_raw = raw.get("apollo", {})

    # Compute default_pages from max_listings / listings_per_page
    search_raw["default_pages"] = max(1, search_raw.get("max_listings", 200) // search_raw.get("listings_per_page", 40))

    cfg = ScraperConfig(
        search=SearchConfig(**{k: v for k, v in search_raw.items() if k in SearchConfig.__dataclass_fields__}),
        high_end_gpus=raw.get("high_end_gpus", _default_high_end_gpus()),
        request=RequestConfig(**{k: v for k, v in request_raw.items() if k in RequestConfig.__dataclass_fields__}),
        apollo=ApolloConfig(**{k: v for k, v in apollo_raw.items() if k in ApolloConfig.__dataclass_fields__}),
    )

    _config_cache = cfg
    return cfg