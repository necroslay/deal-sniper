"""Tests for engines/lifecycle.py and the Store listing-lifecycle methods.

Coverage here guards the *inferred* comp path: comps derived from a listing's
disappearance must stay clearly separate from observed sales, and a listing must
only be retired after several consecutive misses (and never because an unrelated
source returned nothing).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.store import Store
from app.engines.lifecycle import (
    build_inferred_comp,
    build_inferred_comps,
    inferred_sale_price,
)
from app.models.schema import Platform, RawListing, SoldComp

UTC = timezone.utc


def _row(**overrides) -> dict:
    """A disappeared-listing row as ``Store.get_gone_listings`` would return it."""
    base = dict(
        source="kijiji",
        source_listing_id="k1",
        title="RTX 3080 0",
        price_cad=800.0,
        url="https://kijiji.ca/1",
        location="Toronto",
        first_seen_at=(datetime.now(UTC) - timedelta(days=10)).isoformat(),
        last_seen_at=(datetime.now(UTC) - timedelta(days=2)).isoformat(),
    )
    base.update(overrides)
    return base


@pytest.fixture
def store(tmp_path):
    s = Store(db=str(tmp_path / "life.db"))
    s.connect()
    yield s
    s.close()


def _listing(lid, price=800.0, source=Platform.kijiji, title="RTX 3080 0"):
    return RawListing(source=source, listing_id=lid, title=title,
                      price_cad=price, url=f"https://kijiji.ca/{lid}")


# ── Engine ──────────────────────────────────────────────────────────────────

class TestInferredSalePrice:
    def test_applies_haircut(self):
        assert inferred_sale_price(800.0, 0.05) == 760.0

    def test_zero_price_stays_zero(self):
        assert inferred_sale_price(0.0, 0.05) == 0.0

    def test_never_goes_negative(self):
        assert inferred_sale_price(100.0, 1.5) == 0.0


class TestBuildInferredComp:
    def test_qualifying_row_becomes_an_inferred_comp(self):
        comp = build_inferred_comp(_row(), haircut_pct=0.10, min_days_listed=1)
        assert comp is not None
        assert comp.inferred is True
        assert comp.sold_price_cad == 720.0
        assert comp.source == Platform.kijiji
        # product_key is deliberately unset; the store derives it at insert time.
        assert comp.product_key is None

    def test_row_without_price_is_skipped(self):
        assert build_inferred_comp(_row(price_cad=0)) is None

    def test_listing_pulled_too_quickly_is_skipped(self):
        now = datetime.now(UTC)
        row = _row(first_seen_at=(now - timedelta(hours=1)).isoformat(),
                   last_seen_at=now.isoformat())
        assert build_inferred_comp(row, min_days_listed=1.0) is None

    def test_batch_skips_unqualified_rows(self):
        rows = [_row(source_listing_id="a"),
                _row(source_listing_id="b", price_cad=0)]
        comps = build_inferred_comps(rows, haircut_pct=0.0, min_days_listed=0)
        assert [c.source_listing_id for c in comps] == ["a"]

    def test_sold_date_uses_last_sighting(self):
        row = _row(first_seen_at="2026-01-01T00:00:00+00:00",
                   last_seen_at="2026-01-02T00:00:00+00:00")
        comp = build_inferred_comp(row, haircut_pct=0.0, min_days_listed=0)
        assert comp.sold_date.startswith("2026-01-02")

    def test_gone_at_extends_the_observed_lifespan(self):
        # Seen once, then only noticed missing a day later: lifespan is measured
        # to gone_at, not to the (identical) last sighting.
        now = datetime.now(UTC)
        row = _row(first_seen_at=(now - timedelta(days=5)).isoformat(),
                   last_seen_at=(now - timedelta(days=5)).isoformat(),
                   gone_at=(now - timedelta(days=1)).isoformat())
        assert build_inferred_comp(row, haircut_pct=0.0,
                                   min_days_listed=2.0) is not None

    def test_pulled_soon_after_first_sighting_is_skipped(self):
        now = datetime.now(UTC)
        row = _row(
            first_seen_at=(now - timedelta(days=5)).isoformat(),
            last_seen_at=(now - timedelta(days=5)).isoformat(),
            gone_at=(now - timedelta(days=5) + timedelta(hours=1)).isoformat())
        assert build_inferred_comp(row, haircut_pct=0.0,
                                   min_days_listed=2.0) is None


# ── Store: observation tracking ─────────────────────────────────────────────

class TestObservationTracking:
    def test_first_sighting_inserts_an_active_listing(self, store):
        assert store.observe_listing(_listing("a")) is True
        row = store._cur.execute(
            "SELECT * FROM raw_listings WHERE source_listing_id = 'a'"
        ).fetchone()
        assert row["status"] == "active"
        assert row["miss_count"] == 0
        assert row["first_seen_at"] == row["last_seen_at"]

    def test_repeat_sighting_refreshes_and_resets_misses(self, store):
        t1 = datetime(2026, 1, 1, tzinfo=UTC).isoformat()
        t2 = datetime(2026, 1, 2, tzinfo=UTC).isoformat()
        store.observe_listing(_listing("a"), observed_at=t1)
        store.close_observation_cycle(seen_keys=set(), misses_before_gone=5,
                                      sources=[Platform.kijiji])
        assert store._cur.execute(
            "SELECT miss_count FROM raw_listings").fetchone()[0] == 1

        assert store.observe_listing(_listing("a"), observed_at=t2) is False
        row = store._cur.execute(
            "SELECT first_seen_at, last_seen_at, miss_count FROM raw_listings"
        ).fetchone()
        assert row["miss_count"] == 0          # a sighting clears the misses
        assert row["first_seen_at"] == t1      # first sighting is preserved
        assert row["last_seen_at"] == t2       # latest sighting advances


class TestCloseObservationCycle:
    def test_retires_only_after_threshold_misses(self, store):
        store.observe_listing(_listing("a"))
        for _ in range(2):
            res = store.close_observation_cycle(
                seen_keys=set(), misses_before_gone=3, sources=[Platform.kijiji])
            assert res["gone"] == 0
        res = store.close_observation_cycle(
            seen_keys=set(), misses_before_gone=3, sources=[Platform.kijiji])
        assert res["gone"] == 1
        row = store._cur.execute(
            "SELECT status, gone_at FROM raw_listings").fetchone()
        assert row["status"] == "gone"
        assert row["gone_at"] is not None

    def test_seen_listings_are_never_retired(self, store):
        store.observe_listing(_listing("a"))
        res = store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=1,
            sources=[Platform.kijiji])
        assert res["gone"] == 0
        assert store._cur.execute(
            "SELECT status FROM raw_listings").fetchone()[0] == "active"

    def test_other_sources_are_left_untouched(self, store):
        store.observe_listing(_listing("k", source=Platform.kijiji))
        store.observe_listing(_listing("c", source=Platform.craigslist))
        store.close_observation_cycle(seen_keys=set(), misses_before_gone=1,
                                      sources=[Platform.kijiji])
        statuses = {r["source"]: r["status"] for r in store._cur.execute(
            "SELECT source, status FROM raw_listings")}
        assert statuses["kijiji"] == "gone"
        assert statuses["craigslist"] == "active"


class TestGetGoneListings:
    def test_returns_only_the_disappeared(self, store):
        store.observe_listing(_listing("a"))
        store.observe_listing(_listing("b"))
        store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=1,
            sources=[Platform.kijiji])
        assert [r["source_listing_id"]
                for r in store.get_gone_listings()] == ["b"]


# ── Store: inferred comps stay separate from real ones ──────────────────────

class TestInferredCompSeparation:
    def _real(self):
        return SoldComp(source=Platform.ebay, source_listing_id="real1",
                        title="RTX 3080", sold_price_cad=500.0,
                        sold_date=datetime.now(UTC).isoformat(),
                        product_key="RTX 3080")

    def _inferred(self):
        return SoldComp(source=Platform.kijiji, source_listing_id="inf1",
                        title="RTX 3080", sold_price_cad=760.0,
                        sold_date=datetime.now(UTC).isoformat(),
                        product_key="RTX 3080", inferred=True)

    def test_excluded_by_default(self, store):
        store.insert_sold_comp(self._real())
        store.insert_sold_comp(self._inferred())
        got = store.get_sold_comps(standard_name="RTX 3080")
        assert [c.source_listing_id for c in got] == ["real1"]

    def test_included_only_when_asked(self, store):
        store.insert_sold_comp(self._real())
        store.insert_sold_comp(self._inferred())
        got = store.get_sold_comps(standard_name="RTX 3080",
                                   include_inferred=True)
        assert {c.source_listing_id for c in got} == {"real1", "inf1"}

    def test_count_comps_by_origin(self, store):
        store.insert_sold_comp(self._real())
        store.insert_sold_comp(self._inferred())
        store.insert_sold_comp(SoldComp(
            source=Platform.ebay, source_listing_id="baseline_RTX_3080_0",
            title="RTX 3080 0", sold_price_cad=500.0,
            sold_date=datetime.now(UTC).isoformat(), product_key="RTX 3080"))
        assert store.count_comps_by_origin() == {
            "real": 1, "inferred": 1, "synthetic": 1}


# ── End-to-end across scrape cycles ─────────────────────────────────────────

class TestLifecycleEndToEnd:
    def test_a_listing_that_disappears_becomes_an_inferred_comp(self, store):
        """The whole point: track listings, notice a vanishing one, price it."""

        # Cycle 1 — two listings on offer, both seen.
        store.observe_listing(_listing("a", price=800.0))
        store.observe_listing(_listing("b", price=600.0))
        store.close_observation_cycle(
            seen_keys={("kijiji", "a"), ("kijiji", "b")},
            misses_before_gone=2, sources=[Platform.kijiji])
        assert store.get_gone_listings() == []

        # Cycle 2 — listing B has vanished: one miss, not yet retired.
        store.observe_listing(_listing("a", price=800.0))
        store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=2,
            sources=[Platform.kijiji])
        assert store.get_gone_listings() == []

        # Cycle 3 — B still absent: threshold reached, retired.
        store.observe_listing(_listing("a", price=800.0))
        store.close_observation_cycle(
            seen_keys={("kijiji", "a")}, misses_before_gone=2,
            sources=[Platform.kijiji])
        gone = store.get_gone_listings()
        assert [r["source_listing_id"] for r in gone] == ["b"]

        # The disappeared listing yields an inferred comp at a haircut...
        comps = build_inferred_comps(gone, haircut_pct=0.05, min_days_listed=0)
        assert len(comps) == 1
        assert comps[0].sold_price_cad == 570.0        # 600 less 5%
        assert comps[0].inferred is True

        # ...which persists but stays out of the default comp lookup.
        assert store.insert_sold_comp(comps[0]) is True
        assert store.count_comps_by_origin()["inferred"] == 1
        assert store.get_sold_comps(standard_name="RTX 3080") == []
        assert len(store.get_sold_comps(standard_name="RTX 3080",
                                        include_inferred=True)) == 1


# ── Rejected listings must never become comps ───────────────────────────────

class TestRejectedListingsNeverBecomeComps:
    """A $40 'GPU power cable' must not be priced as an RTX 4090 sale.

    Found by a live smoke test: the guard rejected the cable for valuation, but
    the lifecycle path still retired it and derived an inferred comp from its
    title, reintroducing the accessory-contamination bug through a new route.
    """

    def test_engine_skips_a_row_carrying_a_skip_reason(self):
        row = _row(skip_reason="accessory_or_part")
        assert build_inferred_comp(row, haircut_pct=0.0, min_days_listed=0) is None

    def test_store_excludes_rejected_listings_from_gone(self, store):
        store.observe_listing(_listing("good", price=800.0))
        store.observe_listing(
            _listing("cable", price=40.0, title="RTX 4090 power cable"),
            skip_reason="accessory_or_part")
        store.close_observation_cycle(
            seen_keys=set(), misses_before_gone=1, sources=[Platform.kijiji])

        assert [r["source_listing_id"]
                for r in store.get_gone_listings()] == ["good"]
        # ...but the rejected listing is still retained for audit.
        assert store._cur.execute(
            "SELECT COUNT(*) AS n FROM raw_listings").fetchone()["n"] == 2

    def test_pipeline_rejects_cable_but_keeps_good_listing(self, store):
        from app.pipeline.ingest import IngestionPipeline

        pipeline = IngestionPipeline(store=store)
        pipeline.process(raw_listing=_listing("good", price=800.0), persist=True)
        pipeline.process(
            raw_listing=_listing("cable", price=40.0,
                                 title="RTX 4090 power cable"),
            persist=True)
        store.close_observation_cycle(
            seen_keys=set(), misses_before_gone=1, sources=[Platform.kijiji])

        comps = build_inferred_comps(store.get_gone_listings(), min_days_listed=0)
        assert [c.source_listing_id for c in comps] == ["good"]

