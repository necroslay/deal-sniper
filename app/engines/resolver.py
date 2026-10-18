"""
Product Resolver — maps raw listing titles to canonical product names.

Waterfall:
  Tier 1 — Exact regex match against known product aliases
  Tier 2 — Fuzzy substring match with scoring
  Tier 3 — Keyword extraction fallback

Never uses LLM for product resolution.
"""

from __future__ import annotations

import re
import logging
from difflib import SequenceMatcher
from typing import Optional

from app.models.schema import CanonicalProduct

logger = logging.getLogger(__name__)


# ── Product Registry ─────────────────────────────────────────────────────────
# High-liquidity watchlist. In production loaded from canonical_products DB table.

PRODUCT_REGISTRY: list[dict] = [
    # NVIDIA RTX 50-series
    {"standard_name": "RTX 5090", "aliases": ["RTX 5090", "5090", "GeForce RTX 5090"]},
    {"standard_name": "RTX 5080", "aliases": ["RTX 5080", "5080", "GeForce RTX 5080"]},
    # NVIDIA RTX 40-series
    {"standard_name": "RTX 4090", "aliases": ["RTX 4090", "4090", "GeForce RTX 4090"]},
    {"standard_name": "RTX 4080 Super", "aliases": ["RTX 4080 Super", "4080 Super", "4080 SUPER"]},
    {"standard_name": "RTX 4080", "aliases": ["RTX 4080", "4080", "GeForce RTX 4080"]},
    {"standard_name": "RTX 4070 Ti Super", "aliases": ["RTX 4070 Ti Super", "4070 Ti Super"]},
    {"standard_name": "RTX 4070 Ti", "aliases": ["RTX 4070 Ti", "4070 Ti"]},
    {"standard_name": "RTX 4070 Super", "aliases": ["RTX 4070 Super", "4070 Super"]},
    {"standard_name": "RTX 4070", "aliases": ["RTX 4070", "4070", "GeForce RTX 4070"]},
    {"standard_name": "RTX 4060 Ti", "aliases": ["RTX 4060 Ti", "4060 Ti"]},
    {"standard_name": "RTX 4060", "aliases": ["RTX 4060", "4060", "GeForce RTX 4060"]},
    # NVIDIA RTX 30-series
    {"standard_name": "RTX 3090 Ti", "aliases": ["RTX 3090 Ti", "3090 Ti"]},
    {"standard_name": "RTX 3090", "aliases": ["RTX 3090", "3090", "GeForce RTX 3090"]},
    {"standard_name": "RTX 3080 Ti", "aliases": ["RTX 3080 Ti", "3080 Ti"]},
    {"standard_name": "RTX 3080", "aliases": ["RTX 3080", "3080", "GeForce RTX 3080"]},
    {"standard_name": "RTX 3070 Ti", "aliases": ["RTX 3070 Ti", "3070 Ti"]},
    {"standard_name": "RTX 3070", "aliases": ["RTX 3070", "3070", "GeForce RTX 3070"]},
    {"standard_name": "RTX 3060 Ti", "aliases": ["RTX 3060 Ti", "3060 Ti"]},
    {"standard_name": "RTX 3060", "aliases": ["RTX 3060", "3060", "GeForce RTX 3060"]},
    # NVIDIA GTX 10-series / 16-series, RTX 20-series
    # Deliberately no bare-numeric aliases here: "1080" would match "1080p".
    {"standard_name": "RTX 2070", "aliases": ["RTX 2070", "GeForce RTX 2070"]},
    {"standard_name": "GTX 1660 Super", "aliases": ["GTX 1660 Super"]},
    {"standard_name": "GTX 1660", "aliases": ["GTX 1660", "GeForce GTX 1660"]},
    {"standard_name": "GTX 1080 Ti", "aliases": ["GTX 1080 Ti"]},
    {"standard_name": "GTX 1080", "aliases": ["GTX 1080", "GeForce GTX 1080"]},
    {"standard_name": "GTX 1070", "aliases": ["GTX 1070", "GeForce GTX 1070"]},
    # AMD RX 6000-series
    {"standard_name": "RX 6800 XT", "aliases": ["RX 6800 XT", "6800 XT", "Radeon RX 6800 XT"]},
    {"standard_name": "RX 6800", "aliases": ["RX 6800", "Radeon RX 6800"]},
    # AMD RX 500-series
    {"standard_name": "RX 580", "aliases": ["RX 580", "Radeon RX 580"]},
    # AMD RX 7000-series
    {"standard_name": "RX 7900 XTX", "aliases": ["RX 7900 XTX", "7900 XTX", "Radeon RX 7900 XTX"]},
    {"standard_name": "RX 7900 XT", "aliases": ["RX 7900 XT", "7900 XT", "Radeon RX 7900 XT"]},
    {"standard_name": "RX 7800 XT", "aliases": ["RX 7800 XT", "7800 XT"]},
    {"standard_name": "RX 7700 XT", "aliases": ["RX 7700 XT", "7700 XT"]},
    {"standard_name": "RX 7600", "aliases": ["RX 7600", "7600", "Radeon RX 7600"]},
    # CPUs
    {"standard_name": "Ryzen 7 7800X3D", "aliases": ["Ryzen 7 7800X3D", "7800X3D"]},
    {"standard_name": "Ryzen 7 5800X3D", "aliases": ["Ryzen 7 5800X3D", "5800X3D"]},
    {"standard_name": "Ryzen 5 5600X", "aliases": ["Ryzen 5 5600X", "5600X"]},
    # Consoles — variants must precede their base model, since resolution
    # returns the first alias match ("PS5" would otherwise swallow "PS5 Pro").
    {"standard_name": "PS5 Pro", "aliases": ["PS5 Pro", "PlayStation 5 Pro"]},
    {"standard_name": "PlayStation 5", "aliases": ["PlayStation 5", "Playstation 5", "PS5"]},
    {"standard_name": "Nintendo Switch OLED", "aliases": ["Nintendo Switch OLED", "Switch OLED"]},
    {"standard_name": "Nintendo Switch", "aliases": ["Nintendo Switch", "Switch"]},
    # Apple laptops
    {"standard_name": "MacBook Air M1", "aliases": ["MacBook Air M1", "M1 MacBook Air"]},
    {"standard_name": "MacBook Air M2", "aliases": ["MacBook Air M2", "M2 MacBook Air"]},
    {"standard_name": "MacBook Air M3", "aliases": ["MacBook Air M3", "M3 MacBook Air"]},
    {"standard_name": "MacBook Pro M1", "aliases": ["MacBook Pro M1", "M1 MacBook Pro"]},
    {"standard_name": "MacBook Pro M2", "aliases": ["MacBook Pro M2", "M2 MacBook Pro"]},
    {"standard_name": "MacBook Pro M3", "aliases": ["MacBook Pro M3", "M3 MacBook Pro"]},
    # iPhones
    # No bare "15"/"14" alias: a two-digit number appears in unrelated titles
    # ("Dell XPS 15", "iMac 2015", "ThinkCentre M715q") and would resolve them
    # to an iPhone. Anything shorter than a four-digit model number is a
    # size/year/code, not an identifier -- see the registry invariant test.
    {"standard_name": "iPhone 15 Pro Max", "aliases": ["iPhone 15 Pro Max", "15 Pro Max"]},
    {"standard_name": "iPhone 15 Pro", "aliases": ["iPhone 15 Pro", "15 Pro"]},
    {"standard_name": "iPhone 15", "aliases": ["iPhone 15"]},
    {"standard_name": "iPhone 14 Pro Max", "aliases": ["iPhone 14 Pro Max", "14 Pro Max"]},
    {"standard_name": "iPhone 14 Pro", "aliases": ["iPhone 14 Pro", "14 Pro"]},
    {"standard_name": "iPhone 14", "aliases": ["iPhone 14"]},
]


