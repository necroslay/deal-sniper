"""Tests for valuation/score.py — Deal Score computation."""

import pytest

from app.config import CFG
from app.valuation.score import compute_deal_score, _compute_travel_cost, _compute_holding_cost
from app.models.schema import (
    CompBand, FairValueEstimate, RealizedValue,
    ConfidenceLevel, Platform, DealScore,
)


@pytest.fixture
def comp_band_3080() -> CompBand:
    return CompBand(standard_name="RTX 3080", condition="used_good",
        count=10, median_price_cad=835.0, mean_price_cad=825.0,
        p25_price_cad=750.0, p75_price_cad=900.0,
        min_price_cad=650.0, max_price_cad=1050.0,)


@pytest.fixture
def fair_value_3080(comp_band_3080) -> FairValueEstimate:
    return FairValueEstimate(standard_name="RTX 3080",
        fmv_cad=684.70, fmv_confidence_interval=(615.0, 738.0),
        condition="used_good", condition_confidence=0.85,
        comps_used=10, comp_band=comp_band_3080)


@pytest.fixture
def realized_3080() -> RealizedValue:
    return RealizedValue(fmv_cad=684.70, platform_fees_cad=0.0,
        shipping_cost_cad=0.0, return_reserve_cad=20.54, erv_cad=664.16)


class TestDealScore:
    def test_good_deal_scores_well(self, fair_value_3080, realized_3080, comp_band_3080):
        """Asking $550, ERV $664 → expected profit ~$70, margin ~10.5%.
        Conservative weighting gives score ~31, but confidence is ALERT_CAUTION."""
        result = compute_deal_score(
            asking_price_cad=550.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            days_since_posted=1,
            platform=Platform.kijiji,
        )
        assert 25 <= result.score <= 40
        assert result.expected_profit_cad > 50
        assert result.confidence_level == ConfidenceLevel.alert_caution

    def test_bad_deal_scores_low(self, fair_value_3080, realized_3080, comp_band_3080):
        """Asking $900, ERV $664 → profit negative. Should score low."""
        result = compute_deal_score(
            asking_price_cad=900.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            days_since_posted=30,
        )
        assert result.score < 40
        assert result.expected_profit_cad < 0

    def test_risk_flags_reduce_score(self, fair_value_3080, realized_3080, comp_band_3080):
        """Adding scam risk should lower the score."""
        clean = compute_deal_score(
            asking_price_cad=550.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            days_since_posted=1,
        )
        risky = compute_deal_score(
            asking_price_cad=550.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            days_since_posted=1,
            risk_flags=["scam_risk", "unrealistic_price"],
        )
        assert risky.score < clean.score
        assert "scam_risk" in risky.risk_flags

    def test_travel_penalties(self, fair_value_3080, realized_3080, comp_band_3080):
        """Long travel should lower score vs local."""
        local = compute_deal_score(
            asking_price_cad=550.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            travel_distance_km=2.0,
        )
        far = compute_deal_score(
            asking_price_cad=550.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
            condition_confidence=0.85,
            travel_distance_km=50.0,
        )
        assert far.score <= local.score
        assert far.expected_profit_cad < local.expected_profit_cad

    def test_always_returns_bounded_score(self, fair_value_3080, realized_3080, comp_band_3080):
        """Score should always be between 0 and 100."""
        result = compute_deal_score(
            asking_price_cad=999999.0,
            fair_value=fair_value_3080,
            realized_value=realized_3080,
            comp_band=comp_band_3080,
        )
        assert 0 <= result.score <= 100


class TestTravelCost:
    def test_free_within_radius(self):
        cost = _compute_travel_cost(3.0)
        assert cost == 0.0

    def test_cost_beyond_radius(self):
        # 15 km = 10 base + (15-5) * 0.75 = 10 + 7.50 = 17.50
        cost = _compute_travel_cost(15.0)
        assert cost == pytest.approx(17.50, abs=0.1)

    def test_high_distance(self):
        cost = _compute_travel_cost(100.0)
        expected = 10.0 + (100 - 5) * 0.75  # 10 + 71.25 = 81.25
        assert cost == pytest.approx(expected, abs=0.1)


class TestHoldingCost:
    def test_holding_cost_positive(self):
        cost = _compute_holding_cost(800.0)
        assert cost > 0
        # labor: (1+0.5+0.5) * 20 = 40
        # capital: 800 * 0.08 / 12 = 5.33
        # total: ~45.33
        assert cost == pytest.approx(45.33, abs=2)