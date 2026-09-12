"""Tests for db/store.py — comp lookup correctness.

Regression cover for the comp-contamination bug: ``get_sold_comps`` used to match
on ``title LIKE '%name%'``, so a lookup for ``RTX 3060`` also returned
``RTX 3060 Ti`` comps and inflated the median.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.store import Store, derive_product_key
from app.models.schema import Platform, SoldComp


def _comp(title: str, price: float, days_ago: int = 10, uid: str = None) -> SoldComp:
    return SoldComp(
        source=Platform.ebay,
        source_listing_id=uid or f"{title}-{price}",
        title=title,
        sold_price_cad=price,
        sold_date=(datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        condition="used_good",
    )


@pytest.fixture
def store(tmp_path):
    s = Store(db=str(tmp_path / "test.db"))
    s.connect()
    yield s
    s.close()


class TestCompLookupIsExact:
    """A base model must not absorb its own variants' comps."""

    def test_base_model_excludes_variant_comps(self, store):
        # 10 comps for the base model, priced low...
        for i, p in enumerate([200, 220, 240, 260, 280, 300, 320, 340, 360, 380]):
            store.insert_sold_comp(_comp(f"RTX 3060 {i}", float(p), uid=f"base{i}"))
        # ...and 10 for the Ti variant, priced higher.
        for i, p in enumerate([300, 320, 340, 360, 380, 400, 420, 440, 460, 480]):
            store.insert_sold_comp(_comp(f"RTX 3060 Ti {i}", float(p), uid=f"ti{i}"))

        base = store.get_sold_comps(standard_name="RTX 3060")
        assert len(base) == 10, "RTX 3060 lookup must not include RTX 3060 Ti comps"
        assert max(c.sold_price_cad for c in base) == 380.0

    def test_variant_lookup_is_unaffected(self, store):
        for i, p in enumerate([200, 220, 240]):
            store.insert_sold_comp(_comp(f"RTX 3060 {i}", float(p), uid=f"b{i}"))
        for i, p in enumerate([400, 420, 440]):
            store.insert_sold_comp(_comp(f"RTX 3060 Ti {i}", float(p), uid=f"t{i}"))

        ti = store.get_sold_comps(standard_name="RTX 3060 Ti")
        assert len(ti) == 3
        assert min(c.sold_price_cad for c in ti) == 400.0

    def test_super_variant_not_absorbed(self, store):
        for i, p in enumerate([1400, 1450]):
            store.insert_sold_comp(_comp(f"RTX 4080 {i}", float(p), uid=f"b{i}"))
        for i, p in enumerate([1700, 1750, 1800]):
            store.insert_sold_comp(_comp(f"RTX 4080 Super {i}", float(p), uid=f"s{i}"))

        assert len(store.get_sold_comps(standard_name="RTX 4080")) == 2
        assert len(store.get_sold_comps(standard_name="RTX 4080 Super")) == 3

    def test_switch_oled_not_confused_with_switch(self, store):
        store.insert_sold_comp(_comp("Nintendo Switch 0", 250.0, uid="sw0"))
        store.insert_sold_comp(_comp("Nintendo Switch OLED 0", 300.0, uid="oled0"))

        assert len(store.get_sold_comps(standard_name="Nintendo Switch OLED")) == 1
        assert len(store.get_sold_comps(standard_name="Nintendo Switch")) == 1


class TestProductKeyPersistence:
    def test_product_key_is_populated_on_insert(self, store):
        store.insert_sold_comp(_comp("EVGA GeForce RTX 3080 FTW3 5", 550.0, uid="x1"))
        row = store._cur.execute("SELECT product_key FROM sold_comps").fetchone()
        assert row["product_key"] == "RTX 3080"

    def test_unknown_product_key_falls_back_to_title(self, store):
        store.insert_sold_comp(_comp("Totally Unknown Widget", 42.0, uid="u1"))
        row = store._cur.execute("SELECT product_key FROM sold_comps").fetchone()
        assert row["product_key"] == "Totally Unknown Widget"

    def test_unmatched_lookup_returns_empty(self, store):
        store.insert_sold_comp(_comp("RTX 4090 0", 2500.0, uid="g1"))
        assert store.get_sold_comps(standard_name="RX 580") == []