def _compile_patterns() -> list[tuple[re.Pattern, str]]:
    """Build compiled regex patterns from the product registry (Tier 1)."""
    patterns: list[tuple[re.Pattern, str]] = []
    for entry in PRODUCT_REGISTRY:
        canonical = entry["standard_name"]
        for alias in entry["aliases"]:
            escaped = re.escape(alias)
            flexible = escaped.replace(r"\ ", r"[\s\-]*")
            try:
                regex = re.compile(flexible, re.IGNORECASE)
                patterns.append((regex, canonical))
            except re.error as e:
                logger.warning(f"Invalid regex for '{alias}': {e}")
    return patterns


_TIER1_PATTERNS = _compile_patterns()


def find_products(title: str) -> list[str]:
    """Return every canonical product whose alias matches *title*.

    Unlike :func:`resolve_product` (which returns the *first* match), this returns
    all matches so callers can distinguish a single-item listing from a multi-item
    lot. Redundant less-specific matches are dropped: ``"Nintendo Switch"`` is
    discarded when ``"Nintendo Switch OLED"`` also matched, and ``"RTX 3060"`` is
    discarded when ``"RTX 3060 Ti"`` matched.
    """
    found: list[str] = []
    for regex, canonical in _TIER1_PATTERNS:
        if canonical not in found and regex.search(title):
            found.append(canonical)

    # Drop any match that is a substring of a more specific match.
    return [c for c in found if not any(c != other and c in other for other in found)]


