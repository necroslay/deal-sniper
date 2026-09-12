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


class TestRunScrapeControl:
    """The dashboard can trigger the scraper itself.

    ``subprocess.run`` is stubbed so the test never touches the network; what we
    are asserting is that the UI wires up the *same CLI* the terminal uses.
    """

    def test_button_invokes_the_scraper_cli(self, tmp_path, monkeypatch):
        import subprocess

        from streamlit.testing.v1 import AppTest

        db = str(tmp_path / "dash.db")
        _seed(db)
        monkeypatch.setenv("DEAL_SNIPER_DB", db)

        captured = {}

        class FakeCompleted:
            returncode = 0
            stdout = "Stored 3 listing(s)\n  new: 3   refreshed: 0"
            stderr = ""

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return FakeCompleted()

        monkeypatch.setattr(subprocess, "run", fake_run)

        at = AppTest.from_file(DASHBOARD, default_timeout=30)
        at.run()
        assert not at.exception, [str(e) for e in at.exception]

        button = next(b for b in at.sidebar.button if "Run scrape" in b.label)
        button.click().run()
        assert not at.exception, [str(e) for e in at.exception]

        cmd = captured.get("cmd")
        assert cmd, "the scraper was never invoked"
        assert "app.scrape" in cmd
        assert "--source" in cmd and "--db" in cmd
        assert db in cmd, "must write to the same database the dashboard reads"

    def test_scrape_control_is_available_before_any_data_exists(self, tmp_path,
                                                               monkeypatch):
        """An empty database must not be a dead end."""
        from streamlit.testing.v1 import AppTest

        db = str(tmp_path / "empty.db")
        Store(db=db).connect().close()
        monkeypatch.setenv("DEAL_SNIPER_DB", db)

        at = AppTest.from_file(DASHBOARD, default_timeout=30)
        at.run()

        assert not at.exception, [str(e) for e in at.exception]
        assert any("Run scrape" in b.label for b in at.sidebar.button)
