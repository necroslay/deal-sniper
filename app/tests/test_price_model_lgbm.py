"""Tests for the LightGBM quantile price model.

Skipped automatically when lightgbm is not installed — the harness falls back to
the per-product quantile baseline in that case.
"""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("lightgbm")

from app.ml.price_model import QuantilePriceModel  # noqa: E402


def _train(make_price_row, per_product: int = 20):
    """Two well-separated products, so 'did it learn anything' is unambiguous."""
    rows = []
    for index in range(per_product):
        rows.append(make_price_row(product="RTX 3080", price=700.0,
                                   days_ago=index + 1))
        rows.append(make_price_row(product="RTX 4090", price=2100.0,
                                   days_ago=index + 1))
    return rows


class TestQuantilePriceModel:
    def test_band_is_monotone(self, make_price_row):
        train = _train(make_price_row)
        low, mid, high = QuantilePriceModel(seed=1).fit(train).predict_band(
            train[:4])
        assert np.all(low <= mid)
        assert np.all(mid <= high)

    def test_separates_two_well_separated_products(self, make_price_row):
        model = QuantilePriceModel(seed=1).fit(_train(make_price_row))
        cheap = model.predict_band(
            [make_price_row(product="RTX 3080", price=700.0)])[1][0]
        dear = model.predict_band(
            [make_price_row(product="RTX 4090", price=2100.0)])[1][0]
        assert dear > cheap

    def test_unseen_product_does_not_raise(self, make_price_row):
        model = QuantilePriceModel(seed=1).fit(_train(make_price_row))
        band = model.predict_band([make_price_row(product="Mystery GPU")])
        assert np.isfinite(band[1][0])

    def test_predict_before_fit_raises(self, make_price_row):
        with pytest.raises(RuntimeError):
            QuantilePriceModel().predict_band([make_price_row()])

    def test_save_and_load_roundtrip(self, make_price_row, tmp_path):
        train = _train(make_price_row)
        model = QuantilePriceModel(seed=1).fit(train)

        reloaded = QuantilePriceModel.load(model.save(tmp_path / "price.pkl"))
        assert np.allclose(model.predict_band(train[:4])[1],
                           reloaded.predict_band(train[:4])[1])
        assert reloaded.quantiles == model.quantiles
