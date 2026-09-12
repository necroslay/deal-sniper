from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class MarketplaceListing:
    """Represents a single listing scraped from Facebook Marketplace."""
    title: str
    price_cad: float
    url: str
    location: str
    description: str
    date_posted: Optional[str] = None
    listing_id: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)
