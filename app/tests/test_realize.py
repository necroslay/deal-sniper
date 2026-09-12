"""Tests for valuation/realize.py — Expected Resale Value calculation."""

import pytest

from app.valuation.realize import compute_erv
from app.models.schema import Platform


class TestRealize:
    def test_erv_ebay_deducts_fees(self):
        """FMV=800, eBay fees=13.25%, no shipping, return_reserve=3%"""
        result = compute_erv(fmv_cad=800.0, platform=Platform.ebay)
        # fees = 800 * 0.1325 = 106.00
        # reserve = 800 * 0.03 = 24.00
        # ERV = 800 - 106 - 0 - 24 = 670.00
        assert result.platform_fees_cad == pytest.approx(106.0, abs=0.5)
        assert result.return_reserve_cad == pytest.approx(24.0, abs=0.5)
        assert result.erv_cad == pytest.approx(670.0, abs=1)

    def test_erv_local_zero_fees(self):
        """FMV=800, local pickup = 0% fees"""
        result = compute_erv(fmv_cad=800.0, platform=Platform.kijiji)
        assert result.platform_fees_cad == 0.0
        # ERV = 800 - 0 - 0 - 24 = 776
        assert result.erv_cad == pytest.approx(776.0, abs=1)

    def test_erv_with_shipping(self):
        """FMV=800, eBay fees, $20 shipping"""
        result = compute_erv(fmv_cad=800.0, platform=Platform.ebay, shipping_cost_cad=20.0)
        # ERV = 800 - 106 - 20 - 24 = 650
        assert result.shipping_cost_cad == 20.0
        assert result.erv_cad == pytest.approx(650.0, abs=1)

    def test_erv_gpu_category_higher_reserve(self):
        """GPU category = 5% return reserve"""
        result = compute_erv(fmv_cad=1000.0, platform=Platform.ebay, category="gpu")
        # reserve = 1000 * 0.05 = 50
        assert result.return_reserve_cad == pytest.approx(50.0, abs=0.5)

    def test_erv_facebook_zero_fees(self):
        result = compute_erv(fmv_cad=500.0, platform=Platform.facebook)
        assert result.platform_fees_cad == 0.0

    def test_erv_structure(self):
        result = compute_erv(fmv_cad=1000.0, platform=Platform.ebay)
        assert result.fmv_cad == 1000.0
        assert result.platform_fees_cad > 0
        assert result.return_reserve_cad > 0
        assert result.erv_cad < result.fmv_cad