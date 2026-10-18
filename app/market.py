"""Score every listing against its comparable peers.

One scoring pass serves two readers:

* **alerts** want the few listings that cross the bar *and* are new, ranked and
  deduplicated (``alerts.find_deals``);
* **the UI** wants every scored listing, with the bar's verdict attached, so you
  can see the near-misses and judge the thresholds yourself (``explore=True``).

A "deal" is judged on the **landed cost** (price + shipping) against the asking
prices of comparable listings, by two routes: *keep* (at or below the product's
P25 and a clear margin under its median) and *flip* (expected resale profit
clears a profit and ROI bar). It is a claim about asking prices of comparable
listings, not about market value.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import numpy as np

from app.config import CFG
from app.engines import logistics, risk as risk_engine
from app.ml.features import build_price_rows, parse_timestamp, trim_outliers
from app.ml.price_model import ProductQuantileBaseline

# Titles that look cheap because the item is broken or isn't just the item. The
# rule guard doesn't cover these; an alert should never lead with a dead card.
NOT_A_BARGAIN = re.compile(
    r"\b(for parts|parts only|parts|repair|broken|not working|doesn'?t work|"
    r"defective|faulty|as[- ]is|artifacting|dead|no display|cracked|"
    r"locked|icloud lock|activation lock|bios lock|"
    r"combo|bundle|desktop|prebuilt|pc|wanted|looking for|iso|"
    # business desktops/servers whose model number collides with a GPU alias
    # ("Dell Optiplex 5090" is not an RTX 5090), and external GPU enclosures:
    r"optiplex|thinkcentre|poweredge|proliant|elitedesk|prodesk|egpu|"
    r"xg mobile)\b", re.IGNORECASE)
# A GPU listing that is really a laptop is a different (and wrong) comparison.
_LAPTOP_WORD = re.compile(r"\b(laptop|notebook)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Deal:
    """A scored listing: the maths, the risk, and whether it clears the bar."""

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
    route: str = "use"                 # use | flip | both | none
    net_profit_cad: float = 0.0
    roi: float = 0.0
    risk_score: int = 0
    risk_label: str = "low"
    risk_reasons: list = field(default_factory=list)
    condition: str = ""
    price_drop: bool = False
    qualifies: bool = True             # clears every alert threshold
    age_hours: float = 0.0             # since first seen
    description: str = ""
    seller_id: str = ""
    image: str = ""
    payment: str = ""
    seller_rating: Optional[float] = None
    seller_reviews: Optional[int] = None
    first_seen_at: str = ""

    @property
    def landed_cad(self) -> float:
        return self.price_cad + self.shipping_cad

    @property
    def discount(self) -> float:
        """Fraction the *landed* cost is under the median, e.g. 0.3 for 30%."""
        return 1.0 - self.landed_cad / self.median_cad if self.median_cad else 0.0

    @property
    def sniper_score(self) -> int:
        """0-100 ranking: how far under peers and how much profit, scaled down by
        risk (a 70-risk listing keeps only ~half its value), plus freshness."""
        depth = min(max(self.discount, 0.0) / 0.5, 1.0) * 60
        profit = min(max(self.net_profit_cad, 0.0) / 300.0, 1.0) * 20
        trust = 1.0 - 0.7 * self.risk_score / 100.0
        fresh = 10 if self.age_hours < 24 else 5 if self.age_hours < 72 else 0
        drop = 5 if self.price_drop else 0
        return int(max(0, min(100, round((depth + profit) * trust + fresh + drop))))


@dataclass
class Market:
    """The result of one scoring pass."""

    deals: list
    by_id: dict
    bands: dict                        # product -> {median, p25, p75, n, prices}


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


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def _first_image(raw: dict) -> str:
    import json
    try:
        value = json.loads(raw.get("image_urls") or "[]")
        return value[0] if isinstance(value, list) and value else ""
    except (ValueError, TypeError):
        return ""


def score_market(rows: Iterable, already_alerted: Optional[set] = None,
                 now: Optional[datetime] = None, explore: bool = False) -> Market:
    """Score listings against their peers.

    Args:
        rows: Stored listing rows (``Store.all_listings()``).
        already_alerted: Listing ids that have notified before (alert mode only).
        now: Clock override for tests.
        explore: UI mode. Score *every* active listing that has a trusted price
            band — no recency window, no alert history, no thresholds applied —
            and mark each with ``qualifies`` instead of dropping it.
    """
    cfg = CFG.alerts
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg.max_listing_age_hours)
    already_alerted = already_alerted or set()

    rows = list(rows)
    by_id = {str(dict(r)["id"]): dict(r) for r in rows}
    seller_counts = Counter(r["seller_id"] for r in by_id.values()
                            if r.get("seller_id"))

    # Comps: every priceable listing, with obvious nonsense prices trimmed.
    price_rows = build_price_rows(rows)
    comps = trim_outliers(price_rows)
    baseline = ProductQuantileBaseline(min_rows=cfg.min_comps).fit(comps)
    comp_counts = Counter(row.product for row in comps)

    bands: dict = {}
    if explore:
        by_product: dict[str, list[float]] = {}
        for row in comps:
            by_product.setdefault(row.product, []).append(row.price_cad)
        for product, prices in by_product.items():
            if baseline.has_band(product):
                arr = np.array(sorted(prices), dtype=float)
                bands[product] = {
                    "n": len(arr), "p25": float(np.percentile(arr, 25)),
                    "median": float(np.percentile(arr, 50)),
                    "p75": float(np.percentile(arr, 75)),
                    "prices": [round(float(p)) for p in arr[:: max(1, len(arr) // 80)]],
                }

    candidates = []
    for price_row in price_rows:
        raw = by_id.get(price_row.listing_id)
        if raw is None or raw.get("status") != "active":
            continue
        title = raw.get("title") or ""
        if NOT_A_BARGAIN.search(title):
            continue
        if price_row.category != "laptop" and _LAPTOP_WORD.search(title):
            continue
        if not baseline.has_band(price_row.product):
            continue
        if not explore:
            if price_row.listing_id in already_alerted:
                continue
            first_seen = parse_timestamp(raw.get("first_seen_at"))
            if first_seen is None or first_seen < cutoff:
                continue
        candidates.append(price_row)

    if not candidates:
        return Market([], by_id, bands)

    low, mid, _ = baseline.predict_band(candidates)
    deals: list[Deal] = []
    for price_row, p25, median in zip(candidates, low, mid):
        price = price_row.price_cad
        raw = by_id[price_row.listing_id]
        ratio_ok = median > 0 and price >= cfg.min_price_ratio * median
        if not ratio_ok and not explore:
            continue                                  # scam / placeholder price
        if median <= 0:
            continue

        fulfil = logistics.assess(raw, price_row.category)
        shippable = logistics.shippable_for_alert(fulfil.status)
        if not shippable and not explore:
            continue
        landed = price + fulfil.shipping_cad

        name = price_row.product.lower()
        allowed = ((not cfg.max_landed_cad or landed <= cfg.max_landed_cad)
                   and (not cfg.include or any(s.lower() in name for s in cfg.include))
                   and not any(s.lower() in name for s in cfg.exclude))
        if not allowed and not explore:
            continue

        min_discount = (cfg.laptop_min_discount if price_row.category == "laptop"
                        else cfg.min_discount)
        use_route = (landed <= p25 and landed <= (1.0 - min_discount) * median)
        profit, roi = resale_profit(median, landed, price, price_row.category)
        flip_route = profit >= cfg.min_profit_cad and roi >= cfg.min_roi
        if not (use_route or flip_route) and not explore:
            continue

        verdict = risk_engine.assess(
            raw, price_ratio=price / median, shipped=True,
            seller_listing_count=seller_counts.get(raw.get("seller_id") or "", 1),
            shipping_confirmed=fulfil.shippable)
        if verdict.score > cfg.max_risk and not explore:
            continue

        first_seen = parse_timestamp(raw.get("first_seen_at"))
        age = ((now - first_seen).total_seconds() / 3600.0) if first_seen else 9999.0
        qualifies = bool(ratio_ok and shippable and allowed
                         and (use_route or flip_route)
                         and verdict.score <= cfg.max_risk)

        deals.append(Deal(
            listing_id=price_row.listing_id, title=raw.get("title") or "",
            product=price_row.product, category=price_row.category,
            price_cad=price, shipping_cad=fulfil.shipping_cad,
            median_cad=float(median), p25_cad=float(p25),
            n_comps=comp_counts.get(price_row.product, 0),
            url=raw.get("url") or "", source=raw.get("source") or "",
            location=raw.get("location") or "", fulfillment=fulfil.status,
            shipping_stated=fulfil.shipping_stated,
            route=("both" if use_route and flip_route
                   else "use" if use_route else "flip" if flip_route else "none"),
            net_profit_cad=profit, roi=roi, risk_score=verdict.score,
            risk_label=verdict.label, risk_reasons=verdict.reasons,
            condition=raw.get("condition") or "",
            price_drop=bool(raw.get("price_drop")), qualifies=qualifies,
            age_hours=round(age, 1), description=(raw.get("description") or "")[:600],
            seller_id=raw.get("seller_id") or "", image=_first_image(raw),
            payment=raw.get("payment") or "",
            seller_rating=raw.get("seller_rating"),
            seller_reviews=raw.get("seller_reviews"),
            first_seen_at=raw.get("first_seen_at") or ""))
    return Market(deals, by_id, bands)
