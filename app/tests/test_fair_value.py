"""Tests for valuation/fair_value.py — FMV calculation."""

import pytest

from app.valuation.fair_value import compute_fmv
from app.models.schema import CompBand


@pytest.fixture
def rtx_3080_band() -> CompBand:
    return CompBand(
        standard_name="RTX 3080", condition="used_good",
        count=20, median_price_cad=835.0, mean_price_cad=825.0,
        p25_price_cad=750.0, p75_price_cad=900.0,
        min_price_cad=650.0, max_price_cad=1050.0,
    )


class TestFairValue:
    def test_fmv_median_times_multiplier(self, rtx_3080_band):
        # used_good multiplier = 0.82
        # FMV = 835 × 0.82 × 1.0 = 684.70
        result = compute_fmv(rtx_3080_band, "used_good", trend_multiplier=1.0)
        assert result.fmv_cad == pytest.approx(684.70, abs=1)

    def test_fmv_brand_new_higher(self, rtx_3080_band):
        # brand_new multiplier = 1.05
        # FMV = 835 × 1.05 = 876.75
        result = compute_fmv(rtx_3080_band, "brand_new")
        assert result.fmv_cad > rtx_3080_band.median_price_cad

    def test_fmv_for_parts_low(self, rtx_3080_band):
        # for_parts multiplier = 0.30
        # FMV = 835 × 0.30 = 250.50
        result = compute_fmv(rtx_3080_band, "for_parts")
        assert result.fmv_cad == pytest.approx(250.50, abs=1)

    def test_fmv_with_adjustments(self, rtx_3080_band):
        result = compute_fmv(rtx_3080_band, "used_good",
            adjustments=[-30.0, -15.0])  # missing charger, no box
        base = 835.0 * 0.82  # 684.70
        expected = base - 30.0 - 15.0  # 639.70
        assert result.fmv_cad == pytest.approx(expected, abs=1)

    def test_fmv_with_trend(self, rtx_3080_band):
        # 8% market decline
        result = compute_fmv(rtx_3080_band, "used_good", trend_multiplier=0.92)
        expected = 835.0 * 0.82 * 0.92
        assert result.fmv_cad == pytest.approx(expected, abs=1)

    def test_confidence_interval(self, rtx_3080_band):
        result = compute_fmv(rtx_3080_band, "used_good")
        low, high = result.fmv_confidence_interval
        assert low < result.fmv_cad < high
        # P25=750 * 0.82 = 615, P75=900 * 0.82 = 738
        assert low == pytest.approx(615.0, abs=5)
        assert high == pytest.approx(738.0, abs=5)

    def test_no_comps_returns_zero(self):
        empty = CompBand(
            standard_name="X", condition="unknown",
            count=0, median_price_cad=0, mean_price_cad=0,
            p25_price_cad=0, p75_price_cad=0,
            min_price_cad=0, max_price_cad=0,
        )
        result = compute_fmv(empty, "used_good")
        assert result.fmv_cad == 0.0
        assert result.comps_used == 0