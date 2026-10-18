"""Deal detection and Discord notifications.

A "deal" is a listing you could actually buy by mail and come out ahead on. For
each candidate the maths is on the **landed cost** (price + shipping), not the
sticker price, against the asking prices of comparable listings:

* **use route** — landed cost is at or below the product's P25 *and* a clear
  margin under its median: cheap enough to keep.
* **flip route** — expected resale (median x a sold-vs-asked haircut), minus
  marketplace fees, outbound shipping, landed cost and a risk reserve, clears a
  profit and ROI bar.

That is a claim about **asking prices of comparable listings**, not market value.
Listings the seller won't ship (when ``require_shippable``) and listings that look
scam-shaped (``engines.risk``) are dropped before they reach you.

Each listing alerts at most once (``alerts_sent``), and only after the webhook
accepted the message, so a Discord outage retries on the next run instead of
silently dropping the deal. The webhook URL is a secret: read from the
environment or a gitignored ``.env``, never logged or printed.
"""

from __future__ import annotations

import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

from app.config import CFG
from app.engines import logistics, risk as risk_engine
from app.ml.features import build_price_rows, parse_timestamp, trim_outliers
from app.ml.price_model import ProductQuantileBaseline

logger = logging.getLogger("alerts")

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_DISCORD_EMBEDS_PER_MESSAGE = 10
_GREEN, _AMBER, _RED = 0x2ECC71, 0xF39C12, 0xE74C3C

# Titles that look cheap because the item is broken or isn't just the item. The
# rule guard doesn't cover these; an alert should never lead with a dead card.
_NOT_A_BARGAIN = re.compile(
    r"\b(for parts|parts only|parts|repair|broken|not working|doesn'?t work|"
    r"defective|faulty|as[- ]is|artifacting|dead|no display|cracked|"
    r"locked|icloud lock|activation lock|bios lock|"
    r"combo|bundle|desktop|prebuilt|pc|wanted|looking for|iso)\b", re.IGNORECASE)
