"""How scam-shaped is this listing? A 0–100 score with reasons.

Buying by mail from a stranger is where the money is lost: no in-person check, and
Kijiji/Craigslist offer no buyer protection. This is a heuristic, not a verdict —
it exists to push the worst-looking listings out of your alerts and to put a
reason next to the rest, so you know what to ask the seller.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

# Payment methods that can't be reversed or traced.
_UNSAFE_PAYMENT = re.compile(
    r"\b(e-?transfer only|interac only|western union|gift ?card|bitcoin|crypto|"
    r"zelle|venmo|cash ?app|wire transfer|deposit (first|required)|"
    r"send (the )?(money|payment) first)\b", re.IGNORECASE)

_PROLIFIC_SELLER = 8


@dataclass(frozen=True)
class Risk:
    score: int
    reasons: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return "low" if self.score < 35 else "medium" if self.score < 55 else "high"


def _image_count(raw) -> int:
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return len(value) if isinstance(value, list) else 0
    except (ValueError, TypeError):
        return 0


def assess(row: dict, price_ratio: float, shipped: bool,
           seller_listing_count: int = 1,
           shipping_confirmed: bool = True) -> Risk:
    """Score *row*.

    Args:
        row: A stored listing row.
        price_ratio: asking price / comparable median. Far below 1 is the
            classic scam shape.
        shipped: True if the purchase would travel by mail.
        seller_listing_count: How many listings this seller has in the database.
        shipping_confirmed: False when the ad never says it ships, so the deal
            depends on the seller agreeing to.
    """
    score, reasons = 15, []

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(reason)

    if shipped:
        add(15, "shipped sight-unseen, no buyer protection")
    if not shipping_confirmed:
        add(8, "shipping not stated - ask the seller")

    payment = {p.strip() for p in (row.get("payment") or "").split(",") if p.strip()}
    if payment == {"cashaccepted"}:
        add(10, "cash-only seller, awkward to pay remotely")
    elif "cashless" in payment:
        score -= 5
        reasons.append("accepts cashless payment")
    if price_ratio < 0.5:
        add(25, f"priced {1 - price_ratio:.0%} under comparable asks")
    elif price_ratio < 0.65:
        add(10, f"priced {1 - price_ratio:.0%} under comparable asks")

    images = _image_count(row.get("image_urls"))
    if images == 0:
        add(20, "no photos")
    elif images == 1:
        add(8, "only one photo")

    if len((row.get("description") or "").strip()) < 40:
        add(8, "very short description")

    text = f"{row.get('title') or ''}\n{row.get('description') or ''}"
    if _UNSAFE_PAYMENT.search(text):
        add(30, "asks for untraceable payment")

    seller_type = (row.get("seller_type") or "").lower()
    if "dealer" in seller_type or "business" in seller_type:
        score -= 8
        reasons.append("business seller")

    if seller_listing_count >= _PROLIFIC_SELLER:
        add(10, f"seller has {seller_listing_count} listings")

    return Risk(score=max(0, min(100, score)), reasons=reasons)