def resolve_product(title: str) -> CanonicalProduct:
    """
    Resolve a raw listing title to a canonical product.

    Waterfall:
      1. Exact regex match against known aliases (Tier 1)
      2. Fuzzy substring match with score > 0.8 (Tier 2)
      3. Return title as-is with unknown product (Tier 3 fallback)
    """
    # Tier 1: regex match
    for regex, canonical in _TIER1_PATTERNS:
        if regex.search(title):
            logger.debug(f"Tier 1 resolved '{title[:50]}' → {canonical}")
            return CanonicalProduct(
                standard_name=canonical,
                category=_guess_category(canonical),
                resolved_from=title,
            )

    # Tier 2: fuzzy substring scoring
    best_match: Optional[str] = None
    best_score: float = 0.0
    for entry in PRODUCT_REGISTRY:
        for alias in entry["aliases"]:
            if alias.lower() in title.lower():
                score = SequenceMatcher(None, alias.lower(), title.lower()).ratio()
                if score > best_score and score > 0.8:
                    best_score = score
                    best_match = entry["standard_name"]

    if best_match:
        logger.debug(f"Tier 2 fuzzy resolved '{title[:50]}' → {best_match} (score={best_score:.2f})")
        return CanonicalProduct(
            standard_name=best_match,
            category=_guess_category(best_match),
            resolved_from=title,
        )

    # Tier 3: fallback
    logger.debug(f"Tier 3 fallback — no match for '{title[:50]}'")
    return CanonicalProduct(
        standard_name=title,
        category="unknown",
        resolved_from=title,
    )


def _guess_category(standard_name: str) -> str:
    """Broad category guess from product name patterns."""
    name = standard_name.lower()
    if any(x in name for x in ["rtx", "rx", "radeon", "geforce", "graphics", "gpu"]):
        return "gpu"
    if any(x in name for x in ["ryzen", "cpu", "processor"]):
        return "cpu"
    if any(x in name for x in ["playstation", "ps5", "xbox", "nintendo", "switch"]):
        return "console"
    if any(x in name for x in ["macbook", "macbook pro", "macbook air"]):
        return "laptop"
    if any(x in name for x in ["iphone", "ipad", "apple watch"]):
        return "mobile"
    if any(x in name for x in ["ddr", "ram", "memory"]):
        return "memory"
    if any(x in name for x in ["ssd", "nvme", "hard drive", "storage"]):
        return "storage"
    return "electronics"