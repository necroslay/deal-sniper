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
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import requests

from app.config import CFG
from app.market import (Deal, Market, NOT_A_BARGAIN, norm_title,  # noqa: F401
                        resale_profit, score_market)

logger = logging.getLogger("alerts")

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_DISCORD_EMBEDS_PER_MESSAGE = 10
_GREEN, _AMBER, _RED = 0x2ECC71, 0xF39C12, 0xE74C3C


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


def find_deals(rows: Iterable, already_alerted: set,
               now: Optional[datetime] = None) -> list[Deal]:
    """Pick the listings worth alerting on, best first.

    Args:
        rows: Stored listing rows (``Store.all_listings()``).
        already_alerted: Listing ids that have notified before.
        now: Clock override for tests.
    """
    market = score_market(rows, already_alerted, now)
    by_id = market.by_id

    # One alert per item: a seller cross-posting the same ad in ten cities, or
    # relisting it under a new headline, would otherwise fill the channel. The
    # same seller offering the same product at the same price is the same item.
    best: dict = {}
    for d in market.deals:
        seller = by_id[d.listing_id].get("seller_id")
        key = ((seller, d.product, round(d.price_cad)) if seller
               else (d.listing_id, norm_title(d.title)))
        if key not in best or d.landed_cad < best[key].landed_cad:
            best[key] = d
    unique = sorted(best.values(),
                    key=lambda d: (d.net_profit_cad, d.discount), reverse=True)
    return unique[:CFG.alerts.max_per_run]


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
