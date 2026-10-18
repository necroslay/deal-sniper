"""Pydantic models for the scraper + viewer.

Only three models survive the simplification: a listing as scraped, the platform
it came from, and the canonical product a title resolves to. The valuation models
(FairValueEstimate, DealScore, CompBand, ...) live at git tag ``full-engine-v1``.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """Marketplaces the scrapers know how to read."""
    kijiji = "kijiji"
    craigslist = "craigslist"
    reddit = "reddit"      # r/CanadianHardwareSwap
    ebay = "ebay"          # eBay.ca, via the official Browse API


class RawListing(BaseModel):
    """A listing captured directly from a marketplace source."""
    source: Platform
    source_listing_id: str = Field(alias="listing_id")
    title: str
    price_cad: float
    url: str
    location: Optional[str] = ""
    description: Optional[str] = ""
    date_posted: Optional[str] = None
    image_urls: list[str] = Field(default_factory=list)
    # How the item can change hands, and who is selling it. Empty = unknown.
    # ``fulfillment`` is a comma-joined subset of shipping / delivery / curbside /
    # pickup; Craigslist is pickup-only, Kijiji reports it per listing.
    fulfillment: str = ""
    condition: str = ""
    payment: str = ""
    seller_id: str = ""
    seller_type: str = ""
    price_drop: bool = False
    # What the seller will actually charge to ship, when the marketplace says
    # (eBay does); None = unknown, so the estimate table applies.
    shipping_cad: Optional[float] = None
    # Seller reputation where the marketplace has one: feedback percentage
    # (0-100) and number of ratings.
    seller_rating: Optional[float] = None
    seller_reviews: Optional[int] = None
    raw_payload: Optional[dict[str, Any]] = None
    scraped_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    model_config = {"populate_by_name": True}


class CanonicalProduct(BaseModel):
    """A canonical product resolved from a raw title (e.g. "RTX 3080")."""
    standard_name: str
    category: str = "electronics"
    resolved_from: str = ""
