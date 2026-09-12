from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional


@dataclass
class eBayListing:
    """Represents a single listing scraped from eBay Canada."""
    title: str
    price_cad: float
    url: str
    condition: str = ""
    location: str = ""
    description: str = ""
    date_posted: Optional[str] = None
    listing_id: Optional[str] = None
    image_urls: list[str] = field(default_factory=list)
    sold: bool = False
    sold_price_cad: Optional[float] = None
    sold_date: Optional[str] = None
    scraped_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> dict:
        return asdict(self)
