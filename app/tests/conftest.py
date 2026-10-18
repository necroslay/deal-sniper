"""Shared pytest fixtures for the scraper + viewer test suite."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import CFG, AppConfig
from app.db.store import Store
from app.engines import enrich
from app.models.schema import Platform, RawListing

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(autouse=True)
def reset_config():
    """Restore ``CFG`` to defaults (and drop cached models) around every test.

    Tuning parameters and the ``ml`` sub-config are read from a single global, so
    tests must not be able to leak a mutated value into the next one.
    """
    def _restore():
        CFG.__dict__.clear()
        CFG.__dict__.update(AppConfig().__dict__)
        enrich.reset_model_cache()

    _restore()
    yield
    _restore()


@pytest.fixture
def labels_csv() -> str:
    """Path to the tiny hand-labeled fixture dataset."""
    return str(FIXTURES / "labels_tiny.csv")


@pytest.fixture
def tiny_examples(labels_csv):
    """The fixture dataset loaded as resolver examples."""
    from app.ml.dataset import load_labeled_csv

    return load_labeled_csv(labels_csv, "product_label")


@pytest.fixture
def make_price_row():
    """Factory for ``PriceRow`` so price tests don't repeat boilerplate.

    Usage: ``make_price_row("RTX 4090", price=2100)``
    """
    from datetime import datetime, timedelta, timezone

    from app.ml.features import PriceRow

    def _make(product: str = "RTX 3080", price: float = 700.0,
              days_ago: int = 1, age_days: float = 2.0, category: str = "gpu",
              source: str = "kijiji", listing_id=None) -> PriceRow:
        posted = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return PriceRow(
            listing_id=listing_id or f"{product}|{price}|{days_ago}",
            product=product, category=category, source=source,
            price_cad=price, posted_at=posted.isoformat(), age_days=age_days,
            title_len=20, desc_len=10, n_images=1, is_new=0)

    return _make


@pytest.fixture
def make_stored_row():
    """Factory for a stored listing dict as the DB returns it."""
    from datetime import datetime, timedelta, timezone

    def _make(listing_id: str = "l1", title: str = "Nvidia RTX 3080",
              price: float = 700.0, days_ago: int = 3,
              scraped_days_ago: int = 1, source: str = "kijiji",
              product_key: str = "", images: str = "[]") -> dict:
        now = datetime.now(timezone.utc)
        return {
            "id": listing_id, "title": title, "price_cad": price,
            "source": source, "description": "used, good condition",
            "image_urls": images, "product_key": product_key,
            "flag_reason": None,
            "date_posted": (now - timedelta(days=days_ago)).isoformat(),
            "scraped_at": (now - timedelta(days=scraped_days_ago)).isoformat(),
        }

    return _make


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
