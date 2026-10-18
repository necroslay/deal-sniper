"""New-retail price anchor — the ceiling on what a used item can be worth.

A rational buyer will not pay more used than the cheapest new alternative, so
retail bounds a predicted band from above; it is a *ceiling, not a price*.

Prices are supplied explicitly. There is no reliable free scrape for retail —
the archived engine documented Canada Computers and Vuugo responding while
Memory Express (Cloudflare), Newegg (CAPTCHA), Amazon and Best Buy (denied)
blocked outright — so this reads a hand-maintained CSV. When the file is absent
the ceiling is simply not applied and the model runs on peer data alone.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class RetailPrice:
    """A new-retail reference for one product."""

    product: str
    price_cad: float
    observed_at: str = ""


def load_retail(path: str | Path) -> dict[str, RetailPrice]:
    """Load ``product,price_cad[,observed_at]`` rows into a product -> price map.

    A missing file returns an empty map rather than raising: the ceiling is
    optional, and a model run must not fail because a human hasn't filled the
    reference in yet.
    """
    path = Path(path)
    if not path.exists():
        return {}

    prices: dict[str, RetailPrice] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            product = (row.get("product") or "").strip()
            raw = (row.get("price_cad") or "").strip()
            if not product or not raw:
                continue
            try:
                price = float(raw)
            except ValueError:
                continue
            if price <= 0:
                continue
            # Later rows win, so the file can be appended to over time.
            prices[product] = RetailPrice(
                product=product, price_cad=price,
                observed_at=(row.get("observed_at") or "").strip())
    return prices


def retail_for(prices: dict[str, RetailPrice], product: str
               ) -> Optional[float]:
    """Retail price for *product*, or None when we have no reference.

    Returning None (rather than 0) is what makes the ceiling optional: the
    caller leaves the band untouched for products with no reference. Applying
    the *ceiling* to a band is ``app.eval.price_harness.cap_at_retail``, which
    needs the per-row product lookup this function provides.
    """
    entry = prices.get(product)
    return entry.price_cad if entry else None
