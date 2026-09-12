"""Tests for db/store.py — listing persistence and disappearance tracking."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.schema import Platform


class TestObserveListing:
    def test_first_sighting_inserts_an_active_listing(self, store, make_listing):
        assert store.observe_listing(make_listing("a")) is True
        row = store._cur.execute(
            "SELECT * FROM raw_listings WHERE source_listing_id = 'a'"
        ).fetchone()
        assert row["status"] == "active"
        assert row["miss_count"] == 0
        assert row["first_seen_at"] == row["last_seen_at"]

    def test_repeat_sighting_refreshes_rather_than_duplicates(self, store, make_listing):
        store.observe_listing(make_listing("a"))
        assert store.observe_listing(make_listing("a")) is False
        assert store.counts()["total"] == 1

    def test_product_key_is_stored(self, store, make_listing):
        store.observe_listing(make_listing("a"), product_key="RTX 3080")
        row = store._cur.execute(
            "SELECT product_key FROM raw_listings").fetchone()
        assert row["product_key"] == "RTX 3080"

    def test_flag_reason_round_trips(self, store, make_listing):
        store.observe_listing(make_listing("a", title="RTX 4090 power cable"),
                              flag_reason="accessory_or_part")
        row = store._cur.execute(
            "SELECT flag_reason FROM raw_listings").fetchone()
        assert row["flag_reason"] == "accessory_or_part"

    def test_listing_without_a_source_id_is_refused(self, store, make_listing):
        """A blank id cannot be identified, so it must not be written.

        Regression: ``UNIQUE(source, source_listing_id)`` plus a blank id meant
        every id-less listing collided on the key ``(source, '')``, so each one
        silently overwrote the last — hundreds of listings collapsing into one
        row with no error. Craigslist produced exactly this when its markup
        changed and ids parsed as empty.
        """
        assert store.observe_listing(make_listing("", title="mystery item")) is False
        assert store.counts()["total"] == 0

    def test_whitespace_only_id_is_also_refused(self, store, make_listing):
        assert store.observe_listing(make_listing("   ", title="blankish")) is False
        assert store.counts()["total"] == 0

    def test_many_idless_listings_cannot_collapse_into_one_row(self, store,
                                                               make_listing):
        for i in range(5):
            store.observe_listing(make_listing("", title=f"no id {i}"))
        assert store.counts()["total"] == 0


class TestObservationCycle:
    def test_retires_only_after_threshold_misses(self, store, make_listing):
        store.observe_listing(make_listing("a"))
        for _ in range(2):
            res = store.close_observation_cycle(
                seen_keys=set(), misses_before_gone=3, sources=[Platform.kijiji])
            assert res["gone"] == 0
        res = store.close_observation_cycle(
            seen_keys=set(), misses_before_gone=3, sources=[Platform.kijiji])
        assert res["gone"] == 1
        assert store._cur.execute(
            "SELECT status FROM raw_listings").fetchone()["status"] == "gone"

    def test_seen_listing_is_never_retired(self, store, make_listing):
        store.observe_listing(make_listing("a"))
        res = store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=1,
            sources=[Platform.kijiji])
        assert res["gone"] == 0

    def test_other_sources_are_left_untouched(self, store, make_listing):
        store.observe_listing(make_listing("k", source=Platform.kijiji))
        store.observe_listing(make_listing("c", source=Platform.craigslist))
        store.close_observation_cycle(seen_keys=set(), misses_before_gone=1,
                                      sources=[Platform.kijiji])
        statuses = {r["source"]: r["status"] for r in store._cur.execute(
            "SELECT source, status FROM raw_listings")}
        assert statuses["kijiji"] == "gone"
        assert statuses["craigslist"] == "active"


class TestReading:
    def test_all_listings_can_exclude_gone(self, store, make_listing):
        store.observe_listing(make_listing("a"))
        store.observe_listing(make_listing("b"))
        store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=1,
            sources=[Platform.kijiji])
        assert len(store.all_listings()) == 2
        assert len(store.all_listings(include_gone=False)) == 1

    def test_counts_summarises_the_store(self, store, make_listing):
        store.observe_listing(make_listing("a"))
        store.observe_listing(make_listing("b", source=Platform.craigslist))
        c = store.counts()
        assert c["total"] == 2 and c["active"] == 2 and c["gone"] == 0
        assert c["by_source"] == {"kijiji": 1, "craigslist": 1}


class TestMigration:
    def test_legacy_database_gains_the_tracking_columns(self, tmp_path):
        """A DB written before lifecycle tracking still opens and works."""
        import sqlite3

        from app.db.store import Store

        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE raw_listings (
                id TEXT PRIMARY KEY, source TEXT NOT NULL,
                source_listing_id TEXT NOT NULL, title TEXT NOT NULL,
                price_cad REAL NOT NULL, url TEXT NOT NULL,
                location TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                date_posted TEXT, image_urls TEXT DEFAULT '[]',
                scraped_at TEXT NOT NULL,
                UNIQUE(source, source_listing_id));
        """)
        conn.execute(
            "INSERT INTO raw_listings (id, source, source_listing_id, title, "
            "price_cad, url, scraped_at) VALUES ('l1','kijiji','x1',"
            "'RTX 3080',500,'u',?)",
            (datetime.now(timezone.utc).isoformat(),))
        conn.commit()
        conn.close()

        s = Store(db=path)
        s.connect()
        try:
            row = s._cur.execute(
                "SELECT status, first_seen_at, product_key FROM raw_listings "
                "WHERE id = 'l1'").fetchone()
            assert row["status"] == "active"
            assert row["first_seen_at"] is not None
            assert row["product_key"] is None
        finally:
            s.close()
