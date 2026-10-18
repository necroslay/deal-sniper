"""Tests for ml/features.py — the price feature table."""

from __future__ import annotations

import pytest

from app.ml import features as feat


class TestBuildPriceRows:
    def test_keeps_a_real_gpu(self, make_stored_row):
        rows = feat.build_price_rows([make_stored_row()])
        assert len(rows) == 1
        assert rows[0].product == "RTX 3080"
        assert rows[0].category == "gpu"

    def test_drops_what_the_guard_rejects(self, make_stored_row):
        rows = feat.build_price_rows(
            [make_stored_row(title="RTX 4090 water block cooler")])
        assert rows == []

    def test_drops_unknown_products(self, make_stored_row):
        assert feat.build_price_rows([make_stored_row(title="Wooden desk")]) == []

    def test_drops_non_positive_prices(self, make_stored_row):
        assert feat.build_price_rows([make_stored_row(price=0)]) == []

    def test_drops_unparseable_dates(self, make_stored_row):
        row = make_stored_row()
        row["date_posted"] = "not a date"
        assert feat.build_price_rows([row]) == []

    def test_reresolves_instead_of_trusting_the_stored_key(self, make_stored_row):
        """A stale product_key must not leak into the features.

        Rows scraped before the bare-alias fix carry ``iPhone 15`` for a Dell
        XPS 15; trusting the stored column would train on that.
        """
        row = make_stored_row(title="Dell XPS 15 7590 Laptop",
                              product_key="iPhone 15")
        assert feat.build_price_rows([row]) == []

    def test_age_is_measured_from_scraped_at(self, make_stored_row):
        rows = feat.build_price_rows(
            [make_stored_row(days_ago=10, scraped_days_ago=2)])
        assert rows[0].age_days == pytest.approx(8.0, abs=0.01)

    def test_flags_a_new_condition_title(self, make_stored_row):
        rows = feat.build_price_rows(
            [make_stored_row(title="Nvidia RTX 3080 brand new sealed")])
        assert rows[0].is_new == 1

    def test_counts_images(self, make_stored_row):
        rows = feat.build_price_rows(
            [make_stored_row(images='["a.jpg", "b.jpg", "c.jpg"]')])
        assert rows[0].n_images == 3


class TestTrimOutliers:
    def _rows(self, make_stored_row, prices):
        return feat.build_price_rows([
            make_stored_row(listing_id=str(i), price=float(p))
            for i, p in enumerate(prices)])

    def test_drops_placeholder_and_absurd_prices(self, make_stored_row):
        rows = self._rows(make_stored_row,
                          [600, 620, 640, 660, 680, 700, 720, 1, 9000])
        kept = [r.price_cad for r in feat.trim_outliers(rows)]
        assert 1.0 not in kept
        assert 9000.0 not in kept
        assert 700.0 in kept

    def test_keeps_everything_when_prices_are_tight(self, make_stored_row):
        rows = self._rows(make_stored_row, [600, 620, 640, 660, 680, 700])
        assert len(feat.trim_outliers(rows)) == 6

    def test_empty(self):
        assert feat.trim_outliers([]) == []


class TestTemporalSplit:
    def _rows(self, make_stored_row):
        return feat.build_price_rows([
            make_stored_row(listing_id=f"l{d}", days_ago=d)
            for d in range(1, 11)])

    def test_oldest_train_newest_test(self, make_stored_row):
        train, test = feat.temporal_split(self._rows(make_stored_row), 0.3)
        assert len(test) == 3
        assert max(r.posted_at for r in train) <= min(r.posted_at for r in test)

    def test_no_listing_appears_on_both_sides(self, make_stored_row):
        train, test = feat.temporal_split(self._rows(make_stored_row), 0.3)
        assert {r.listing_id for r in train}.isdisjoint(
            {r.listing_id for r in test})

    def test_is_deterministic(self, make_stored_row):
        rows = self._rows(make_stored_row)
        first = [r.listing_id for r in feat.temporal_split(rows, 0.3)[0]]
        second = [r.listing_id for r in feat.temporal_split(rows, 0.3)[0]]
        assert first == second

    def test_empty(self):
        assert feat.temporal_split([], 0.25) == ([], [])
