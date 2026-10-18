"""The local web UI's server: state payload, triage marks, static serving."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

import pytest

from app.web import server
from app.web.server import State, family, make_handler


@pytest.fixture
def running(tmp_path, make_listing):
    """A live server over a database with a cheap listing among ordinary comps."""
    from app.db.store import Store

    db = str(tmp_path / "ui.db")
    store = Store(db=db).connect()
    for i in range(10):
        listing = make_listing(f"c{i}", price=700 + i * 10)
        listing.fulfillment = "shipping"
        listing.image_urls = ["a.jpg", "b.jpg", "c.jpg"]
        listing.description = "Used graphics card, works perfectly, original box."
        store.observe_listing(listing, product_key="RTX 3080")
    cheap = make_listing("cheap", price=520)
    cheap.fulfillment, cheap.image_urls = "shipping", ["a.jpg", "b.jpg", "c.jpg"]
    cheap.description = "Used graphics card, works perfectly, original box."
    store.observe_listing(cheap, product_key="RTX 3080")
    store.close()

    state = State(db)
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state, db))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield base, db
    httpd.shutdown()
    httpd.server_close()


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.status, resp.read(), resp.headers


def _post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def test_state_lists_scored_listings_with_bands(running):
    base, _ = running
    status, body, headers = _get(base + "/api/state")
    data = json.loads(body)
    assert status == 200 and "application/json" in headers["Content-Type"]
    assert data["counts"]["active"] == 11
    assert data["sectors"] and "RTX 3080" in data["bands"]
    cheap = next(d for d in data["deals"] if d["id"] and d["price"] == 520)
    assert cheap["product"] == "RTX 3080" and cheap["family"] == "RTX 30"
    assert cheap["landed"] > cheap["price"] and 0 <= cheap["score"] <= 100
    assert cheap["discount"] > 0.2 and cheap["fulfillment"] == "ships"
    assert data["deals"] == sorted(data["deals"], key=lambda d: -d["score"])


def test_marks_roundtrip_and_survive_a_rescore(running):
    base, db = running
    deal = next(d for d in json.loads(_get(base + "/api/state")[1])["deals"]
                if d["price"] == 520)
    assert _post(base + "/api/mark", {"id": deal["id"], "mark": "saved"}) == {"ok": True}
    fresh = json.loads(_get(base + "/api/state?refresh=1")[1])
    assert next(d for d in fresh["deals"] if d["id"] == deal["id"])["mark"] == "saved"
    _post(base + "/api/mark", {"id": deal["id"], "mark": None})
    fresh = json.loads(_get(base + "/api/state?refresh=1")[1])
    assert next(d for d in fresh["deals"] if d["id"] == deal["id"])["mark"] is None


@pytest.mark.parametrize("body", [{"id": "x", "mark": "bogus"}, {"mark": "saved"}, {}])
def test_bad_marks_are_rejected(running, body):
    base, _ = running
    with pytest.raises(urllib.error.HTTPError) as err:
        _post(base + "/api/mark", body)
    assert err.value.code == 400


def test_history_endpoint(running):
    base, _ = running
    deal = json.loads(_get(base + "/api/state")[1])["deals"][0]
    history = json.loads(_get(base + "/api/history/" + deal["id"])[1])["history"]
    assert history and history[0]["price"] == deal["price"]


def test_static_files_are_served_and_cannot_escape(running):
    base, _ = running
    status, body, headers = _get(base + "/")
    assert status == 200 and b"Deal" in body and "text/html" in headers["Content-Type"]
    assert _get(base + "/app.js")[0] == 200
    for evil in ("/../config.py", "/..%2f..%2fconfig.py", "/nope.txt"):
        with pytest.raises(urllib.error.HTTPError) as err:
            _get(base + evil)
        assert err.value.code == 404


def test_foreign_host_headers_are_refused(running):
    """DNS-rebinding guard: a request addressed to some other hostname gets 403."""
    base, _ = running
    req = urllib.request.Request(base + "/api/state", headers={"Host": "evil.example"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 403


def test_writes_require_json(running):
    base, _ = running
    req = urllib.request.Request(base + "/api/mark", b'{"id": "x", "mark": "saved"}',
                                 {"Content-Type": "text/plain"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(req, timeout=10)
    assert err.value.code == 415


@pytest.mark.parametrize("product,category,expected", [
    ("RTX 5090", "gpu", "RTX 50"), ("RTX 4070 Ti", "gpu", "RTX 40"),
    ("RTX 3060", "gpu", "RTX 30"), ("RTX 2070", "gpu", "Older GPU"),
    ("GTX 1080", "gpu", "Older GPU"), ("RX 7900 XTX", "gpu", "Radeon"),
    ("Laptop RTX 4060", "laptop", "Gaming laptop"),
    ("MacBook Pro M2", "laptop", "MacBook"),
    ("Lenovo ThinkPad i7 16GB", "laptop", "Laptop"),
])
def test_family_sectors(product, category, expected):
    assert family(product, category) == expected
    assert expected in server.SECTORS


def test_marks_table_rejects_unknown_values(store):
    with pytest.raises(ValueError):
        store.set_mark("x", "starred")
    store.set_mark("x", "saved")
    assert store.marks() == {"x": "saved"}
    store.set_mark("x", None)
    assert store.marks() == {}
