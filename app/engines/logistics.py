"""Can this item be shipped to me, and what does it really cost?

Two inputs, in order of trust:

1. **Structured data** from the marketplace (Kijiji's ``fulfillment`` attribute).
2. **Free text** in the title/description — "will ship", "pickup only".

An explicit refusal in the text ("no shipping", "pickup only") overrides a
structured "shipping" tag, because sellers tick the box and then restrict it in
the description often enough that trusting the tag alone would send you to the
wrong side of the country.

Costs are *estimates* (``CFG.logistics``); a stated seller charge in the text
("shipping $25") wins over the table.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from app.config import CFG

SHIPS, PICKUP_ONLY, UNKNOWN = "ships", "pickup_only", "unknown"

_NO_SHIP = re.compile(
    r"\b(no ship(ping|ped)?|won'?t ship|will not ship|not shipping|"
    r"pick ?-?up only|local pick ?-?up only|local only|in[- ]person only|"
    r"cash and carry|must pick ?up|meet ?-?up only)\b", re.IGNORECASE)

_SHIP = re.compile(
    r"\b(will ship|can ship|able to ship|willing to ship|ship(ping)? "
    r"(is )?(available|possible|extra|offered)|ships? (across|anywhere|to all|"
    r"within|canada)|shipping (at )?(buyer|your)|buyer pays (for )?shipping|"
    r"canada post|purolator|fedex|free shipping|delivery "
    r"(available|possible))\b", re.IGNORECASE)

_STATED_SHIPPING = re.compile(
    r"\bshipping\s*(?:is|cost|costs|fee|of|:|-)?\s*\$?\s*(\d{1,3}(?:\.\d{2})?)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Logistics:
    """The fulfilment verdict for one listing."""

    status: str                       # ships | pickup_only | unknown
    shipping_cad: float               # estimated or stated cost to receive it
    shipping_stated: bool             # True if the seller named the charge

    @property
    def shippable(self) -> bool:
        return self.status == SHIPS


def fulfillment_status(fulfillment: str, title: str = "",
                       description: str = "") -> str:
    """ships / pickup_only / unknown from structured data, then text."""
    text = f"{title}\n{description}"
    tags = {t.strip().lower() for t in (fulfillment or "").split(",") if t.strip()}

    if _NO_SHIP.search(text):
        return PICKUP_ONLY
    if tags & {"shipping", "delivery", "ships"}:
        return SHIPS
    if _SHIP.search(text):
        return SHIPS
    if tags and tags <= {"pickup", "curbside"}:
        return PICKUP_ONLY
    return UNKNOWN


def shipping_estimate(category: str, price_cad: float,
                      description: str = "") -> tuple[float, bool]:
    """``(cost, stated)``: the seller's stated charge, else the table estimate."""
    m = _STATED_SHIPPING.search(description or "")
    if m:
        stated = float(m.group(1))
        if 5 <= stated <= 150:                      # ignore "shipping 2 days"
            return stated, True
    table = CFG.logistics.shipping_cad
    base = float(table.get(category, table.get("default", 30.0)))
    return base + CFG.logistics.insured_value_rate * max(price_cad, 0.0), False


def assess(row: dict, category: str) -> Logistics:
    """The :class:`Logistics` for a stored listing row."""
    status = fulfillment_status(row.get("fulfillment") or "",
                                row.get("title") or "",
                                row.get("description") or "")
    cost, stated = shipping_estimate(category, float(row.get("price_cad") or 0),
                                     row.get("description") or "")
    return Logistics(status=status, shipping_cad=cost, shipping_stated=stated)


def outbound_shipping(category: str, sale_price: float) -> float:
    """What it costs to ship the item on to a buyer when reselling."""
    cost, _ = shipping_estimate(category, sale_price)
    return cost


def shippable_for_alert(status: str) -> bool:
    """Apply the user's travel preference to a fulfilment status."""
    if not CFG.alerts.require_shippable:
        return True
    if status == SHIPS:
        return True
    return status == UNKNOWN and CFG.alerts.unknown_counts_as_shippable
