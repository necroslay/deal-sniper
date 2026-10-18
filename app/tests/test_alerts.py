"""Deal detection, Discord payloads, and the run-history/dedup tables."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import alerts
from app.config import CFG


@pytest.fixture
def market(make_stored_row):
    """Ten ordinary RTX 3080 comps plus one cheap, fresh listing."""
    now = datetime.now(timezone.utc)

    def row(lid, price, seen_hours_ago=1.0, status="active"):
        r = make_stored_row(lid, "Nvidia RTX 3080", price)
        r.update(status=status, url=f"https://example.com/{lid}",
                 location="Toronto",
                 first_seen_at=(now - timedelta(hours=seen_hours_ago)).isoformat())
        return r

    comps = [row(f"c{i}", 700 + i * 10, seen_hours_ago=500) for i in range(10)]
    return comps, row


def test_cheap_fresh_listing_is_a_deal(market):
    comps, row = market
    deals = alerts.find_deals(comps + [row("cheap", 520)], set())
    assert [d.listing_id for d in deals] == ["cheap"]
    deal = deals[0]
    assert deal.product == "RTX 3080"
    assert deal.discount > 0.2 and deal.n_comps >= CFG.alerts.min_comps


def test_ordinary_price_is_not_a_deal(market):
    comps, row = market
    assert alerts.find_deals(comps + [row("fair", 705)], set()) == []


def test_scam_priced_listing_is_ignored(market):
    comps, row = market
    # $50 against a ~$745 median is a placeholder or scam, not a bargain.
    assert alerts.find_deals(comps + [row("scam", 50)], set()) == []


def test_already_alerted_listing_is_skipped(market):
    comps, row = market
    assert alerts.find_deals(comps + [row("cheap", 520)], {"cheap"}) == []


def test_old_or_gone_listings_are_skipped(market):
    comps, row = market
    old = row("old", 520, seen_hours_ago=CFG.alerts.max_listing_age_hours + 10)
    gone = row("gone", 520, status="gone")
    assert alerts.find_deals(comps + [old, gone], set()) == []


@pytest.mark.parametrize("title", [
    "Nvidia RTX 3080 for parts or repair",
    "Nvidia RTX 3080 not working",
    "500W PSU and Nvidia RTX 3080 combo",
])
def test_broken_or_bundled_titles_are_not_alerted(market, title):
    comps, row = market
    bad = row("bad", 520)
    bad["title"] = title
    assert alerts.find_deals(comps + [bad], set()) == []


def test_thin_product_has_no_trusted_band(market):
    comps, row = market
    assert alerts.find_deals(comps[:3] + [row("cheap", 520)], set()) == []


def test_max_per_run_caps_results(market):
    comps, row = market
    CFG.alerts.max_per_run = 2
    cheap = [row(f"x{i}", 500 + i) for i in range(5)]
    assert len(alerts.find_deals(comps + cheap, set())) == 2


def test_payloads_chunk_to_discord_embed_limit(market):
    comps, row = market
    CFG.alerts.max_per_run = 25
    # Enough ordinary comps that 12 cheap listings don't drag the median down.
    comps = comps + [row(f"m{i}", 700 + i * 3, seen_hours_ago=500) for i in range(60)]
    deals = alerts.find_deals(comps + [row(f"x{i}", 500 + i) for i in range(12)], set())
    payloads = alerts.build_payloads(deals)
    assert [len(p["embeds"]) for p in payloads] == [10, 2]
    assert payloads[0]["embeds"][0]["url"].startswith("https://")


def test_run_alerts_marks_only_successful_sends(store, make_listing, monkeypatch):
    for i in range(10):
        store.observe_listing(make_listing(f"c{i}", price=700 + i * 10),
                              product_key="RTX 3080")
    store.observe_listing(make_listing("cheap", price=520), product_key="RTX 3080")
    monkeypatch.setenv(CFG.alerts.webhook_env, "https://discord.test/hook")
    monkeypatch.setattr(alerts, "load_env_file", lambda *a, **k: None)

    monkeypatch.setattr(alerts, "post_payload", lambda *a, **k: False)
    outcome = alerts.run_alerts(store)
    assert outcome["found"] == 1 and outcome["sent"] == 0
    assert store.alerted_ids() == set()               # retried next run

    monkeypatch.setattr(alerts, "post_payload", lambda *a, **k: True)
    outcome = alerts.run_alerts(store)
    assert outcome["sent"] == 1
    assert len(store.alerted_ids()) == 1
    assert alerts.run_alerts(store)["found"] == 0     # never alerts twice


def test_run_alerts_without_webhook_sends_nothing(store, make_listing, monkeypatch):
    monkeypatch.delenv(CFG.alerts.webhook_env, raising=False)
    monkeypatch.setattr(alerts, "load_env_file", lambda *a, **k: None)
    assert alerts.run_alerts(store) == {"found": 0, "sent": 0, "configured": False}


def test_env_file_does_not_override_real_environment(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nA_TEST_KEY=\"from-file\"\nB_TEST_KEY=file\n")
    monkeypatch.setenv("A_TEST_KEY", "from-env")
    monkeypatch.delenv("B_TEST_KEY", raising=False)
    alerts.load_env_file(env)
    import os
    assert os.environ["A_TEST_KEY"] == "from-env"
    assert os.environ["B_TEST_KEY"] == "file"
    monkeypatch.delenv("B_TEST_KEY")


def test_run_history_roundtrip(store):
    run_id = store.start_run(["kijiji", "craigslist"])
    assert store.recent_runs()[0]["status"] == "running"
    store.finish_run(run_id, "partial", collected=5, unique_listings=4,
                     new_listings=2, errors=["craigslist returned 0 listings"])
    row = store.recent_runs(1)[0]
    assert row["status"] == "partial" and row["new_listings"] == 2
    assert "craigslist" in row["errors"]
