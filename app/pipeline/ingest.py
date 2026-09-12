"""
Pipeline — end-to-end orchestration.

Scrape → Normalize → Resolve → Comps → Value → Score → Decide → Notify
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.config import CFG
from app.models.schema import (
    CompBand,
    ConfidenceLevel,
    DealScore,
    FairValueEstimate,
    Platform,
    RawListing,
    RealizedValue,
    ValuationOutput,
)
from app.engines.resolver import resolve_product
from app.engines.condition import classify_condition
from app.engines.adjustments import detect_adjustments
from app.engines.listing_guard import assess_listing
from app.engines.comp_engine import compute_comp_band, has_sufficient_comps
from app.valuation.fair_value import compute_fmv, compute_fmv_from_retail
from app.valuation.realize import compute_erv
from app.valuation.score import compute_deal_score
from app.valuation.gate import passes_alert_gate
from app.alerting.notifier import Notifier
from app.db.store import Store

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """
    End-to-end pipeline: listing → canonical product → comps → value → score.

    Usage:
        pipeline = IngestionPipeline()
        output = pipeline.process(raw_listing, comps=[...])
        if output.deal_score.confidence_level.value in ("ALERT_IMMEDIATE", "ALERT_CAUTION"):
            pipeline.notify(output)
    """

    def __init__(self, notifier: Optional[Notifier] = None, store: Optional[Store] = None):
        self.notifier = notifier or Notifier()
        self.store = store

    def process(
        self,
        raw_listing: RawListing,
        sold_comps: list = None,
        comp_band: Optional[CompBand] = None,
        travel_distance_km: float = 0.0,
        persist: bool = True,
    ) -> ValuationOutput:
        """
        Run the full pipeline on a single listing.

        Args:
            raw_listing: The raw listing from a marketplace
            sold_comps: Optional list of SoldComp objects for comp aggregation
            comp_band: Optional pre-computed CompBand (skips aggregation)
            travel_distance_km: Distance to pickup location

        Returns: ValuationOutput with all pipeline stages.
        """
        decision_id = str(uuid.uuid4())

        # Pre-filter: skip junk / unrealistic listings
        skip_reason = self._pre_filter(raw_listing)

        # Stage 1: Resolve canonical product
        product = resolve_product(raw_listing.title)

        # Stage 2: Classify condition
        condition_result = classify_condition(
            raw_listing.title, raw_listing.description or ""
        )

        # Guard: reject listings that merely *mention* a product — an accessory,
        # "X for PS5", a component inside a laptop, or a multi-item lot. These
        # would otherwise be valued as if they were the product itself.
        guard_reason = assess_listing(raw_listing.title, product)
        if guard_reason:
            skip_reason = skip_reason or guard_reason

        # If pre-filter says skip, return zero-score output immediately
        if skip_reason:
            logger.info(
                f"Pipeline [{decision_id[:8]}]: SKIP ({skip_reason}) "
                f"{raw_listing.title[:60]!r}"
            )
            empty_band = CompBand(
                standard_name=product.standard_name,
                condition=condition_result.grade.value,
                count=0, median_price_cad=0.0, mean_price_cad=0.0,
                p25_price_cad=0.0, p75_price_cad=0.0,
                min_price_cad=0.0, max_price_cad=0.0,
            )
            output = ValuationOutput(
                raw_listing=raw_listing, canonical_product=product,
                fair_value=FairValueEstimate(standard_name=product.standard_name,
                    fmv_cad=0.0, fmv_confidence_interval=(0.0, 0.0),
                    condition=condition_result.grade.value, condition_confidence=0.0, comps_used=0),
                realized_value=RealizedValue(fmv_cad=0.0, platform_fees_cad=0.0,
                    shipping_cost_cad=0.0, return_reserve_cad=0.0, erv_cad=0.0),
                deal_score=DealScore(score=0.0, margin_score=0.0, liquidity_score=0.0,
                    urgency_score=0.0, confidence_score=0.0, penalty_total=0.0,
                    expected_profit_cad=0.0, margin_pct=0.0,
                    confidence_level=ConfidenceLevel.suppress,
                    asking_price_cad=raw_listing.price_cad, fmv_cad=0.0, erv_cad=0.0,
                    risk_flags=[skip_reason]),
                pipeline_version="1.0.0",
            )
            if persist and self.store:
                try:
                    # Persist the listing *before* its decision. A rejected
                    # listing is still worth keeping for audit, and the decision
                    # row's ``listing_id`` must always resolve to a parent listing
                    # (Postgres enforces this with a FK that SQLite silently
                    # ignores — without this, 169 decisions were orphaned).
                    self.store.observe_listing(raw_listing, skip_reason=skip_reason)
                    self.store.insert_decision(output)
                except Exception:
                    pass
            return output

        # Stage 3: Comp aggregation (or use provided band)
        if comp_band:
            band = comp_band
        elif sold_comps:
            band = compute_comp_band(
                sold_comps,
                standard_name=product.standard_name,
                condition=condition_result.grade.value,
            )
        elif self.store:
            # Try to load comps from database
            db_comps = self.store.get_sold_comps(standard_name=product.standard_name, max_days_old=90)
            if db_comps:
                band = compute_comp_band(
                    db_comps,
                    standard_name=product.standard_name,
                    condition=condition_result.grade.value,
                )
            else:
                band = CompBand(
                    standard_name=product.standard_name,
                    condition=condition_result.grade.value,
                    count=0, median_price_cad=0.0, mean_price_cad=0.0,
                    p25_price_cad=0.0, p75_price_cad=0.0,
                    min_price_cad=0.0, max_price_cad=0.0,
                )
        else:
            # No comps — empty band
            band = CompBand(
                standard_name=product.standard_name,
                condition=condition_result.grade.value,
                count=0,
                median_price_cad=0.0,
                mean_price_cad=0.0,
                p25_price_cad=0.0,
                p75_price_cad=0.0,
                min_price_cad=0.0,
                max_price_cad=0.0,
            )

        # Stage 4: Fair Market Value
        # Apply item-level deductions (missing box/charger, cosmetic damage, ...)
        # configured in CFG.item_adjustments and detected from the listing text.
        adjustments = detect_adjustments(
            raw_listing.title, raw_listing.description or ""
        )

        fair_value = None
        if band.count > 0:
            # Preferred basis: observed completed sales.
            fair_value = compute_fmv(
                comp_band=band,
                condition=condition_result.grade.value,
                trend_multiplier=1.0,
                adjustments=adjustments,
            )
        elif CFG.retail.enabled and self.store:
            # No sold comps for this product. Fall back to the new-retail
            # ceiling (FMV = retail × M_condition), clearly tagged
            # basis="retail" and capped below ALERT_IMMEDIATE downstream.
            retail_quote = self.store.get_retail_price(
                product.standard_name, max_age_days=CFG.retail.max_age_days)
            if retail_quote:
                fair_value = compute_fmv_from_retail(
                    retail_price_cad=retail_quote.price_cad,
                    condition=condition_result.grade.value,
                    trend_multiplier=1.0,
                    adjustments=adjustments,
                )

        if fair_value is None:
            # No comps and no retail reference: FMV is zero (and flagged).
            fair_value = compute_fmv(
                comp_band=band,
                condition=condition_result.grade.value,
                trend_multiplier=1.0,
                adjustments=adjustments,
            )

        # Stage 5: Expected Resale Value (realization costs)
        platform = raw_listing.source
        shipping_cost = 0.0
        realized_value = compute_erv(
            fmv_cad=fair_value.fmv_cad,
            platform=platform,
            shipping_cost_cad=shipping_cost,
            category=product.category,
        )

        # Stage 6: Deal Score
        # Compute days since posted
        days_since = None
        if raw_listing.date_posted:
            try:
                posted = datetime.fromisoformat(raw_listing.date_posted.replace("Z", "+00:00"))
                days_since = (datetime.now(timezone.utc) - posted).days
            except (ValueError, TypeError):
                pass

        # Collect risk flags from condition uncertainty
        risk_flags = []
        if condition_result.grade.value == "unknown":
            risk_flags.append("condition_uncertainty")
        if condition_result.signals:
            risk_flags.extend(condition_result.signals)

        deal_score = compute_deal_score(
            asking_price_cad=raw_listing.price_cad,
            fair_value=fair_value,
            realized_value=realized_value,
            comp_band=band,
            condition_confidence=condition_result.confidence,
            days_since_posted=days_since,
            travel_distance_km=travel_distance_km,
            platform=platform,
            risk_flags=risk_flags,
        )

        output = ValuationOutput(
            raw_listing=raw_listing,
            canonical_product=product,
            fair_value=fair_value,
            realized_value=realized_value,
            deal_score=deal_score,
            pipeline_version="1.0.0",
        )

        logger.info(
            f"Pipeline [{decision_id[:8]}]: {product.standard_name} | "
            f"ask=${raw_listing.price_cad:,.0f} fmv=${fair_value.fmv_cad:,.0f} "
            f"score={deal_score.score:.0f} confidence={deal_score.confidence_level.value}"
        )

        # Persist to database
        if persist and self.store:
            try:
                # observe_listing both inserts a new listing and refreshes the
                # lifecycle tracking columns (last_seen_at, miss_count) on repeat
                # sightings — that is what makes disappearance detectable.
                self.store.observe_listing(raw_listing)
                self.store.insert_decision(output)
            except Exception as e:
                logger.warning(f"Failed to persist decision: {e}")

        return output

    def should_alert(self, output: ValuationOutput) -> bool:
        """Check if this output should trigger an alert based on the MVP gate.

        Delegates to the single shared implementation in ``valuation/gate.py``.
        """
        return passes_alert_gate(output)

    def notify(self, output: ValuationOutput) -> bool:
        """Send alert via all configured channels."""
        return self.notifier.send_alert(output)

    def _pre_filter(self, listing: RawListing) -> Optional[str]:
        """Check if a listing should be skipped. Returns None if OK, or a reason string."""
        # Price too low (trade posts, $1, free, contact for price)
        if listing.price_cad < 10:
            return "price_too_low"

        # Price too high (scam or mis-categorized)
        if listing.price_cad > 100000:
            return "price_too_high"

        # Title too short (garbage posts)
        if len(listing.title.strip()) < 5:
            return "title_too_short"

        # Contact for price / no price
        title_lower = listing.title.lower()
        if "contact" in title_lower and "price" in title_lower:
            return "contact_for_price"

        # Check for "for trade" / "trade only" posts
        if "trade" in title_lower and "sell" not in title_lower and "for sale" not in title_lower:
            if listing.price_cad < 50:
                return "trade_post"

        # Check for services / buying services (not actual products)
        if "buying service" in title_lower or "we buy" in title_lower:
            return "service_not_product"

        return None