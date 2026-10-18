from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class KijijiListing:
    """Represents a single listing scraped from Kijiji."""
    title: str
    price_cad: float
    url: str
    location: str
    description: str
    date_posted: Optional[str] = None
    listing_id: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    # Structured signals from Kijiji's own listing attributes. ``fulfillment`` is
    # a comma-joined subset of shipping / delivery / curbside / pickup.
    fulfillment: str = ""
    condition: str = ""
    payment: str = ""
    seller_id: str = ""
    seller_type: str = ""
    price_drop: bool = False
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)
