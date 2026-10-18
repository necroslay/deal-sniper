"""Reddit and eBay adapters, parsed from fixtures (no network)."""

from __future__ import annotations

import html

import pytest

from app.adapters import ebay, reddit_chs
from app.engines import logistics, risk
from app.models.schema import Platform


def _entry(post_id, title, body, author="/u/seller", published="2026-10-17T12:00:00+00:00"):
    return f"""<entry><author><name>{author}</name></author>
    <id>t3_{post_id}</id><title>{html.escape(title)}</title>
    <link href="https://www.reddit.com/r/CanadianHardwareSwap/comments/{post_id}/x/"/>
    <published>{published}</published><updated>{published}</updated>
    <content type="html">{html.escape("<div><p>" + body + "</p></div>")}</content>
    </entry>"""


def _feed(*entries):
    return ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:media="http://search.yahoo.com/mrss/">' + "".join(entries) + "</feed>")


# ── reddit ──────────────────────────────────────────────────────────────────

def test_sale_post_becomes_a_listing():
    feed = _feed(_entry(
        "abc1", "[CA-ON] [H] Gigabyte RTX 3080 10GB [W] PayPal G&S, E-transfer",
        "Price: $650 shipped. PayPal G&amp;S only. Ships Canada Post insured."))
    (listing,) = reddit_chs.parse_feed(feed)
    assert listing.source == Platform.reddit
    assert listing.source_listing_id == "t3_abc1"
    assert listing.price_cad == 650 and listing.location == "CA-ON"
    assert listing.title == "Gigabyte RTX 3080 10GB"
    assert listing.fulfillment == "shipping"
    assert "paypal_gs" in listing.payment and listing.seller_id == "seller"
    assert listing.shipping_cad == 0.0                    # "$650 shipped"

    plain = reddit_chs.parse_feed(_feed(_entry(
        "abc2", "[CA-ON] [H] RTX 3070 [W] PayPal", "Asking $400. Can ship at buyer's cost.")))
    assert plain[0].shipping_cad is None                  # estimate applies


@pytest.mark.parametrize("title,body", [
    ("[CA-ON] [H] PayPal [W] RTX 3080", "looking to buy, $600"),            # wanted ad
    ("[CA-BC] [H] RTX 3070 [W] RTX 3080", "trade up, $100 cash"),            # trade
    ("[CA-ON] [H] RTX 3080 [W] PayPal [SOLD]", "price $600"),                # sold
    ("[CA-ON] [H] DDR5 32GB kit [W] PayPal", "price $120"),                  # not GPU/laptop
    ("[CA-ON] [H] RTX 3080 [W] PayPal", "message me for the price"),         # no price
])
def test_non_sales_are_skipped(title, body):
    assert reddit_chs.parse_feed(_feed(_entry("x1", title, body))) == []


def test_price_ignores_shipping_charges():
    feed = _feed(_entry("p1", "[CA-AB] [H] RTX 4070 [W] PayPal",
                        "Shipping $40 extra. Asking $520 OBO."))
    assert reddit_chs.parse_feed(feed)[0].price_cad == 520


def test_laptop_sales_are_kept():
    feed = _feed(_entry("l1", "[CA-ON] [H] ThinkPad T14 i7 16GB laptop [W] E-transfer",
                        "$700 local or shipped"))
    assert reddit_chs.parse_feed(feed)[0].price_cad == 700


def test_garbage_feed_returns_nothing():
    assert reddit_chs.parse_feed("<html>nope") == []


def test_rate_limit_waits_out_the_reported_window():
    class Resp:
        def __init__(self, code, headers=None, text=""):
            self.status_code, self.headers, self.text = code, headers or {}, text

    answers = iter([Resp(429, {"x-ratelimit-reset": "7"}), Resp(200, text="<feed/>")])
    waits = []
    out = reddit_chs.fetch_feed("https://x", sleep=waits.append,
                                get=lambda *a, **k: next(answers))
    assert out == "<feed/>" and waits == [9.0]


def test_persistent_rate_limit_gives_up_quietly():
    class Resp:
        status_code, headers, text = 429, {"x-ratelimit-reset": "1"}, ""

    waits = []
    assert reddit_chs.fetch_feed("https://x", max_attempts=2, sleep=waits.append,
                                 get=lambda *a, **k: Resp()) is None
    assert len(waits) == 1


# ── ebay ────────────────────────────────────────────────────────────────────

def _item(**over):
    base = {
        "itemId": "v1|1234|0", "title": "MSI RTX 3070 Ventus 8GB", "condition": "Used",
        "price": {"value": "480.00", "currency": "CAD"},
        "buyingOptions": ["FIXED_PRICE"],
        "itemWebUrl": "https://www.ebay.ca/itm/1234",
        "shippingOptions": [{"shippingCost": {"value": "19.99", "currency": "CAD"}},
                            {"shippingCost": {"value": "35.00", "currency": "CAD"}}],
        "seller": {"username": "gpu_guy", "feedbackPercentage": "99.4",
                   "feedbackScore": "812"},
        "itemLocation": {"city": "Calgary", "stateOrProvince": "AB", "country": "CA"},
        "image": {"imageUrl": "https://i.ebayimg.com/a.jpg"},
        "itemCreationDate": "2026-10-16T10:00:00.000Z",
    }
    base.update(over)
    return base


