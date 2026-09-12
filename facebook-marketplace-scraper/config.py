"""
Configuration loader for Facebook Marketplace Scraper.

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


# ── Config Dataclass ─────────────────────────────────────────────────────────

@dataclass
class SearchConfig:
    base_url: str = "https://www.facebook.com"
    url_template: str = "https://www.facebook.com/marketplace/{location}/search?query={search_term}"
    default_search_term: str = "graphics card"
    default_location: str = "toronto"
    default_pages: int = 5
    listings_per_page: int = 24


@dataclass
class AuthConfig:
    """Facebook session authentication via exported cookies."""
    cookie_file: str = "fb_cookies.json"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


@dataclass
class RequestConfig:
    page_delay: float = 3.0
    timeout: int = 30
    max_retries: int = 3
    headers: dict[str, str] = field(default_factory=lambda: {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-CA,en-US;q=0.7,en;q=0.3",
        "Referer": "https://www.facebook.com/",
        "DNT": "1",
    })


@dataclass
class ScraperConfig:
    search: SearchConfig = field(default_factory=SearchConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    request: RequestConfig = field(default_factory=RequestConfig)


# ── Loader ───────────────────────────────────────────────────────────────────

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
    auth_raw = raw.get("auth", {})
    request_raw = raw.get("request", {})

    cfg = ScraperConfig(
        search=SearchConfig(**{k: v for k, v in search_raw.items() if k in SearchConfig.__dataclass_fields__}),
        auth=AuthConfig(**{k: v for k, v in auth_raw.items() if k in AuthConfig.__dataclass_fields__}),
        request=RequestConfig(**{k: v for k, v in request_raw.items() if k in RequestConfig.__dataclass_fields__}),
    )

    _config_cache = cfg
    return cfg