"""Tests for ml/price_model.py — the per-product quantile baseline.

The LightGBM model lives in ``test_price_model_lgbm.py`` and skips when the
dependency is absent; the baseline must run everywhere.
"""

from __future__ import annotations

import pytest

from app.ml.price_model import ProductQuantileBaseline


class TestProductQuantileBaseline:
    def test_band_is_monotone(self, make_price_row):
        train = [make_price_row(price=p) for p in (500, 600, 700, 800, 900)]
        low, mid, high = ProductQuantileBaseline().fit(train).predict_band(
            [make_price_row()])
        assert low[0] <= mid[0] <= high[0]

    def test_uses_each_products_own_band(self, make_price_row):
        train = ([make_price_row(product="RTX 3080", price=p)
                  for p in (500, 600, 700)]
                 + [make_price_row(product="RTX 4090", price=p)
                    for p in (2000, 2200, 2400)])
        baseline = ProductQuantileBaseline(min_rows=3).fit(train)

        mid_3080 = baseline.predict_band([make_price_row(product="RTX 3080")])[1][0]
        mid_4090 = baseline.predict_band([make_price_row(product="RTX 4090")])[1][0]
        assert mid_4090 > mid_3080
        assert baseline.products_covered == 2

    def test_thin_product_falls_back_to_the_global_band(self, make_price_row):
        train = [make_price_row(product="RTX 3080", price=p)
                 for p in (500, 600, 700, 800, 900)]
        baseline = ProductQuantileBaseline(min_rows=3).fit(train)

        mid = baseline.predict_band([make_price_row(product="Unseen GPU")])[1][0]
        assert mid == pytest.approx(700.0, abs=1.0)
        assert baseline.products_covered == 1

    def test_empty_input(self):
        low, mid, high = ProductQuantileBaseline().fit([]).predict_band([])
        assert len(low) == len(mid) == len(high) == 0
