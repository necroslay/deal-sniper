"""Shared pytest fixtures for the scraper + viewer test suite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.store import Store
from app.models.schema import Platform, RawListing


@pytest.fixture
def store(tmp_path) -> Store:
    """A throwaway SQLite store, one per test."""
    s = Store(db=str(tmp_path / "test.db"))
    s.connect()
    yield s
    s.close()


@pytest.fixture
def make_listing():
    """Factory for ``RawListing``s so tests don't repeat boilerplate.

    Usage: ``make_listing("a", price=600.0)``
    """
    def _make(lid: str = "k1", source: Platform = Platform.kijiji,
              price: float = 500.0,
              title: str = "EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB",
              days_ago: int = 1) -> RawListing:
        return RawListing(
            source=source,
            listing_id=lid,
            title=title,
            price_cad=price,
            url=f"https://example.com/{lid}",
            location="Toronto",
            description="Used, good condition.",
            date_posted=(datetime.now(timezone.utc)
                         - timedelta(days=days_ago)).isoformat(),
        )
    return _make
