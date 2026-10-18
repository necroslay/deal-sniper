"""Feature table for the price model.

Rebuilt from the listings database on every run, and every title is *re-resolved*
with the current resolver rather than trusting the stored ``product_key``. That
matters: a stored key was written by whatever resolver existed at scrape time, so
rows scraped before a resolver fix carry stale labels (the ``iPhone 15`` phantom
matches being the obvious example) that would otherwise poison the features.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

import numpy as np

from app.engines.enrich import resolve_and_assess

# Coarse condition signal from the title. Deliberately shallow: a real condition
# classifier lives at git tag full-engine-v1, and inventing a graded one here
# would be claiming more than a keyword match supports.
_NEW_PATTERNS = re.compile(
    r"\b(new|sealed|bnib|brand new|unopened|never used|mint)\b", re.IGNORECASE)


@dataclass(frozen=True)
class PriceRow:
    """One listing prepared for price modelling."""

    listing_id: str
    product: str
    category: str
    source: str
    price_cad: float
    posted_at: str
    age_days: float
    title_len: int
    desc_len: int
    n_images: int
    is_new: int


def parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    """Parse the ISO-8601 timestamps the scrapers write (``Z`` or offset)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _image_count(raw) -> int:
    try:
        value = json.loads(raw) if isinstance(raw, str) else (raw or [])
        return len(value) if isinstance(value, list) else 0
    except (ValueError, TypeError):
        return 0


def build_price_rows(rows: Iterable) -> list[PriceRow]:
    """Turn stored listing rows into price rows, skipping what can't be priced.

    A row is dropped when it has no positive price, no parseable posted date, or
    the current resolver/guard rejects it (unknown product, accessory, lot, a
    component inside a host system, ...).

    ``age_days`` is measured against the row's own ``scraped_at`` rather than
    "now", so the feature — and therefore the whole evaluation — is reproducible
    instead of drifting as wall-clock time passes.
    """
    out: list[PriceRow] = []
    for row in rows:
        data = dict(row)
        price = float(data.get("price_cad") or 0)
        if price <= 0:
            continue
        posted = parse_timestamp(data.get("date_posted"))
        if posted is None:
            continue
        product, flag = resolve_and_assess(data.get("title") or "")
        if product.category == "unknown" or flag:
            continue

        reference = parse_timestamp(data.get("scraped_at")) or posted
        title = data.get("title") or ""
        out.append(PriceRow(
            listing_id=str(data.get("id") or ""),
            product=product.standard_name,
            category=product.category,
            source=str(data.get("source") or ""),
            price_cad=price,
            posted_at=posted.isoformat(),
            age_days=max(0.0, (reference - posted).total_seconds() / 86400.0),
            title_len=len(title),
            desc_len=len(data.get("description") or ""),
            n_images=_image_count(data.get("image_urls")),
            is_new=1 if _NEW_PATTERNS.search(title) else 0,
        ))
    return out


def trim_outliers(rows: list[PriceRow], factor: float = 1.5,
                  min_group: int = 8) -> list[PriceRow]:
    """Drop rows outside an IQR fence, computed per product.

    Real asking prices include placeholders (``$1``) and obvious nonsense
    (``$10,000`` for an RTX 3080). Left in, they dominate absolute error and make
    every model look worse than it is. Fences are per product when the product
    has enough rows to estimate one, else global.
    """
    if not rows:
        return []

    def fence(prices: np.ndarray) -> tuple[float, float]:
        q1, q3 = np.percentile(prices, [25, 75])
        iqr = q3 - q1
        return q1 - factor * iqr, q3 + factor * iqr

    by_product: dict[str, list[float]] = {}
    for row in rows:
        by_product.setdefault(row.product, []).append(row.price_cad)
    global_fence = fence(np.array([r.price_cad for r in rows], dtype=float))

    kept: list[PriceRow] = []
    for row in rows:
        prices = by_product[row.product]
        low, high = (fence(np.array(prices, dtype=float))
                     if len(prices) >= min_group else global_fence)
        if low <= row.price_cad <= high:
            kept.append(row)
    return kept


def temporal_split(rows: list[PriceRow], test_fraction: float = 0.25
                   ) -> tuple[list[PriceRow], list[PriceRow]]:
    """Split by ``date_posted``: oldest rows train, newest rows test.

    Sorted by (posted_at, listing_id) so the boundary is deterministic and a
    listing can never appear on both sides. This is the split that matches how
    the model is used — predicting on newly-posted listings from older history.
    """
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda r: (r.posted_at, r.listing_id))
    cut = int(round(len(ordered) * (1.0 - test_fraction)))
    cut = min(max(cut, 1), len(ordered) - 1) if len(ordered) > 1 else len(ordered)
    return ordered[:cut], ordered[cut:]
