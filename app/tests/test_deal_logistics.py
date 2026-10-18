"""Shipping, laptop keys, risk scoring, resale maths and price history."""

from __future__ import annotations

import pytest

from app import alerts
from app.config import CFG
from app.engines import logistics, risk
from app.engines.laptops import extract_ram_gb, extract_storage_gb, parse_laptop


# ── logistics ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fulfillment,text,expected", [
    ("shipping,delivery", "", "ships"),
    ("", "Will ship anywhere in Canada", "ships"),
    ("shipping", "pickup only, no shipping", "pickup_only"),   # text refusal wins
    ("pickup", "", "pickup_only"),
    ("", "great card, works well", "unknown"),
    ("", "Shipping available via Canada Post", "ships"),
])
def test_fulfillment_status(fulfillment, text, expected):
    assert logistics.fulfillment_status(fulfillment, "", text) == expected


def test_stated_shipping_cost_beats_the_table():
    cost, stated = logistics.shipping_estimate("gpu", 500, "Shipping $22 tracked")
    assert (cost, stated) == (22.0, True)


def test_table_estimate_scales_with_value():
    cheap, _ = logistics.shipping_estimate("gpu", 100)
    dear, _ = logistics.shipping_estimate("gpu", 1000)
    assert dear > cheap > CFG.logistics.shipping_cad["gpu"]


def test_travel_preference_controls_what_is_shippable():
    assert logistics.shippable_for_alert("ships")
    assert logistics.shippable_for_alert("unknown")          # ask the seller
    assert not logistics.shippable_for_alert("pickup_only")  # never
    CFG.alerts.unknown_counts_as_shippable = False
    assert not logistics.shippable_for_alert("unknown")
    CFG.alerts.require_shippable = False
    assert logistics.shippable_for_alert("pickup_only")


# ── laptops ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,key", [
    ("ASUS ROG Strix G15 RTX 3060 16GB 512GB SSD gaming laptop", "Laptop RTX 3060"),
    ("Macbook Pro 14-inch 2023 (M2 Pro 10-core CPU - 16GB - 512GB)", "MacBook Pro M2 Pro"),
    ("Lenovo ThinkPad T14 i7 16GB RAM 256GB SSD", "Lenovo ThinkPad i7 16GB"),
    ("Dell XPS 13 i5 8GB laptop", "Dell XPS i5 8GB"),
])
def test_laptop_keys(title, key):
    assert parse_laptop(title).key == key


@pytest.mark.parametrize("title", [
    "Dell Inspiron tower desktop i7", "laptop bag 15.6", "HP laptop",
    "Laptop charger for ThinkPad", "Wooden desk",
    # GPU product lines that share a laptop brand name:
    "ASUS TUF Gaming GeForce RTX 3060 12GB graphics card",
    "ASUS ROG Strix RTX 3080 OC 10GB",
    "Sapphire Nitro+ RX 580 8GB",
])
def test_non_placeable_titles_are_not_laptops(title):
    assert parse_laptop(title) is None


def test_ram_and_storage_are_told_apart():
    title = "i7 16GB 512GB SSD laptop"
    assert extract_ram_gb(title) == 16
    assert extract_storage_gb(title) == 512
    assert extract_storage_gb("laptop 1TB NVMe") == 1000


# ── risk ────────────────────────────────────────────────────────────────────

def _row(**kw):
    base = {"title": "RTX 3080", "description": "x" * 80,
            "image_urls": '["a","b","c"]', "seller_type": "FSBO"}
    base.update(kw)
    return base


def test_ordinary_listing_is_low_risk():
    assert risk.assess(_row(), price_ratio=0.85, shipped=True).label == "low"


def test_scam_shaped_listing_is_high_risk():
    r = risk.assess(_row(image_urls="[]", description="e-transfer only",
                         title="RTX 3080 etransfer only"),
                    price_ratio=0.4, shipped=True)
    assert r.label == "high" and r.reasons


def test_business_seller_is_slightly_safer():
    a = risk.assess(_row(), 0.8, True)
    b = risk.assess(_row(seller_type="DEALER"), 0.8, True)
    assert b.score < a.score


# ── resale maths ────────────────────────────────────────────────────────────

def test_resale_profit_accounts_for_fees_and_shipping():
    profit, roi = alerts.resale_profit(median=1000, landed=600, price=570,
                                       category="gpu")
    # sale 920 - fees ~124.5 - outbound ~37 - landed 600 - reserve ~23
    assert 100 < profit < 150 and roi == pytest.approx(profit / 600)


