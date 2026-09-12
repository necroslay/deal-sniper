"""
Realize — computes Expected Resale Value after realization costs.

ERV = FMV − platform_fees − shipping_subsidy − return_reserve
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import CFG
from app.models.schema import Platform, RealizedValue

logger = logging.getLogger(__name__)


def compute_erv(
    fmv_cad: float,
    platform: Platform = Platform.ebay,
    shipping_cost_cad: float = 0.0,
    category: str = "electronics",
) -> RealizedValue:
    """
    Compute Expected Resale Value from FMV minus realization costs.

    Args:
        fmv_cad: Fair Market Value
        platform: Resale platform (determines fee rate)
        shipping_cost_cad: Estimated shipping cost (0 for local pickup)
        category: Product category (determines return reserve %)

    Returns: RealizedValue with fee breakdown and ERV.
    """
    fee_map = {
        Platform.ebay: CFG.platform_fees.ebay_canada_pct,
        Platform.kijiji: CFG.platform_fees.kijiji_pct,
        Platform.facebook: CFG.platform_fees.facebook_marketplace_pct,
        Platform.craigslist: CFG.platform_fees.craigslist_pct,
    }
    fee_rate = fee_map.get(platform, 0.0)

    platform_fees = round(fmv_cad * fee_rate, 2)

    # Return reserve
    return_reserve_pct = CFG.return_reserve.default_pct
    if category in ("gpu", "laptop", "mobile", "console"):
        return_reserve_pct = CFG.return_reserve.electronics_max_pct
    return_reserve = round(fmv_cad * return_reserve_pct, 2)

    erv = round(fmv_cad - platform_fees - shipping_cost_cad - return_reserve, 2)

    logger.info(
        f"ERV: FMV=${fmv_cad:,.0f} − fees=${platform_fees:,.0f} − shipping=${shipping_cost_cad:,.0f} "
        f"− return_reserve=${return_reserve:,.0f} = ${erv:,.0f}"
    )

    return RealizedValue(
        fmv_cad=fmv_cad,
        platform_fees_cad=platform_fees,
        shipping_cost_cad=shipping_cost_cad,
        return_reserve_cad=return_reserve,
        erv_cad=erv,
    )