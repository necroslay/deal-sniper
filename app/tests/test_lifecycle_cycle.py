"""Tests for pipeline/lifecycle_cycle.py — reusable cycle orchestration.

The important guarantee here is safety: a cycle with no observations must never
retire the inventory, and re-running the comp refresh must be idempotent.
"""

from __future__ import annotations

import pytest

from app.db.store import Store
from app.models.schema import Platform, RawListing
from app.pipeline.lifecycle_cycle import observe_cycle, refresh_inferred_comps


@pytest.fixture
def store(tmp_path):
    s = Store(db=str(tmp_path / "cycle.db"))
    s.connect()
    yield s
    s.close()


def _listing(lid, price=800.0, source=Platform.kijiji, title="RTX 3080 0"):
    return RawListing(source=source, listing_id=lid, title=title,
                      price_cad=price, url=f"https://kijiji.ca/{lid}")


class TestObserveCycle:
    def test_reports_missed_gone_and_inferred(self, store):
        # min_days_listed=0 lets a freshly-observed listing qualify at once; the
        # default 1-day guard is covered in test_lifecycle.py.
        store.observe_listing(_listing("a"))
        store.observe_listing(_listing("b"))

        # Cycle: only 'a' reappears, so 'b' retires at threshold 1.
        summary = observe_cycle(store, [_listing("a")], misses_before_gone=1,
                                min_days_listed=0)
        assert summary["missed"] == 1
        assert summary["gone"] == 1
        assert summary["inferred_built"] == 1
        assert summary["inferred_added"] == 1

    def test_seen_listings_survive(self, store):
        store.observe_listing(_listing("a"))
        summary = observe_cycle(store, [_listing("a")], misses_before_gone=1)
        assert summary["gone"] == 0
        assert store._cur.execute(
            "SELECT status FROM raw_listings").fetchone()[0] == "active"

    def test_empty_cycle_does_not_retire_anything(self, store):
        store.observe_listing(_listing("a"))
        store.observe_listing(_listing("b"))
        summary = observe_cycle(store, [], misses_before_gone=1)
        assert summary["missed"] == 0 and summary["gone"] == 0
        assert store._cur.execute(
            "SELECT COUNT(*) AS n FROM raw_listings WHERE status = 'gone'"
        ).fetchone()["n"] == 0


class TestRefreshInferredComps:
    def test_is_idempotent(self, store):
        store.observe_listing(_listing("a"))
        store.close_observation_cycle(seen_keys=set(), misses_before_gone=1,
                                      sources=[Platform.kijiji])

        first = refresh_inferred_comps(store, min_days_listed=0)
        assert first == {"inferred_built": 1, "inferred_added": 1}

        # Second run rebuilds the same comp but stores nothing new.
        second = refresh_inferred_comps(store, min_days_listed=0)
        assert second == {"inferred_built": 1, "inferred_added": 0}
        assert store.count_comps_by_origin()["inferred"] == 1
