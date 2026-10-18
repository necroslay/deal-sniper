"""Tests for eval/price_harness.py — scoring, ceiling, and the MAE gate."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.eval import price_harness as ph
from app.ml.retail import RetailPrice


class _BandPredictor:
    """Predicts a constant band around a fixed mid, for deterministic scoring."""

    name = "fixed-band"
    quantiles = (0.25, 0.50, 0.75)

    def __init__(self, mid: float):
        self.mid = mid

    def fit(self, rows):
        return self

    def predict_band(self, rows):
        count = len(rows)
        return (np.full(count, self.mid * 0.8), np.full(count, float(self.mid)),
                np.full(count, self.mid * 1.2))


class TestCapAtRetail:
    def test_only_rows_with_a_reference_are_capped(self, make_price_row):
        rows = [make_price_row(product="A"), make_price_row(product="B")]
        retail = {"A": RetailPrice("A", 55.0)}
        low, mid, high, capped = ph.cap_at_retail(
            [40.0, 500.0], [50.0, 600.0], [60.0, 700.0], rows, retail)
        assert capped == 1
        # A is clamped at its ceiling; B has no reference and is untouched.
        assert (low[0], mid[0], high[0]) == (40.0, 50.0, 55.0)
        assert (low[1], mid[1], high[1]) == (500.0, 600.0, 700.0)

    def test_clamps_at_the_ceiling(self, make_price_row):
        rows = [make_price_row(product="A")]
        retail = {"A": RetailPrice("A", 550.0)}
        low, mid, high, capped = ph.cap_at_retail(
            [500.0], [600.0], [700.0], rows, retail)
        assert capped == 1
        assert (low[0], mid[0], high[0]) == (500.0, 550.0, 550.0)

    def test_missing_reference_leaves_the_band_untouched(self, make_price_row):
        rows = [make_price_row(product="A")]
        low, mid, high, capped = ph.cap_at_retail(
            [500.0], [600.0], [700.0], rows, {})
        assert capped == 0
        assert (low[0], mid[0], high[0]) == (500.0, 600.0, 700.0)


class TestEvaluatePredictor:
    def test_perfect_median_scores_zero_mae_and_full_coverage(self, make_price_row):
        rows = [make_price_row(price=1000.0, listing_id="a"),
                make_price_row(price=1000.0, listing_id="b")]
        result = ph.evaluate_predictor(_BandPredictor(1000.0), rows, {})
        assert result.mae == pytest.approx(0.0)
        assert result.coverage == pytest.approx(1.0)   # band 800-1200 covers 1000
        assert result.n == 2

    def test_mae_reflects_the_gap(self, make_price_row):
        rows = [make_price_row(price=1100.0)]
        result = ph.evaluate_predictor(_BandPredictor(1000.0), rows, {})
        assert result.mae == pytest.approx(100.0)

    def test_pinball_is_reported_per_quantile(self, make_price_row):
        result = ph.evaluate_predictor(
            _BandPredictor(1000.0), [make_price_row(price=1000.0)], {})
        assert set(result.pinball) == {"p25", "p50", "p75"}


class TestCheckBaseline:
    def test_detects_an_mae_regression(self):
        assert len(ph.check_baseline([{"predictor": "m", "mae": 900.0}],
                                     [{"predictor": "m", "mae": 800.0}])) == 1

    def test_a_missing_predictor_is_a_regression(self):
        assert "missing" in ph.check_baseline(
            [], [{"predictor": "m", "mae": 800.0}])[0]

    def test_no_regression_when_equal(self):
        assert ph.check_baseline([{"predictor": "m", "mae": 800.0}],
                                 [{"predictor": "m", "mae": 800.0}]) == []


class TestCli:
    def _make_db(self, tmp_path, listings: int = 16) -> str:
        from app.db.store import Store
        from app.models.schema import Platform, RawListing

        db = str(tmp_path / "listings.db")
        store = Store(db=db).connect()
        now = datetime.now(timezone.utc)
        for index in range(listings):
            listing = RawListing(
                source=Platform.kijiji, listing_id=f"k{index}",
                title="Nvidia RTX 3080" if index % 2 else "ASUS RTX 3070",
                price_cad=700.0 + index, url=f"https://example.com/{index}",
                location="Toronto", description="used",
                date_posted=(now - timedelta(days=listings - index)).isoformat(),
            )
            store.observe_listing(listing)
        store.close()
        return db

    def test_runs_and_writes_reports(self, tmp_path):
        db = self._make_db(tmp_path)
        code = ph.main(["--db", db, "--reports-dir", str(tmp_path), "--no-model"])

        assert code == 0
        assert (tmp_path / "price_eval.md").exists()
        metrics = json.loads(
            (tmp_path / "price_metrics.json").read_text(encoding="utf-8"))
        assert metrics[0]["predictor"] == "product-quantile-baseline"
        assert metrics[0]["n"] > 0

    def test_the_gate_rerun_passes(self, tmp_path):
        db = self._make_db(tmp_path)
        args = ["--db", db, "--reports-dir", str(tmp_path), "--no-model"]

        assert ph.main(args) == 0
        assert ph.main(args + ["--check-baseline"]) == 0

    def test_empty_database_returns_2(self, tmp_path):
        from app.db.store import Store

        db = str(tmp_path / "empty.db")
        Store(db=db).connect().close()
        code = ph.main(["--db", db, "--reports-dir", str(tmp_path), "--no-model"])
        assert code == 2
