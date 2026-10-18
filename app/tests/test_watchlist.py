"""watchlist.yaml overrides, and the alert filters they drive."""

from __future__ import annotations

import pytest

from app import alerts
from app.config import CFG
from app.watchlist import apply_watchlist


def _write(tmp_path, text):
    path = tmp_path / "watchlist.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_missing_file_is_a_noop(tmp_path):
    assert apply_watchlist(tmp_path / "nope.yaml") == []


def test_values_override_defaults(tmp_path):
    problems = apply_watchlist(_write(tmp_path, """
alerts:
  max_risk: 40
  min_discount: 0.35
  include: [RTX 4090]
  require_shippable: false
profit:
  resale_fee_rate: 0.10
logistics:
  shipping_cad: {gpu: 19}
"""))
    assert problems == []
    assert CFG.alerts.max_risk == 40 and isinstance(CFG.alerts.max_risk, int)
    assert CFG.alerts.min_discount == 0.35
    assert CFG.alerts.include == ["RTX 4090"]
    assert CFG.alerts.require_shippable is False
    assert CFG.profit.resale_fee_rate == 0.10
    assert CFG.logistics.shipping_cad["gpu"] == 19
    assert CFG.logistics.shipping_cad["laptop"] == 40       # untouched keys survive


def test_typos_are_reported_not_ignored(tmp_path):
    problems = apply_watchlist(_write(tmp_path, """
alerts:
  max_rsik: 40
  max_risk: lots
bogus: {a: 1}
"""))
    assert len(problems) == 3
    assert CFG.alerts.max_risk == 55                         # unchanged


def test_garbage_file_does_not_raise(tmp_path):
    assert apply_watchlist(_write(tmp_path, "[1, 2")) != []
    assert apply_watchlist(_write(tmp_path, "- a\n- b\n")) != []


# ── filters driven by the config ────────────────────────────────────────────

@pytest.fixture
def market(make_stored_row):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def row(lid, price, title="Nvidia RTX 3080", seller=None, seen=1.0):
        r = make_stored_row(lid, title, price)
        r.update(status="active", url=f"https://example.com/{lid}",
                 location="Toronto", fulfillment="shipping",
                 image_urls='["a","b","c"]',
                 first_seen_at=(now - timedelta(hours=seen)).isoformat())
        if seller:
            r["seller_id"] = seller
        return r

    return [row(f"c{i}", 700 + i * 10, seen=500) for i in range(10)], row


def test_include_and_exclude_filter_products(market):
    comps, row = market
    cheap = row("cheap", 520)
    CFG.alerts.include = ["RTX 4090"]
    assert alerts.find_deals(comps + [cheap], set()) == []
    CFG.alerts.include = ["rtx 3080"]
    assert len(alerts.find_deals(comps + [cheap], set())) == 1
    CFG.alerts.exclude = ["3080"]
    assert alerts.find_deals(comps + [cheap], set()) == []


def test_budget_ceiling_applies_to_landed_cost(market):
    comps, row = market
    CFG.alerts.max_landed_cad = 500
    assert alerts.find_deals(comps + [row("cheap", 520)], set()) == []


def test_same_seller_same_item_same_price_alerts_once(market):
    comps, row = market
    a = row("a", 520, seller="s1")
    b = row("b", 520, seller="s1")
    b["title"] = "AUTUMN SALE Nvidia RTX 3080"           # relisted, new headline
    assert len(alerts.find_deals(comps + [a, b], set())) == 1


def test_laptops_need_a_bigger_margin_than_gpus(make_stored_row):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def lap(lid, price, seen=1.0):
        r = make_stored_row(lid, "ASUS gaming laptop RTX 3060 16GB", price)
        r.update(status="active", url=f"https://example.com/{lid}", location="x",
                 fulfillment="shipping", image_urls='["a","b","c"]',
                 first_seen_at=(now - timedelta(hours=seen)).isoformat())
        return r

    comps = [lap(f"c{i}", 900 + i * 10, seen=500) for i in range(10)]
    # ~$686 landed is ~27% under the ~$945 median: enough for a GPU, not a laptop.
    assert alerts.find_deals(comps + [lap("l", 640)], set()) == []
    # ~$550 landed is ~42% under: clears the laptop bar.
    assert len(alerts.find_deals(comps + [lap("l2", 505)], set())) == 1
