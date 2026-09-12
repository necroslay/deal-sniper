"""Smoke test for the Streamlit dashboard.

Runs the real dashboard script through Streamlit's ``AppTest`` against a seeded
database. A rendering error in a Streamlit app is a *silent* failure in normal
use (you just get a red box in the browser), so it's worth asserting.
"""

from __future__ import annotations

import pathlib
from datetime import datetime, timezone

import pytest

from app.db.store import Store
from app.models.schema import Platform, RawListing

pytest.importorskip("streamlit")

DASHBOARD = str(pathlib.Path(__file__).resolve().parents[1] / "dashboard.py")


def _seed(path: str) -> None:
    """Two listings: one real GPU, one accessory the guard should flag."""
    store = Store(db=path).connect()
    store.observe_listing(
        RawListing(source=Platform.kijiji, listing_id="a",
                   title="EVGA GeForce RTX 3080 FTW3", price_cad=600.0,
                   url="https://kijiji.ca/a", location="Toronto",
                   date_posted=datetime.now(timezone.utc).isoformat()),
        product_key="RTX 3080")
    store.observe_listing(
        RawListing(source=Platform.craigslist, listing_id="b",
                   title="RTX 4090 power cable", price_cad=30.0,
                   url="https://craigslist.org/b", location="Toronto"),
        product_key="RTX 4090", flag_reason="accessory_or_part")
    store.close()


class TestDashboardRenders:
    def test_renders_with_data(self, tmp_path, monkeypatch):
        from streamlit.testing.v1 import AppTest

        db = str(tmp_path / "dash.db")
        _seed(db)
        monkeypatch.setenv("DEAL_SNIPER_DB", db)

        at = AppTest.from_file(DASHBOARD, default_timeout=30)
        at.run()

        assert not at.exception, [str(e) for e in at.exception]
        labels = {m.label for m in at.metric}
        assert "Listings" in labels
        assert "Active" in labels

    def test_renders_with_an_empty_database(self, tmp_path, monkeypatch):
        from streamlit.testing.v1 import AppTest

        db = str(tmp_path / "empty.db")
        Store(db=db).connect().close()  # create the schema, insert nothing
        monkeypatch.setenv("DEAL_SNIPER_DB", db)

        at = AppTest.from_file(DASHBOARD, default_timeout=30)
        at.run()

        assert not at.exception, [str(e) for e in at.exception]
