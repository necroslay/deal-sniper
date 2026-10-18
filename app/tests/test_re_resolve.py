"""Tests for app/re_resolve.py — bringing stale enrichment up to date.

``product_key`` / ``flag_reason`` are written at scrape time, so a resolver fix
never reaches rows already stored until they are re-enriched. This is the command
that repairs them.
"""

from __future__ import annotations

from app.re_resolve import re_resolve


class TestReResolve:
    def test_repairs_a_phantom_iphone_key(self, store, make_listing):
        """A Dell XPS 15 was filed as an iPhone 15 by the old bare-alias rule."""
        store.observe_listing(
            make_listing("k1", title="Dell XPS 15 7590 Laptop"),
            product_key="iPhone 15")

        summary = re_resolve(store)

        assert summary["phantom_iphones"] == 1
        assert summary["changed"] == 1
        assert not (store.all_listings()[0]["product_key"] or "").startswith(
            "iPhone")

    def test_adds_a_guard_flag_that_was_missing(self, store, make_listing):
        store.observe_listing(
            make_listing("k1", title="RTX 4090 water block cooler"),
            product_key="RTX 4090")

        re_resolve(store)

        assert store.all_listings()[0]["flag_reason"] == "accessory_or_part"

    def test_correctly_enriched_rows_are_left_alone(self, store, make_listing):
        store.observe_listing(make_listing("k1", title="EVGA RTX 3080 FTW3"),
                              product_key="RTX 3080")
        assert re_resolve(store)["changed"] == 0

    def test_dry_run_reports_but_writes_nothing(self, store, make_listing):
        store.observe_listing(
            make_listing("k1", title="Dell XPS 15 7590 Laptop"),
            product_key="iPhone 15")

        summary = re_resolve(store, dry_run=True)

        assert summary["changed"] == 1
        assert store.all_listings()[0]["product_key"] == "iPhone 15"

    def test_counts_every_listing(self, store, make_listing):
        store.observe_listing(make_listing("a"), product_key="RTX 3080")
        store.observe_listing(make_listing("b", title="ASUS RTX 3070"),
                              product_key="RTX 3070")
        assert re_resolve(store)["total"] == 2
