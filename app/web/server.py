"""Serve the UI and its JSON API on localhost.

Standard library only (no web framework to install), bound to 127.0.0.1 so the
database is never exposed to the network. Run it with::

    python -m app.web            # opens http://127.0.0.1:8765

Endpoints
    GET  /api/state              every scored listing + comparable-price bands
    GET  /api/history/<id>       one listing's recorded prices over time
    POST /api/mark               {"id": ..., "mark": saved|contacted|dismissed|null}
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import CFG
from app.db.store import DB, Store
from app.market import score_market
from app.watchlist import apply_watchlist

STATIC = Path(__file__).resolve().parent / "static"
CACHE_SECONDS = 120

# Radar sectors, in clockwise order from the top.
SECTORS = ["RTX 50", "RTX 40", "RTX 30", "Older GPU", "Radeon",
           "Gaming laptop", "MacBook", "Laptop"]


def family(product: str, category: str) -> str:
    """Which radar sector a product belongs to."""
    if category == "laptop":
        if product.startswith("MacBook"):
            return "MacBook"
        if product.startswith(("Laptop RTX", "Laptop GTX")):
            return "Gaming laptop"
        return "Laptop"
    m = re.match(r"RTX (\d)\d{2,3}", product)
    if m:
        return {"5": "RTX 50", "4": "RTX 40", "3": "RTX 30"}.get(m.group(1), "Older GPU")
    if product.startswith("GTX"):
        return "Older GPU"
    if product.startswith("RX"):
        return "Radeon"
    return "Older GPU"


def _number(value, digits=2):
    return None if value is None else round(float(value), digits)


def serialise(deal, mark):
    return {
        "id": deal.listing_id, "title": deal.title, "product": deal.product,
        "category": deal.category, "family": family(deal.product, deal.category),
        "source": deal.source, "location": deal.location, "url": deal.url,
        "price": round(deal.price_cad), "shipping": round(deal.shipping_cad),
        "shippingStated": deal.shipping_stated, "landed": round(deal.landed_cad),
        "median": round(deal.median_cad), "p25": round(deal.p25_cad),
        "comps": deal.n_comps, "discount": _number(deal.discount, 3),
        "profit": round(deal.net_profit_cad), "roi": _number(deal.roi, 3),
        "risk": deal.risk_score, "riskLabel": deal.risk_label,
        "riskReasons": deal.risk_reasons, "route": deal.route,
        "qualifies": deal.qualifies, "fulfillment": deal.fulfillment,
        "condition": deal.condition, "priceDrop": deal.price_drop,
        "ageHours": deal.age_hours, "image": deal.image,
        "description": deal.description, "seller": deal.seller_id,
        "rating": deal.seller_rating, "reviews": deal.seller_reviews,
        "payment": deal.payment, "score": deal.sniper_score, "mark": mark,
    }


class State:
    """Scores the market once and serves it from memory for a couple of minutes."""

    def __init__(self, db: str):
        self.db = db
        self._lock = threading.Lock()
        self._payload = None
        self._built = 0.0

    def get(self, refresh: bool = False) -> dict:
        with self._lock:
            if refresh or self._payload is None or time.time() - self._built > CACHE_SECONDS:
                self._payload = self._build()
                self._built = time.time()
            return self._payload

    def invalidate(self) -> None:
        with self._lock:
            self._built = 0.0

    def _build(self) -> dict:
        store = Store(db=self.db).connect()
        try:
            rows = store.all_listings(include_gone=False)
            market = score_market(rows, explore=True)
            marks = store.marks()
            counts = store.counts()
            runs = [dict(r) for r in store.recent_runs(5)]
        finally:
            store.close()
        deals = sorted(market.deals, key=lambda d: d.sniper_score, reverse=True)
        cfg = CFG.alerts
        return {
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "counts": counts, "sectors": SECTORS,
            "runs": [{"startedAt": r["started_at"], "status": r["status"],
                      "new": r["new_listings"], "alerts": r["alerts_sent"],
                      "errors": r["errors"]} for r in runs],
            "config": {"minDiscount": cfg.min_discount,
                       "laptopMinDiscount": cfg.laptop_min_discount,
                       "maxRisk": cfg.max_risk,
                       "requireShippable": cfg.require_shippable},
            "deals": [serialise(d, marks.get(d.listing_id)) for d in deals],
            "bands": market.bands,
        }


def make_handler(state: State, db: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DealSniper/1.0"

        def log_message(self, *args):                 # keep the console quiet
            pass

        def _send(self, status, body: bytes, ctype: str):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload, status=HTTPStatus.OK):
            self._send(status, json.dumps(payload).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _local_host(self) -> bool:
            """Only answer to the loopback names: a web page on another site can
            otherwise point its own domain at 127.0.0.1 (DNS rebinding) and read
            your listings through your browser."""
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]")
            return host in ("127.0.0.1", "localhost", "::1")

        def do_GET(self):
            if not self._local_host():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            url = urlparse(self.path)
            if url.path == "/api/state":
                refresh = "refresh" in parse_qs(url.query)
                return self._json(state.get(refresh))
            if url.path.startswith("/api/history/"):
                listing_id = url.path.rsplit("/", 1)[-1]
                store = Store(db=db).connect()
                try:
                    history = [{"price": r["price_cad"], "at": r["seen_at"]}
                               for r in store.price_history(listing_id)]
                finally:
                    store.close()
                return self._json({"history": history})
            return self._static(url.path)

        def _static(self, path: str):
            rel = "index.html" if path in ("", "/") else path.lstrip("/")
            target = (STATIC / rel).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, target.read_bytes(), ctype)

        def do_POST(self):
            if not self._local_host():
                return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
            if urlparse(self.path).path != "/api/mark":
                return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            # JSON-only forces a CORS preflight on cross-site requests, which this
            # server never approves, so other websites cannot write marks.
            if "application/json" not in (self.headers.get("Content-Type") or ""):
                return self._json({"error": "JSON required"},
                                  HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                listing_id, mark = str(body["id"]), body.get("mark")
                store = Store(db=db).connect()
                try:
                    store.set_mark(listing_id, mark)
                finally:
                    store.close()
            except (KeyError, ValueError, TypeError) as exc:
                return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            # Reflect the change in the cached payload without a full rescore.
            payload = state._payload
            if payload:
                for deal in payload["deals"]:
                    if deal["id"] == listing_id:
                        deal["mark"] = mark
            return self._json({"ok": True})

    return Handler


class Server(ThreadingHTTPServer):
    # Windows lets a second socket bind a port that is already in use when
    # SO_REUSEADDR is set (the default here), so a second copy would silently
    # share the port instead of failing. Refuse, so we can pick another one.
    allow_reuse_address = False
    daemon_threads = True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Deal Sniper local web UI.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default=DB)
    parser.add_argument("--no-open", action="store_true",
                        help="Don't open a browser window.")
    args = parser.parse_args(argv)

    for problem in apply_watchlist():
        print(f"watchlist.yaml: {problem}")
    state = State(args.db)
    server = None
    for port in range(args.port, args.port + 11):        # first free port wins
        try:
            server = Server(("127.0.0.1", port), make_handler(state, args.db))
            break
        except OSError:
            print(f"Port {port} is busy, trying the next one...", flush=True)
    if server is None:
        print("Couldn't find a free port. Close other copies of Deal Sniper and retry.")
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"Deal Sniper UI on {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
