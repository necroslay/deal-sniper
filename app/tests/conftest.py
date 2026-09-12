"""
Shared test fixtures for the valuation engine test suite.

Provides deterministic sold comp data for common GPU models.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest

from app.config import AppConfig, CFG
from app.models.schema import CompBand, Platform, RawListing, SoldComp


# ── Fixture: Reset config to defaults before each test ──────────────────────

@pytest.fixture(autouse=True)
def reset_config():
    """Reset the global config to defaults before each test."""
    import app.config as cfg_module
    cfg_module.CFG = AppConfig()
    yield


# ── Fixture: Sample sold comps for RTX 3080 ──────────────────────────────

@pytest.fixture
def rtx_3080_sold_comps() -> list[SoldComp]:
    """20 deterministic sold comps for RTX 3080 (used-good). Prices in CAD."""
    base_date = datetime.now(timezone.utc) - timedelta(days=30)
    comps = []
    prices = [
        650.0, 680.0, 700.0, 720.0, 750.0,   # 5 low
        780.0, 800.0, 810.0, 820.0, 830.0,   # 5 mid-low
        840.0, 850.0, 860.0, 870.0, 890.0,   # 5 mid-high
        900.0, 920.0, 950.0, 980.0, 1050.0,  # 5 high (one mild outlier at 1050)
    ]
    for i, price in enumerate(prices):
        comps.append(SoldComp(
            source=Platform.ebay,
            source_listing_id=f"ebay_3080_{i}",
            title=f"RTX 3080 {i}",
            sold_price_cad=price,
            sold_date=(base_date - timedelta(days=i)).isoformat(),
            condition="used_good",
            url=f"https://ebay.ca/itm/{i}",
        ))
    return comps


@pytest.fixture
def rtx_3080_outlier_comps() -> list[SoldComp]:
    """
    12 comps for RTX 3080 with extreme outliers to test IQR stripping.
    Prices: 100, 120, 450, 480, 500, 520, 540, 560, 580, 600, 2000, 2500
    Outliers should be: 100, 120 (low) and 2000, 2500 (high).
    """
    base_date = datetime.now(timezone.utc) - timedelta(days=20)
    comps = []
    prices = [100, 120, 450, 480, 500, 520, 540, 560, 580, 600, 2000, 2500]
    for i, price in enumerate(prices):
        comps.append(SoldComp(
            source=Platform.ebay,
            source_listing_id=f"ebay_outlier_{i}",
            title=f"RTX 3080 Outlier {i}",
            sold_price_cad=float(price),
            sold_date=(base_date - timedelta(days=i)).isoformat(),
            condition="used_good",
        ))
    return comps


@pytest.fixture
def rtx_4090_sold_comps() -> list[SoldComp]:
    """15 deterministic sold comps for RTX 4090."""
    base_date = datetime.now(timezone.utc) - timedelta(days=45)
    comps = []
    prices = [
        2200.0, 2350.0, 2400.0, 2450.0, 2500.0,
        2550.0, 2600.0, 2650.0, 2700.0, 2750.0,
        2800.0, 2850.0, 2900.0, 3000.0, 3200.0,
    ]
    for i, price in enumerate(prices):
        comps.append(SoldComp(
            source=Platform.ebay,
            source_listing_id=f"ebay_4090_{i}",
            title=f"RTX 4090 {i}",
            sold_price_cad=price,
            sold_date=(base_date - timedelta(days=i * 2)).isoformat(),
            condition="used_good",
        ))
    return comps


@pytest.fixture
def ps5_sold_comps() -> list[SoldComp]:
    """10 sold comps for PS5."""
    base_date = datetime.now(timezone.utc) - timedelta(days=30)
    comps = []
    prices = [350, 380, 400, 420, 430, 440, 450, 460, 480, 500]
    for i, price in enumerate(prices):
        comps.append(SoldComp(
            source=Platform.ebay,
            source_listing_id=f"ebay_ps5_{i}",
            title=f"PlayStation 5 {i}",
            sold_price_cad=float(price),
            sold_date=(base_date - timedelta(days=i)).isoformat(),
            condition="used_good",
        ))
    return comps


# ── Fixture: Sample Raw Listings ──────────────────────────────────────────

@pytest.fixture
def rtx_3080_listing() -> RawListing:
    """A typical RTX 3080 listing from Kijiji."""
    return RawListing(
        source=Platform.kijiji,
        listing_id="kijiji_3080_1",
        title="EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB",
        price_cad=750.0,
        url="https://www.kijiji.ca/v-rtx-3080/1",
        location="Toronto",
        description="Used for gaming, excellent condition, no mining. Original box.",
        date_posted=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
    )


@pytest.fixture
def rtx_4090_listing() -> RawListing:
    """A typical RTX 4090 listing from Facebook."""
    return RawListing(
        source=Platform.facebook,
        listing_id="fb_4090_1",
        title="ASUS RTX 4090 TUF Gaming OC Edition",
        price_cad=2000.0,
        url="https://facebook.com/marketplace/item/4090_1",
        location="Mississauga, ON",
        description="Like new, used for 2 months, still has warranty.",
        date_posted=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
    )


@pytest.fixture
def ps5_listing() -> RawListing:
    """A typical PS5 listing."""
    return RawListing(
        source=Platform.craigslist,
        listing_id="cl_ps5_1",
        title="PS5 Disc Version - Like New",
        price_cad=380.0,
        url="https://toronto.craigslist.org/ele/ps5_1",
        location="Toronto",
        description="PS5 disc version, barely used, comes with 2 controllers.",
        date_posted=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
    )