class TestMaxDaysOld:
    """``max_days_old`` used to be accepted and silently ignored."""

    def test_old_comps_are_excluded(self, store):
        store.insert_sold_comp(_comp("RTX 3080 recent", 600.0, days_ago=5, uid="r1"))
        store.insert_sold_comp(_comp("RTX 3080 old", 600.0, days_ago=200, uid="o1"))

        recent_only = store.get_sold_comps(standard_name="RTX 3080", max_days_old=90)
        assert len(recent_only) == 1
        assert recent_only[0].source_listing_id == "r1"

    def test_zero_disables_the_window(self, store):
        store.insert_sold_comp(_comp("RTX 3080 recent", 600.0, days_ago=5, uid="r1"))
        store.insert_sold_comp(_comp("RTX 3080 old", 600.0, days_ago=200, uid="o1"))
        assert len(store.get_sold_comps(standard_name="RTX 3080", max_days_old=0)) == 2


class TestMigration:
    def test_legacy_rows_are_backfilled(self, tmp_path):
        """A DB written before product_key existed still resolves."""
        import sqlite3

        path = str(tmp_path / "legacy.db")
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE sold_comps (
                id TEXT PRIMARY KEY, source TEXT NOT NULL,
                source_listing_id TEXT NOT NULL, title TEXT NOT NULL,
                sold_price_cad REAL NOT NULL, sold_date TEXT NOT NULL,
                condition TEXT NOT NULL DEFAULT '', url TEXT, location TEXT,
                scraped_at TEXT, UNIQUE(source, source_listing_id));
        """)
        conn.execute(
            "INSERT INTO sold_comps (id, source, source_listing_id, title, "
            "sold_price_cad, sold_date, condition) VALUES (?,?,?,?,?,?,?)",
            ("legacy1", "ebay", "l1", "RTX 3080 0", 500.0,
             datetime.now(timezone.utc).isoformat(), "used_good"),
        )
        conn.commit()
        conn.close()

        s = Store(db=path)
        s.connect()
        try:
            row = s._cur.execute(
                "SELECT product_key FROM sold_comps WHERE id = 'legacy1'"
            ).fetchone()
            assert row["product_key"] == "RTX 3080"
            assert len(s.get_sold_comps(standard_name="RTX 3080")) == 1
        finally:
            s.close()


class TestDedup:
    def test_duplicate_comp_is_ignored(self, store):
        c = _comp("RTX 3080 0", 500.0, uid="dup1")
        assert store.insert_sold_comp(c) is True
        assert store.insert_sold_comp(c) is False
        assert len(store.get_sold_comps(standard_name="RTX 3080")) == 1


class TestDeriveProductKey:
    """Comps must group by product even when the registry doesn't know it."""

    def test_registry_product_is_canonical(self):
        assert derive_product_key("RTX 3080 0") == "RTX 3080"

    def test_variant_is_not_collapsed_into_base(self):
        assert derive_product_key("RTX 3060 Ti 3") == "RTX 3060 Ti"

    def test_unregistered_product_strips_trailing_index(self):
        assert derive_product_key("Some Unlisted GPU 7") == "Some Unlisted GPU"

    def test_unregistered_product_without_index_is_unchanged(self):
        assert derive_product_key("Totally Unknown Widget") == "Totally Unknown Widget"

    def test_explicit_key_wins(self, store):
        c = _comp("weird title 9", 100.0, uid="explicit1")
        c.product_key = "RTX 4090"
        store.insert_sold_comp(c)
        assert len(store.get_sold_comps(standard_name="RTX 4090")) == 1

    def test_unresolved_comps_are_retrievable_together(self, store):
        """Regression: unresolved comps each got a unique key and were unreachable."""
        for i, p in enumerate([100.0, 200.0, 300.0]):
            store.insert_sold_comp(_comp(f"Some Unlisted GPU {i}", p, uid=f"u{i}"))
        got = store.get_sold_comps(standard_name="Some Unlisted GPU")
        assert len(got) == 3