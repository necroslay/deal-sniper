"""Tests for ml/retail.py — the new-retail ceiling."""

from __future__ import annotations

import pytest

from app.ml.retail import load_retail, retail_for


class TestLoadRetail:
    def test_missing_file_is_not_an_error(self, tmp_path):
        """The ceiling is optional: a model run must not fail without it."""
        assert load_retail(tmp_path / "absent.csv") == {}

    def test_parses_rows(self, tmp_path):
        path = tmp_path / "retail.csv"
        path.write_text("product,price_cad,observed_at\n"
                        "RTX 5080,1499,2026-09-01\n", encoding="utf-8")
        prices = load_retail(path)
        assert prices["RTX 5080"].price_cad == 1499.0
        assert prices["RTX 5080"].observed_at == "2026-09-01"

    def test_later_rows_win(self, tmp_path):
        path = tmp_path / "retail.csv"
        path.write_text("product,price_cad\nRTX 5080,1499\nRTX 5080,1399\n",
                        encoding="utf-8")
        assert load_retail(path)["RTX 5080"].price_cad == 1399.0

    def test_junk_rows_are_skipped(self, tmp_path):
        path = tmp_path / "retail.csv"
        path.write_text("product,price_cad\n"
                        "RTX 5080,not-a-number\n"
                        ",1499\n"
                        "RTX 4090,0\n"
                        "RTX 3080,650\n", encoding="utf-8")
        prices = load_retail(path)
        assert set(prices) == {"RTX 3080"}


class TestRetailFor:
    def test_known_product(self):
        from app.ml.retail import RetailPrice
        prices = {"RTX 5080": RetailPrice("RTX 5080", 1499.0)}
        assert retail_for(prices, "RTX 5080") == 1499.0

    def test_unknown_product_is_none(self):
        assert retail_for({}, "RTX 5080") is None


class TestRetailForSemantics:
    def test_missing_reference_is_none_not_zero(self):
        """None is what makes the ceiling optional; 0 would clamp everything."""
        assert retail_for({}, "RTX 5080") is None
