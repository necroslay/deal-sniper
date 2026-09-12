"""Tests for models/schema.py — Pydantic validation."""

import pytest
from pydantic import ValidationError

from app.models.schema import (
    RawListing, SoldComp, CompBand, FairValueEstimate,
    RealizedValue, DealScore, ValuationOutput, FeedbackEvent,
    ConfidenceLevel, Platform,
)


class TestRawListing:
    def test_valid_listing(self):
        listing = RawListing(source=Platform.ebay, listing_id="123",
            title="RTX 3080", price_cad=800.0, url="https://ebay.ca/itm/123")
        assert listing.source == Platform.ebay
        assert listing.price_cad == 800.0

    def test_defaults(self):
        listing = RawListing(source=Platform.kijiji, listing_id="456",
            title="Test", price_cad=100.0, url="https://kijiji.ca/456")
        assert listing.location == ""
        assert isinstance(listing.image_urls, list)

    def test_invalid_source(self):
        with pytest.raises(ValidationError):
            RawListing(source="invalid_source", listing_id="1",
                title="Test", price_cad=100.0, url="https://test.ca")


class TestSoldComp:
    def test_valid_sold_comp(self):
        comp = SoldComp(source_listing_id="ebay_1", title="RTX 3080",
            sold_price_cad=750.0, sold_date="2026-07-15T12:00:00")
        assert comp.sold_price_cad == 750.0

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            SoldComp(source_listing_id="1", title="Test")


class TestCompBand:
    def test_valid_band(self):
        band = CompBand(standard_name="RTX 3080", condition="used_good",
            count=20, median_price_cad=800.0, mean_price_cad=790.0,
            p25_price_cad=700.0, p75_price_cad=900.0,
            min_price_cad=650.0, max_price_cad=1050.0)
        assert band.median_price_cad == 800.0

    def test_empty_band(self):
        band = CompBand(standard_name="X", condition="unknown", count=0,
            median_price_cad=0, mean_price_cad=0, p25_price_cad=0,
            p75_price_cad=0, min_price_cad=0, max_price_cad=0)
        assert band.count == 0


class TestDealScore:
    def test_valid_score(self):
        score = DealScore(score=75.0, margin_score=80.0, liquidity_score=70.0,
            urgency_score=60.0, confidence_score=85.0, penalty_total=0.0,
            expected_profit_cad=150.0, margin_pct=20.0,
            confidence_level=ConfidenceLevel.alert_immediate,
            asking_price_cad=800.0, fmv_cad=950.0, erv_cad=900.0)
        assert score.score == 75.0
        assert score.confidence_level == ConfidenceLevel.alert_immediate


class TestFeedbackEvent:
    def test_valid_outcomes(self):
        for outcome in ["Good Lead", "Bad Match", "Too Risky", "Already Sold", "Bought", "Resold"]:
            event = FeedbackEvent(decision_id="abc-123", outcome=outcome)
            assert event.outcome == outcome

    def test_invalid_outcome(self):
        with pytest.raises(ValidationError):
            FeedbackEvent(decision_id="abc-123", outcome="Invalid")


class TestValuationOutput:
    def test_complete_output(self, rtx_3080_listing):
        from app.engines.resolver import resolve_product
        product = resolve_product(rtx_3080_listing.title)
        output = ValuationOutput(
            raw_listing=rtx_3080_listing, canonical_product=product,
            fair_value=FairValueEstimate(standard_name="RTX 3080",
                fmv_cad=800.0, fmv_confidence_interval=(700.0, 900.0),
                condition="used_good", condition_confidence=0.9, comps_used=15),
            realized_value=RealizedValue(fmv_cad=800.0, platform_fees_cad=0.0,
                shipping_cost_cad=0.0, return_reserve_cad=24.0, erv_cad=776.0),
            deal_score=DealScore(score=85.0, margin_score=90.0,
                liquidity_score=80.0, urgency_score=75.0, confidence_score=95.0,
                penalty_total=0.0, expected_profit_cad=200.0, margin_pct=25.0,
                confidence_level=ConfidenceLevel.alert_immediate,
                asking_price_cad=750.0, fmv_cad=800.0, erv_cad=776.0),
        )
        assert output.canonical_product.standard_name == "RTX 3080"
        assert output.deal_score.score == 85.0