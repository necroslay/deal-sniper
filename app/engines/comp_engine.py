"""
Comp Engine — aggregates eBay sold listings into percentile price bands.

Takes raw sold comps, strips outliers via IQR, computes median/mean/P25/P75
per (canonical product, condition grade) pair.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import CFG
from app.models.schema import CompBand, SoldComp

logger = logging.getLogger(__name__)


def compute_comp_band(
    comps: list[SoldComp],
    standard_name: str,
    condition: str = "unknown",
    window_days: Optional[int] = None,
) -> CompBand:
    """
    Compute a price band from a list of sold comps.

    Steps:
      1. Filter comps within the time window
      2. Compute IQR and remove outliers (outside Q1 − 1.5·IQR, Q3 + 1.5·IQR)
      3. Compute median, mean, P25, P75, min, max, std_dev

    Returns: CompBand with statistics.
    """
    window = window_days or CFG.comp.comp_window_days
    min_comps = CFG.comp.min_comps_for_valuation

    # Filter by time window
    cutoff = datetime.now(timezone.utc) - timedelta(days=window)
    filtered: list[SoldComp] = []
    for c in comps:
        try:
            sold_dt = datetime.fromisoformat(c.sold_date)
            if sold_dt >= cutoff:
                filtered.append(c)
        except (ValueError, TypeError):
            continue

    prices = [c.sold_price_cad for c in filtered if c.sold_price_cad > 0]
    if not prices:
        logger.warning(f"No valid sold prices for {standard_name} / {condition}")
        return CompBand(
            standard_name=standard_name,
            condition=condition,
            count=0,
            median_price_cad=0.0,
            mean_price_cad=0.0,
            p25_price_cad=0.0,
            p75_price_cad=0.0,
            min_price_cad=0.0,
            max_price_cad=0.0,
            window_days=window,
        )

    # Sort for percentile computation
    prices.sort()
    n = len(prices)

    # IQR outlier removal
    q1 = prices[n // 4]
    q3 = prices[(3 * n) // 4]
    iqr = q3 - q1
    iqr_mult = CFG.comp.iqr_multiplier
    lower_bound = q1 - iqr_mult * iqr
    upper_bound = q3 + iqr_mult * iqr

    cleaned = [p for p in prices if lower_bound <= p <= upper_bound]
    outliers_removed = len(prices) - len(cleaned)
    if outliers_removed > 0:
        logger.debug(f"Removed {outliers_removed} outliers from {len(prices)} comps for {standard_name}")

    if not cleaned:
        # All were outliers — fall back to original data
        cleaned = prices

    cleaned.sort()
    m = len(cleaned)

    band = CompBand(
        standard_name=standard_name,
        condition=condition,
        count=m,
        median_price_cad=statistics.median(cleaned),
        mean_price_cad=round(sum(cleaned) / m, 2),
        p25_price_cad=cleaned[m // 4],
        p75_price_cad=cleaned[(3 * m) // 4],
        min_price_cad=min(cleaned),
        max_price_cad=max(cleaned),
        std_dev=round(statistics.stdev(cleaned), 2) if m >= 2 else 0.0,
        window_days=window,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        f"CompBand: {standard_name} ({condition}) — "
        f"{m} comps, median=${band.median_price_cad:,.0f}, "
        f"P25=${band.p25_price_cad:,.0f} P75=${band.p75_price_cad:,.0f}"
    )

    return band


def has_sufficient_comps(band: CompBand) -> bool:
    """Check if the comp band has enough data for valuation."""
    return band.count >= CFG.comp.min_comps_for_valuation


def comp_volume_score(band: CompBand) -> float:
    """
    Liquidity score based on comp volume (0–100).

    100 = excellent (≥ 50 comps), 0 = insufficient (< min_comps).
    """
    min_comps = CFG.comp.min_comps_for_valuation
    if band.count < min_comps:
        return 0.0
    if band.count >= 50:
        return 100.0
    return min(100.0, (band.count - min_comps) / (50 - min_comps) * 100)