def test_ebay_item_maps_shipping_seller_and_protection():
    listing = ebay.parse_item(_item())
    assert listing.source == Platform.ebay and listing.price_cad == 480
    assert listing.shipping_cad == 19.99                 # cheapest option
    assert (listing.seller_rating, listing.seller_reviews) == (99.4, 812)
    assert listing.fulfillment == "shipping" and listing.payment == "ebay_protection"
    assert listing.location == "Calgary, AB, CA"


@pytest.mark.parametrize("over", [
    {"price": {"value": "480", "currency": "USD"}},          # wrong currency
    {"buyingOptions": ["AUCTION"]},                          # no firm price
    {"condition": "For parts or not working"},
    {"title": ""},
])
def test_ebay_items_that_cant_be_bought_are_skipped(over):
    assert ebay.parse_item(_item(**over)) is None


def test_ebay_free_shipping_is_a_real_zero():
    listing = ebay.parse_item(_item(shippingOptions=[
        {"shippingCost": {"value": "0.00", "currency": "CAD"}}]))
    assert listing.shipping_cad == 0.0


def test_ebay_is_off_without_keys(monkeypatch):
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    monkeypatch.setattr("app.alerts.load_env_file", lambda *a, **k: None)
    assert ebay.credentials() is None
    assert ebay.EbayAdapter().search() == []


def test_ebay_client_requests_cad_fixed_price_with_shipping_context():
    calls = {}

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    class Session:
        def post(self, url, **kw):
            calls["token"] = kw
            return Resp(200, {"access_token": "tok", "expires_in": 7200})

        def get(self, url, **kw):
            calls["search"] = kw
            return Resp(200, {"itemSummaries": [_item()]})

    client = ebay.EbayClient("id", "secret", session=Session(), postal_code="M5V2T6")
    (listing,) = client.search("rtx 3070", ebay.GPU_CATEGORY)
    assert listing.price_cad == 480
    assert calls["token"]["data"]["grant_type"] == "client_credentials"
    assert calls["search"]["headers"]["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_CA"
    assert "zip=M5V2T6" in calls["search"]["headers"]["X-EBAY-C-ENDUSERCTX"]
    assert "FIXED_PRICE" in calls["search"]["params"]["filter"]


def test_ebay_failure_returns_nothing():
    class Resp:
        status_code = 500

    class Session:
        def post(self, *a, **k):
            return Resp()

    assert ebay.EbayClient("i", "s", session=Session()).search("x", "1") == []


# ── how the new fields feed shipping + risk ─────────────────────────────────

def test_marketplace_shipping_charge_beats_the_estimate():
    info = logistics.assess({"price_cad": 480, "title": "RTX 3070", "description": "",
                             "fulfillment": "shipping", "shipping_cad": 19.99}, "gpu")
    assert (info.shipping_cad, info.shipping_stated) == (19.99, True)
    free = logistics.assess({"price_cad": 480, "title": "x", "description": "",
                             "fulfillment": "shipping", "shipping_cad": 0.0}, "gpu")
    assert free.shipping_cad == 0.0


def _row(**kw):
    base = {"title": "RTX 3070", "description": "x" * 80,
            "image_urls": '["a","b","c"]', "seller_type": ""}
    base.update(kw)
    return base


def test_buyer_protection_lowers_risk():
    plain = risk.assess(_row(), 0.8, True)
    protected = risk.assess(_row(payment="paypal_gs"), 0.8, True)
    ebay_row = risk.assess(_row(payment="ebay_protection", seller_rating=99.5,
                                seller_reviews=900), 0.8, True)
    assert protected.score < plain.score and ebay_row.score < protected.score
    assert any("protection" in r for r in ebay_row.reasons)


def test_poor_seller_feedback_raises_risk():
    good = risk.assess(_row(seller_rating=99.0, seller_reviews=300), 0.8, True)
    bad = risk.assess(_row(seller_rating=88.0, seller_reviews=4), 0.8, True)
    assert bad.score >= good.score + 30


def test_new_fields_are_stored(store, make_listing):
    listing = make_listing("e1")
    listing.shipping_cad, listing.seller_rating, listing.seller_reviews = 19.99, 99.4, 812
    store.observe_listing(listing)
    row = store.all_listings()[0]
    assert (row["shipping_cad"], row["seller_rating"], row["seller_reviews"]) == (19.99, 99.4, 812)


def test_all_sources_are_registered():
    from app.adapters.sources import get_adapter
    for platform in (Platform.reddit, Platform.ebay):
        assert get_adapter(platform).platform == platform
