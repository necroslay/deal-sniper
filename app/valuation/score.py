"""
Deal Score — computes deal profitability score from valuation components.

deal_score = w_m·S_margin + w_l·S_liquidity + w_u·S_urgency + w_c·S_confidence − Σ penalties
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.config import CFG
from app.models.schema import (
    CompBand,
    ConfidenceLevel,
    DealScore,
    FairValueEstimate,
    Platform,
    RealizedValue,
)
from app.engines.comp_engine import comp_volume_score

logger = logging.getLogger(__name__)


def compute_deal_score(
    asking_price_cad: float,
    fair_value: FairValueEstimate,
    realized_value: RealizedValue,
    comp_band: CompBand,
    condition_confidence: float = 1.0,
    days_since_posted: Optional[int] = None,
    travel_distance_km: float = 0.0,
    platform: Platform = Platform.ebay,
    risk_flags: Optional[list[str]] = None,
) -> DealScore:
    """
    Compute the final deal score (0–100) for a listing.

    Args:
        asking_price_cad: The seller's asking price
        fair_value: Fair Market Value estimate
        realized_value: Expected Resale Value after costs
        comp_band: Comp statistics (for liquidity score)
        condition_confidence: How sure we are about condition (0.0–1.0)
        days_since_posted: Age of listing in days
        travel_distance_km: How far the pickup is
        platform: Source platform (affects fee calculation)
        risk_flags: Additional risk signals

    Returns: DealScore with all component scores and metadata.
    """
    risk = list(risk_flags or [])
    erv = realized_value.erv_cad
    fmv = fair_value.fmv_cad

    # ── Expected Profit ─────────────────────────────────────────────────────
    travel_cost = _compute_travel_cost(travel_distance_km)
    holding_cost = _compute_holding_cost(fmv)
    expected_profit = round(erv - asking_price_cad - travel_cost - holding_cost, 2)
    margin_pct = 0.0
    if erv > 0:
        margin_pct = round((expected_profit / erv) * 100, 1)

    # ── Margin Score (0–100) ───────────────────────────────────────────────
    margin_score = min(100.0, expected_profit / erv * 100) if erv > 0 else 0.0
    margin_score = max(0.0, margin_score)

    # ── Liquidity Score (0–100) ─────────────────────────────────────────────
    liquidity_score = comp_volume_score(comp_band)

    # ── Urgency Score (0–100) ──────────────────────────────────────────────
    urgency_score = 50.0
    if days_since_posted is not None:
        if days_since_posted <= 1: urgency_score = 90.0
        elif days_since_posted <= 3: urgency_score = 75.0
        elif days_since_posted <= 7: urgency_score = 60.0
        elif days_since_posted <= 14: urgency_score = 40.0
        elif days_since_posted <= 30: urgency_score = 25.0
        else: urgency_score = 10.0

    # ── Confidence Score (0–100) ───────────────────────────────────────────
    confidence_base = 70.0
    if comp_band.count >= 20: confidence_base = 90.0
    elif comp_band.count >= 10: confidence_base = 80.0
    elif comp_band.count >= 5: confidence_base = 65.0
    else: confidence_base = 40.0
    confidence_score = confidence_base * condition_confidence

    # ── Penalties ──────────────────────────────────────────────────────────
    penalty_total = _compute_penalties(risk, travel_distance_km)

    # ── Total Score ────────────────────────────────────────────────────────
    w = CFG.score_weights
    score = (
        w.margin_weight * margin_score
        + w.liquidity_weight * liquidity_score
        + w.urgency_weight * urgency_score
        + w.confidence_weight * confidence_score
        + penalty_total
    )
    score = max(0.0, min(100.0, score))

    # ── Confidence Level ──────────────────────────────────────────────────
    avg_confidence = (confidence_score / 100.0)
    confidence_level = _assign_confidence_level(
        avg_confidence, comp_band.count, margin_score
    )

    # ── Recommendations ───────────────────────────────────────────────────
    recommendations = _generate_recommendations(
        score, margin_pct, expected_profit, risk, travel_distance_km
    )

    deal_score = DealScore(
        score=round(score, 1),
        margin_score=round(margin_score, 1),
        liquidity_score=round(liquidity_score, 1),
        urgency_score=round(urgency_score, 1),
        confidence_score=round(confidence_score, 1),
        penalty_total=round(penalty_total, 1),
        expected_profit_cad=expected_profit,
        margin_pct=margin_pct,
        confidence_level=confidence_level,
        asking_price_cad=asking_price_cad,
        fmv_cad=fmv,
        erv_cad=erv,
        risk_flags=risk,
        recommendations=recommendations,
        computed_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        f"DealScore: {score:.0f}/100 | profit=${expected_profit:,.0f} "
        f"({margin_pct:.0f}% margin) | confidence={confidence_level.value}"
    )

    return deal_score


def _compute_travel_cost(distance_km: float) -> float:
    cfg = CFG.travel_costs
    if distance_km <= cfg.free_radius_km:
        return 0.0
    billable_km = distance_km - cfg.free_radius_km
    return round(cfg.flat_base + billable_km * cfg.per_km_beyond, 2)


def _compute_holding_cost(fmv_cad: float) -> float:
    cfg = CFG.holding_costs
    labor_hours = cfg.testing_hours_default + cfg.cleaning_hours + cfg.photo_listing_hours
    labor_cost = labor_hours * cfg.hourly_labor_rate_cad
    capital_cost = fmv_cad * cfg.annual_capital_cost_pct / 12
    return round(labor_cost + capital_cost, 2)


def _compute_penalties(risk_flags: list[str], travel_distance_km: float) -> float:
    total = 0.0
    penalties_map = {
        "scam_risk": CFG.penalties.scam_risk,
        "condition_uncertainty": CFG.penalties.condition_uncertainty,
        "vague_bundle": CFG.penalties.vague_bundle,
        "unrealistic_price": CFG.penalties.unrealistic_price,
    }
    for flag in risk_flags:
        if flag in penalties_map:
            total += penalties_map[flag]
    if travel_distance_km > 30:
        total += CFG.penalties.transit_friction_high
    elif travel_distance_km > 10:
        total += CFG.penalties.transit_friction_medium
    return total


def _assign_confidence_level(avg_confidence: float, comp_count: int, margin_score: float) -> ConfidenceLevel:
    gate = CFG.alert_gate
    if avg_confidence >= gate.confidence_alert_immediate and comp_count >= gate.min_comps and margin_score >= 20:
        return ConfidenceLevel.alert_immediate
    if avg_confidence >= gate.confidence_alert_caution and comp_count >= gate.min_comps:
        return ConfidenceLevel.alert_caution
    if avg_confidence >= gate.confidence_review:
        return ConfidenceLevel.review
    return ConfidenceLevel.suppress


def _generate_recommendations(score: float, margin_pct: float, expected_profit: float, risk_flags: list[str], travel_distance: float) -> list[str]:
    recs = []
    if score >= 80 and expected_profit > 100:
        recs.append("High-confidence deal — consider immediate action")
    elif score >= 60:
        recs.append("Moderate deal — investigate condition details")
    else:
        recs.append("Low score — proceed with caution or skip")
    if margin_pct >= 30:
        recs.append(f"Strong margin ({margin_pct:.0f}%)")
    elif margin_pct < 10 and margin_pct > 0:
        recs.append(f"Thin margin ({margin_pct:.0f}%)")
    if travel_distance > 30:
        recs.append(f"Far pickup ({travel_distance:.0f} km)")
    if "scam_risk" in risk_flags:
        recs.append("⚠ Potential scam risk — verify carefully")
    if "condition_uncertainty" in risk_flags:
        recs.append("Ask for detailed condition photos")
    return recs