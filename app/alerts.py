"""Deal detection and Discord notifications.

A "deal" is a listing priced well under its peers: the product has enough
comparable listings to trust its own price band, and this one sits at or below
the band's P25 *and* a clear margin under its median. That is a claim about
**asking prices of comparable listings**, not about market value.

Each listing alerts at most once (``alerts_sent``), and only after the webhook
accepted the message, so a Discord outage retries on the next run instead of
silently dropping the deal.

The webhook URL is a secret. It is read from the environment (or a gitignored
``.env`` file) and is never logged or printed.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import requests

from app.config import CFG
from app.ml.features import build_price_rows, parse_timestamp, trim_outliers
from app.ml.price_model import ProductQuantileBaseline

logger = logging.getLogger("alerts")

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_DISCORD_EMBEDS_PER_MESSAGE = 10
_GREEN, _RED = 0x2ECC71, 0xE74C3C


# Titles that look cheap because the item is broken or isn't just the card. The
# rule guard doesn't cover these; an alert should never lead with a dead card.
_NOT_A_BARGAIN = re.compile(
    r"\b(for parts|parts only|parts|repair|broken|not working|doesn'?t work|"
    r"defective|faulty|as[- ]is|artifacting|dead|no display|laptop|notebook|"
    r"combo|bundle|desktop|prebuilt|pc)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Deal:
    """A listing that qualified, with the band it was judged against."""

    listing_id: str
    title: str
    product: str
    price_cad: float
    median_cad: float
    p25_cad: float
    n_comps: int
    url: str
    source: str
    location: str

    @property
    def discount(self) -> float:
        """Fraction under the median, e.g. 0.3 for 30% below."""
        return 1.0 - self.price_cad / self.median_cad


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
    cfg = CFG.alerts
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg.max_listing_age_hours)

    rows = list(rows)
    by_id = {str(dict(r)["id"]): dict(r) for r in rows}

    # Comps: every priceable listing, with obvious nonsense prices trimmed.
    comps = trim_outliers(build_price_rows(rows))
    baseline = ProductQuantileBaseline(min_rows=cfg.min_comps).fit(comps)
    comp_counts: dict[str, int] = {}
    for row in comps:
        comp_counts[row.product] = comp_counts.get(row.product, 0) + 1

    # Candidates: active, recently first seen, not alerted before.
    candidates = []
    for price_row in build_price_rows(rows):
        raw = by_id.get(price_row.listing_id)
        if raw is None or price_row.listing_id in already_alerted:
            continue
        if raw.get("status") != "active":
            continue
        if _NOT_A_BARGAIN.search(raw.get("title") or ""):
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
        if median <= 0 or price > p25:
            continue
        if price < cfg.min_price_ratio * median:
            continue
        if price > (1.0 - cfg.min_discount) * median:
            continue
        raw = by_id[price_row.listing_id]
        deals.append(Deal(
            listing_id=price_row.listing_id, title=raw.get("title") or "",
            product=price_row.product, price_cad=price,
            median_cad=float(median), p25_cad=float(p25),
            n_comps=comp_counts.get(price_row.product, 0),
            url=raw.get("url") or "", source=raw.get("source") or "",
            location=raw.get("location") or ""))

    deals.sort(key=lambda d: d.discount, reverse=True)
    return deals[:cfg.max_per_run]


def build_payloads(deals: list[Deal]) -> list[dict]:
    """Discord webhook bodies for *deals*, chunked to the embed limit."""
    payloads = []
    for start in range(0, len(deals), _DISCORD_EMBEDS_PER_MESSAGE):
        embeds = []
        for d in deals[start:start + _DISCORD_EMBEDS_PER_MESSAGE]:
            embeds.append({
                "title": f"{d.product}: ${d.price_cad:,.0f}"[:256],
                **({"url": d.url} if d.url else {}),
                "description": d.title[:300],
                "color": _GREEN,
                "fields": [
                    {"name": "vs. comparable asks",
                     "value": (f"{d.discount:.0%} under median "
                               f"${d.median_cad:,.0f} (P25 ${d.p25_cad:,.0f}, "
                               f"{d.n_comps} comps)"), "inline": False},
                    {"name": "Where", "value": f"{d.source} {d.location}".strip()
                     or "unknown", "inline": True},
                ],
            })
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
            print(f"  [dry-run] {d.product} ${d.price_cad:,.0f} "
                  f"({d.discount:.0%} under median ${d.median_cad:,.0f}) {d.url}")
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
