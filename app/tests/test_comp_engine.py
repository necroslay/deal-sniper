"""Tests for engines/comp_engine.py — sold comps aggregation."""

import pytest

from app.engines.comp_engine import compute_comp_band, has_sufficient_comps, comp_volume_score
from app.models.schema import CompBand


class TestCompEngine:
    def test_median_price(self, rtx_3080_sold_comps):
        band = compute_comp_band(rtx_3080_sold_comps, "RTX 3080", "used_good")
        # Sorted: 650,680,700,720,750,780,800,810,820,830,840,850,860,870,890,900,920,950,980,1050
        # Median of 20 = average of 10th(830) and 11th(840) = 835
        assert band.count == 20
        assert band.median_price_cad == pytest.approx(835.0, abs=5)

    def test_iqr_outlier_removal(self, rtx_3080_outlier_comps):
        """
        Prices: 100,120,450,480,500,520,540,560,580,600,2000,2500
        Q1 = avg of 3rd(450) and 4th(480) = 465
        Q3 = avg of 9th(580) and 10th(600) = 590
        IQR = 590-465 = 125
        Lower = 465 - 1.5*125 = 277.5 → removes 100, 120
        Upper = 590 + 1.5*125 = 777.5 → removes 2000, 2500
        Result: 8 comps remaining: 450,480,500,520,540,560,580,600
        Median of 8 = avg of 4th(520) and 5th(540) = 530
        """
        band = compute_comp_band(rtx_3080_outlier_comps, "RTX 3080", "used_good")
        assert band.count == 8
        assert band.median_price_cad == pytest.approx(530.0, abs=2)

    def test_empty_comps(self):
        band = compute_comp_band([], "RTX 4090", "used_good")
        assert band.count == 0
        assert band.median_price_cad == 0.0

    def test_rtx_4090_median(self, rtx_4090_sold_comps):
        band = compute_comp_band(rtx_4090_sold_comps, "RTX 4090", "used_good")
        assert band.count == 15
        assert band.median_price_cad == pytest.approx(2600.0, abs=50)

    def test_ps5_median(self, ps5_sold_comps):
        band = compute_comp_band(ps5_sold_comps, "PlayStation 5", "used_good")
        assert band.count == 10
        assert band.median_price_cad == pytest.approx(435.0, abs=10)

    def test_p25_p75_percentiles(self, rtx_3080_sold_comps):
        band = compute_comp_band(rtx_3080_sold_comps, "RTX 3080", "used_good")
        # After IQR, should still be reasonable
        assert band.p25_price_cad < band.median_price_cad
        assert band.p75_price_cad > band.median_price_cad


class TestCompHelpers:
    def test_has_sufficient_comps(self):
        good = CompBand(standard_name="X", condition="y", count=5,
            median_price_cad=100, mean_price_cad=100,
            p25_price_cad=80, p75_price_cad=120,
            min_price_cad=50, max_price_cad=150)
        assert has_sufficient_comps(good) is True

        bad = CompBand(standard_name="X", condition="y", count=2,
            median_price_cad=100, mean_price_cad=100,
            p25_price_cad=80, p75_price_cad=120,
            min_price_cad=50, max_price_cad=150)
        assert has_sufficient_comps(bad) is False

    def test_comp_volume_score(self):
        low = CompBand(standard_name="X", condition="y", count=3,
            median_price_cad=100, mean_price_cad=100,
            p25_price_cad=80, p75_price_cad=120,
            min_price_cad=50, max_price_cad=150)
        assert comp_volume_score(low) == 0.0

        high = CompBand(standard_name="X", condition="y", count=60,
            median_price_cad=100, mean_price_cad=100,
            p25_price_cad=80, p75_price_cad=120,
            min_price_cad=50, max_price_cad=150)
        assert comp_volume_score(high) == 100.0