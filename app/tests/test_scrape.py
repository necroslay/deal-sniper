"""Tests for app/scrape.py — dedup and the store-and-enrich step."""

from __future__ import annotations

from app.scrape import dedupe_by_url, store_listings


class TestDedupeByUrl:
    def test_duplicate_urls_collapse(self, make_listing):
        first = make_listing("a")
        duplicate = make_listing("b")
        duplicate.url = first.url
        assert len(dedupe_by_url([first, duplicate])) == 1

    def test_distinct_urls_survive(self, make_listing):
        assert len(dedupe_by_url([make_listing("a"), make_listing("b")])) == 2


class TestStoreListings:
    def test_stores_and_enriches_each_listing(self, store, make_listing):
        seen, new, skipped, products = store_listings(store, [
            make_listing("a", title="EVGA GeForce RTX 3080 FTW3"),
            make_listing("b", title="RTX 4090 power cable"),
        ])

        assert new == 2
        assert skipped == 0
        assert len(seen) == 2
        assert products["RTX 3080"] == 1

        rows = {r["source_listing_id"]: r for r in store.all_listings()}
        assert rows["a"]["product_key"] == "RTX 3080"
        # The guard flags the cable so the viewer can hide it.
        assert rows["b"]["flag_reason"] == "accessory_or_part"

    def test_second_pass_adds_nothing_new(self, store, make_listing):
        store_listings(store, [make_listing("a")])
        _, new, skipped, _ = store_listings(store, [make_listing("a")])
        assert new == 0
        assert skipped == 0
        assert store.counts()["total"] == 1

    def test_listings_without_an_id_are_skipped_and_counted(self, store, make_listing):
        """A missing id must not be filed under the blank key."""
        seen, new, skipped, _ = store_listings(store, [
            make_listing("a"),
            make_listing("", title="no id here"),
            make_listing("", title="also no id"),
        ])

        assert new == 1
        assert skipped == 2
        assert len(seen) == 1                      # not counted as seen
        assert store.counts()["total"] == 1        # nothing written for them