def test_break_even_resale_is_not_a_profit():
    profit, _ = alerts.resale_profit(median=1000, landed=950, price=920, category="gpu")
    assert profit < 0


# ── alerts + logistics integration ──────────────────────────────────────────

@pytest.fixture
def market(make_stored_row):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)

    def row(lid, price, fulfillment="shipping", seen_hours_ago=1.0, **extra):
        r = make_stored_row(lid, "Nvidia RTX 3080", price)
        r.update(status="active", url=f"https://example.com/{lid}",
                 location="Toronto", fulfillment=fulfillment,
                 image_urls='["a","b","c"]',
                 first_seen_at=(now - timedelta(hours=seen_hours_ago)).isoformat())
        r.update(extra)
        return r

    comps = [row(f"c{i}", 700 + i * 10, seen_hours_ago=500) for i in range(10)]
    return comps, row


def test_pickup_only_listing_is_not_alerted(market):
    comps, row = market
    assert alerts.find_deals(comps + [row("p", 520, fulfillment="pickup")], set()) == []


def test_unknown_fulfillment_alerts_but_is_marked_ask_the_seller(market):
    comps, row = market
    (deal,) = alerts.find_deals(comps + [row("u", 520, fulfillment="")], set())
    assert deal.fulfillment == "unknown"
    assert "ask" in str(alerts.build_payloads([deal]))
    assert any("not stated" in r for r in deal.risk_reasons)
    CFG.alerts.unknown_counts_as_shippable = False
    assert alerts.find_deals(comps + [row("u", 520, fulfillment="")], set()) == []


def test_deal_carries_landed_cost_and_risk(market):
    comps, row = market
    (deal,) = alerts.find_deals(comps + [row("cheap", 520)], set())
    assert deal.landed_cad > deal.price_cad
    assert deal.risk_label in {"low", "medium"}
    assert deal.route in {"use", "flip", "both"}


def test_deal_that_only_beats_the_median_before_shipping_is_dropped(market):
    comps, row = market
    # 570 + ~34 shipping = ~604 landed: not 20% under the ~745 median.
    assert alerts.find_deals(comps + [row("meh", 570)], set()) == []


def test_high_risk_deal_is_dropped(market):
    comps, row = market
    scammy = row("scam", 400, image_urls="[]", description="e-transfer only")
    assert alerts.find_deals(comps + [scammy], set()) == []


def test_cross_posted_listing_alerts_once(market):
    comps, row = market
    a = row("a", 520, seller_id="s1")
    b = row("b", 520, seller_id="s1")                 # same item posted twice
    c = row("c", 520, seller_id="s2")                 # a different seller's own
    got = {d.listing_id for d in alerts.find_deals(comps + [a, b, c], set())}
    assert len(got & {"a", "b"}) == 1 and "c" in got


def test_laptop_titles_do_not_pollute_gpu_comps(market):
    comps, row = market
    lap = row("lap", 300)
    lap["title"] = "Gaming laptop RTX 3080 16GB"
    assert alerts.find_deals(comps + [lap], set()) == []


def test_payload_shows_landed_cost_and_risk(market):
    comps, row = market
    payload = alerts.build_payloads(alerts.find_deals(comps + [row("cheap", 520)], set()))[0]
    text = str(payload)
    assert "landed" in text and "Risk" in text


# ── price history ───────────────────────────────────────────────────────────

def test_price_changes_are_recorded(store, make_listing):
    store.observe_listing(make_listing("h1", price=500))
    store.observe_listing(make_listing("h1", price=500))       # unchanged
    store.observe_listing(make_listing("h1", price=450))       # dropped
    pk = store.all_listings()[0]["id"]
    assert [r["price_cad"] for r in store.price_history(pk)] == [500.0, 450.0]


def test_shipping_fields_are_persisted_and_refreshed(store, make_listing):
    listing = make_listing("f1")
    listing.fulfillment, listing.seller_id, listing.price_drop = "shipping", "s9", True
    store.observe_listing(listing)
    row = store.all_listings()[0]
    assert (row["fulfillment"], row["seller_id"], row["price_drop"]) == ("shipping", "s9", 1)
    listing.fulfillment = "pickup"
    store.observe_listing(listing)
    assert store.all_listings()[0]["fulfillment"] == "pickup"