# A GPU listing that is really a laptop is a different (and wrong) comparison.
_LAPTOP_WORD = re.compile(r"\b(laptop|notebook)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Deal:
    """A listing that qualified, with the maths that qualified it."""

    listing_id: str
    title: str
    product: str
    category: str
    price_cad: float
    shipping_cad: float
    median_cad: float
    p25_cad: float
    n_comps: int
    url: str
    source: str
    location: str
    fulfillment: str = "unknown"
    shipping_stated: bool = False
    route: str = "use"                 # use | flip | both
    net_profit_cad: float = 0.0
    roi: float = 0.0
    risk_score: int = 0
    risk_label: str = "low"
    risk_reasons: list = field(default_factory=list)
    condition: str = ""
    price_drop: bool = False

    @property
    def landed_cad(self) -> float:
        return self.price_cad + self.shipping_cad

    @property
    def discount(self) -> float:
        """Fraction the *landed* cost is under the median, e.g. 0.3 for 30%."""
        return 1.0 - self.landed_cad / self.median_cad


def load_env_file(path: Path = ENV_FILE) -> None:
    """Load ``KEY=VALUE`` lines into ``os.environ`` without overriding real vars."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def webhook_url() -> Optional[str]:
    """The configured webhook URL, or None when alerts are not set up."""
    load_env_file()
    return os.environ.get(CFG.alerts.webhook_env) or None


def resale_profit(median: float, landed: float, price: float,
                  category: str) -> tuple[float, float]:
    """``(net_profit, roi)`` from buying at *landed* and reselling near *median*."""
    p = CFG.profit
    sale = median * p.ask_to_sold
    fees = sale * p.resale_fee_rate + p.resale_fixed_fee_cad
    outbound = logistics.outbound_shipping(category, sale)
    reserve = price * p.risk_reserve_rate
    profit = sale - fees - outbound - landed - reserve
    return profit, (profit / landed if landed > 0 else 0.0)


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def find_deals(rows: Iterable, already_alerted: set,
               now: Optional[datetime] = None) -> list[Deal]:
    """Pick the listings worth alerting on, best first.

    Args:
        rows: Stored listing rows (``Store.all_listings()``).
        already_alerted: Listing ids that have notified before.
        now: Clock override for tests.
    """
    cfg = CFG.alerts
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg.max_listing_age_hours)

    rows = list(rows)
    by_id = {str(dict(r)["id"]): dict(r) for r in rows}
    seller_counts = Counter(r["seller_id"] for r in by_id.values()
                            if r.get("seller_id"))

    # Comps: every priceable listing, with obvious nonsense prices trimmed.
    price_rows = build_price_rows(rows)
    comps = trim_outliers(price_rows)
    baseline = ProductQuantileBaseline(min_rows=cfg.min_comps).fit(comps)
    comp_counts = Counter(row.product for row in comps)

    # Candidates: active, recently first seen, not alerted before.
    candidates = []
    for price_row in price_rows:
        raw = by_id.get(price_row.listing_id)
        if raw is None or price_row.listing_id in already_alerted:
            continue
        if raw.get("status") != "active":
            continue
        title = raw.get("title") or ""
        if _NOT_A_BARGAIN.search(title):
            continue
        if price_row.category != "laptop" and _LAPTOP_WORD.search(title):
            continue
        first_seen = parse_timestamp(raw.get("first_seen_at"))
        if first_seen is None or first_seen < cutoff:
            continue
        if not baseline.has_band(price_row.product):
            continue
        candidates.append(price_row)

    if not candidates:
        return []

    low, mid, _ = baseline.predict_band(candidates)
    deals: list[Deal] = []
    for price_row, p25, median in zip(candidates, low, mid):
        price = price_row.price_cad
        raw = by_id[price_row.listing_id]
        if median <= 0 or price < cfg.min_price_ratio * median:
            continue                                  # scam / placeholder price

        fulfil = logistics.assess(raw, price_row.category)
        if not logistics.shippable_for_alert(fulfil.status):
            continue
        landed = price + fulfil.shipping_cad

        if cfg.max_landed_cad and landed > cfg.max_landed_cad:
            continue
        name = price_row.product.lower()
        if cfg.include and not any(s.lower() in name for s in cfg.include):
            continue
        if any(s.lower() in name for s in cfg.exclude):
            continue

        min_discount = (cfg.laptop_min_discount if price_row.category == "laptop"
                        else cfg.min_discount)
        use_route = (landed <= p25 and landed <= (1.0 - min_discount) * median)
        profit, roi = resale_profit(median, landed, price, price_row.category)
        flip_route = profit >= cfg.min_profit_cad and roi >= cfg.min_roi
        if not (use_route or flip_route):
            continue

        verdict = risk_engine.assess(
            raw, price_ratio=price / median, shipped=True,
            seller_listing_count=seller_counts.get(raw.get("seller_id") or "", 1),
            shipping_confirmed=fulfil.shippable)
        if verdict.score > cfg.max_risk:
            continue

        deals.append(Deal(
            listing_id=price_row.listing_id, title=raw.get("title") or "",
            product=price_row.product, category=price_row.category,
            price_cad=price, shipping_cad=fulfil.shipping_cad,
            median_cad=float(median), p25_cad=float(p25),
            n_comps=comp_counts.get(price_row.product, 0),
            url=raw.get("url") or "", source=raw.get("source") or "",
            location=raw.get("location") or "", fulfillment=fulfil.status,
            shipping_stated=fulfil.shipping_stated,
            route="both" if use_route and flip_route
            else "use" if use_route else "flip",
            net_profit_cad=profit, roi=roi, risk_score=verdict.score,
            risk_label=verdict.label, risk_reasons=verdict.reasons,
            condition=raw.get("condition") or "",
            price_drop=bool(raw.get("price_drop"))))

    # One alert per item: a seller cross-posting the same ad in ten cities, or
    # relisting it under a new headline, would otherwise fill the channel. The
    # same seller offering the same product at the same price is the same item.
    best: dict = {}
    for d in deals:
        raw = by_id[d.listing_id]
        seller = raw.get("seller_id")
        key = ((seller, d.product, round(d.price_cad)) if seller
               else (d.listing_id, _norm_title(d.title)))
        if key not in best or d.landed_cad < best[key].landed_cad:
            best[key] = d
    unique = sorted(best.values(),
                    key=lambda d: (d.net_profit_cad, d.discount), reverse=True)
    return unique[:cfg.max_per_run]


def _color(deal: Deal) -> int:
    return {"low": _GREEN, "medium": _AMBER}.get(deal.risk_label, _RED)


def build_payloads(deals: list[Deal]) -> list[dict]:
    """Discord webhook bodies for *deals*, chunked to the embed limit."""
    payloads = []
    for start in range(0, len(deals), _DISCORD_EMBEDS_PER_MESSAGE):
        embeds = []
        for d in deals[start:start + _DISCORD_EMBEDS_PER_MESSAGE]:
            ship = ("$%.0f shipping%s" % (d.shipping_cad,
                    "" if d.shipping_stated else " (est.)"))
            if d.fulfillment != "ships":
                ship += ", **ask if they ship**"
            money = (f"**${d.landed_cad:,.0f} landed** "
                     f"(${d.price_cad:,.0f} + {ship})\n"
                     f"{d.discount:.0%} under median ${d.median_cad:,.0f} "
                     f"(P25 ${d.p25_cad:,.0f}, {d.n_comps} comps)")
            if d.net_profit_cad > 0:
                money += (f"\nResale profit ~${d.net_profit_cad:,.0f} "
                          f"({d.roi:.0%} ROI)")
            risk = f"{d.risk_label} ({d.risk_score})"
            if d.risk_reasons:
                risk += ": " + "; ".join(d.risk_reasons[:3])
            notes = []
            if d.price_drop:
                notes.append("price just dropped")
            if d.condition:
                notes.append(d.condition.replace(",", ", "))
            embed = {
                "title": f"{d.product}: ${d.price_cad:,.0f}"[:256],
                **({"url": d.url} if d.url else {}),
                "description": d.title[:300],
                "color": _color(d),
                "fields": [
                    {"name": "Cost vs comparable asks", "value": money[:1024],
                     "inline": False},
                    {"name": "Risk", "value": risk[:1024], "inline": False},
                    {"name": "Where", "value": (f"{d.source} {d.location}".strip()
                     or "unknown")[:256], "inline": True},
                    {"name": "Route", "value": {"use": "keep", "flip": "flip",
                     "both": "keep / flip"}[d.route], "inline": True},
                ],
            }
            if notes:
                embed["footer"] = {"text": "; ".join(notes)[:256]}
            embeds.append(embed)
        payloads.append({"username": "Deal Sniper", "embeds": embeds})
    return payloads


def post_payload(url: str, payload: dict, timeout: float = 10.0) -> bool:
    """POST to the webhook. Returns True on success; never raises or logs *url*."""
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("Discord post failed: %s", type(exc).__name__)
        return False
    if resp.status_code >= 300:
        logger.warning("Discord rejected the message (HTTP %s)", resp.status_code)
        return False
    return True


def send_health_alert(message: str) -> bool:
    """Tell the channel the pipeline itself is unhealthy (scraper down, etc.)."""
    url = webhook_url()
    if not url:
        return False
    return post_payload(url, {"username": "Deal Sniper", "embeds": [{
        "title": "Scrape problem", "description": message[:1800],
        "color": _RED}]})


def run_alerts(store, dry_run: bool = False) -> dict:
    """Find new deals and notify. Returns ``{"found": n, "sent": n, "configured": bool}``.

    With ``dry_run`` deals are printed instead of posted and nothing is marked
    as alerted, so it is safe to try against real data.
    """
    deals = find_deals(store.all_listings(include_gone=False), store.alerted_ids())
    url = webhook_url()

    if dry_run:
        for d in deals:
            print(f"  [dry-run] {d.product} ${d.landed_cad:,.0f} landed "
                  f"({d.discount:.0%} under median ${d.median_cad:,.0f}, "
                  f"profit ~${d.net_profit_cad:,.0f}, risk {d.risk_label}, "
                  f"{d.route}) {d.url}")
        return {"found": len(deals), "sent": 0, "configured": bool(url)}
    if not url:
        return {"found": len(deals), "sent": 0, "configured": False}

    sent = 0
    for start in range(0, len(deals), _DISCORD_EMBEDS_PER_MESSAGE):
        chunk = deals[start:start + _DISCORD_EMBEDS_PER_MESSAGE]
        (payload,) = build_payloads(chunk)
        if post_payload(url, payload):
            for d in chunk:
                store.mark_alerted(d.listing_id, d.price_cad)
            sent += len(chunk)
    return {"found": len(deals), "sent": sent, "configured": True}
