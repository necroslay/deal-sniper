"""
Fair Value — computes Fair Market Value from comp bands and condition.

FMV = median_30-90d(sold_comps) × M_condition × M_trend ± Σ adjustments
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import CFG
from app.models.schema import (
    CompBand,
    FairValueEstimate,
)

logger = logging.getLogger(__name__)


def compute_fmv(
    comp_band: CompBand,
    condition: str,
    trend_multiplier: float = 1.0,
    adjustments: list[float] | None = None,
) -> FairValueEstimate:
    """
    Compute Fair Market Value from comp bands + condition + adjustments.

    Args:
        comp_band: Price band from comp engine
        condition: Condition grade string (brand_new, used_good, etc.)
        trend_multiplier: M_trend — market macro trend factor (default 1.0)
        adjustments: List of item-level value adjustments (CAD)

    Returns: FairValueEstimate with FMV and confidence interval.
    """
    if comp_band.count == 0 or comp_band.median_price_cad == 0:
        logger.warning(f"No comp data for {comp_band.standard_name}, FMV=0")
        return FairValueEstimate(
            standard_name=comp_band.standard_name,
            fmv_cad=0.0,
            fmv_confidence_interval=(0.0, 0.0),
            condition=condition,
            condition_confidence=0.0,
            comps_used=0,
            comp_band=comp_band,
        )

    # Condition multiplier
    mult_map = {
        "brand_new": CFG.condition_multipliers.brand_new,
        "open_box_like_new": CFG.condition_multipliers.open_box_like_new,
        "used_good": CFG.condition_multipliers.used_good,
        "acceptable": CFG.condition_multipliers.acceptable,
        "for_parts": CFG.condition_multipliers.for_parts,
    }
    m_condition = mult_map.get(condition, 1.0)

    # FMV = median × M_condition × M_trend
    base_fmv = comp_band.median_price_cad * m_condition * trend_multiplier

    # Apply item-level adjustments
    adj = adjustments or []
    total_adjustment = sum(adj)
    fmv = round(base_fmv + total_adjustment, 2)

    # Confidence interval: P25–P75 scaled by condition multiplier × trend
    low = round(comp_band.p25_price_cad * m_condition * trend_multiplier + total_adjustment, 2)
    high = round(comp_band.p75_price_cad * m_condition * trend_multiplier + total_adjustment, 2)

    # Condition confidence — passed through from classifier
    condition_confidence = 1.0 if condition != "unknown" else 0.0

    logger.info(
        f"FMV: {comp_band.standard_name} (condition={condition}) = ${fmv:,.0f} "
        f"[${low:,.0f} – ${high:,.0f}] using {comp_band.count} comps"
    )

    return FairValueEstimate(
        standard_name=comp_band.standard_name,
        fmv_cad=fmv,
        fmv_confidence_interval=(low, high),
        condition=condition,
        condition_confidence=condition_confidence,
        comps_used=comp_band.count,
        comp_band=comp_band,
        basis="sold_comps",
    )


def compute_fmv_from_retail(
    retail_price_cad: float,
    condition: str,
    trend_multiplier: float = 1.0,
    adjustments: list[float] | None = None,
) -> FairValueEstimate:
    """Value a used item from its **new-retail** price.

    ``FMV = retail_price × M_condition × M_trend ± adjustments``

    This is a weaker basis than sold comps and is treated as such throughout:
    the result is tagged ``basis="retail"``, ``comps_used`` stays 0 (no comps
    were consulted — saying otherwise would be a lie), and the caller is
    expected to keep it out of ``ALERT_IMMEDIATE``.

    Retail acts as a **ceiling**: a rational buyer will not pay more used than
    the best new alternative, so this bounds value from above rather than
    measuring it. The condition multiplier supplies the new→used discount.

    Args:
        retail_price_cad: Cheapest current new-retail price for the product.
        condition: Condition grade string (brand_new, used_good, ...).
        trend_multiplier: M_trend — market macro trend factor (default 1.0).
        adjustments: Item-level value adjustments (CAD).

    Returns:
        FairValueEstimate with ``basis="retail"``, or ``basis="none"``/FMV 0
        when no usable retail price was supplied.
    """
    if retail_price_cad <= 0:
        logger.warning("No retail price supplied, FMV=0")
        return FairValueEstimate(
            standard_name="",
            fmv_cad=0.0,
            fmv_confidence_interval=(0.0, 0.0),
            condition=condition,
            condition_confidence=0.0,
            comps_used=0,
            basis="none",
        )

    mult_map = {
        "brand_new": CFG.condition_multipliers.brand_new,
        "open_box_like_new": CFG.condition_multipliers.open_box_like_new,
        "used_good": CFG.condition_multipliers.used_good,
        "acceptable": CFG.condition_multipliers.acceptable,
        "for_parts": CFG.condition_multipliers.for_parts,
    }
    m_condition = mult_map.get(condition, 1.0)

    total_adjustment = sum(adjustments or [])
    fmv = round(retail_price_cad * m_condition * trend_multiplier + total_adjustment, 2)

    # A new price pins the top of the range; the used discount below it is the
    # uncertain part, so the interval runs (fmv × low_pct, fmv).
    low = round(fmv * CFG.retail.fmv_interval_low_pct, 2)
    high = fmv

    condition_confidence = 1.0 if condition != "unknown" else 0.0

    logger.info(
        f"FMV(retail basis): retail=${retail_price_cad:,.0f} "
        f"(condition={condition}) = ${fmv:,.0f} [${low:,.0f} – ${high:,.0f}]"
    )

    return FairValueEstimate(
        standard_name="",
        fmv_cad=fmv,
        fmv_confidence_interval=(low, high),
        condition=condition,
        condition_confidence=condition_confidence,
        comps_used=0,
        basis="retail",
        retail_reference_cad=retail_price_cad,
    )