"""Infer completed sales from listing disappearance.

Real sold comps are not reachable from any free source (see
``PROJECT_STATUS.md`` §5: eBay gates sold search behind sign-in and a CAPTCHA,
PriceCharting and 130point sit behind Cloudflare, and Swappa exposes asking
prices only). So a second, weaker signal is derived from data we *can* read:
listings tracked across scrapes. A listing that stops appearing is treated as
sold, at a haircut to its last observed asking price.

Everything produced here is flagged ``inferred=True`` and is excluded from comp
lookups unless a caller explicitly asks for it
(``Store.get_sold_comps(include_inferred=True)``). Inferred and observed comps
must never be silently mixed.

This module is pure: it takes already-fetched ``raw_listings`` rows and returns
``SoldComp`` objects. It performs no I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.config import CFG
from app.models.schema import Platform, SoldComp


def _parse_ts(value) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``. None on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _get(row, key):
    """Read *key* from a dict-like or ``sqlite3.Row``, returning None if absent."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def inferred_sale_price(last_asking_price_cad: float, haircut_pct: float) -> float:
    """Return the inferred clearing price for a disappeared listing.

    Sellers rarely realise their full asking price on used goods, so the last
    observed asking price is discounted. The haircut lives in config
    (``lifecycle.inferred_comp_haircut_pct``) rather than being hard-coded here.

    Args:
        last_asking_price_cad: The final observed asking price.
        haircut_pct: Fraction to discount, e.g. ``0.05`` for 5%.

    Returns:
        The inferred price in CAD, rounded to cents and never below zero.
    """
    if last_asking_price_cad <= 0:
        return 0.0
    return round(max(0.0, last_asking_price_cad * (1.0 - haircut_pct)), 2)


def build_inferred_comp(row, *, haircut_pct: Optional[float] = None,
                        min_days_listed: Optional[float] = None
                        ) -> Optional[SoldComp]:
    """Turn one disappeared listing row into an inferred ``SoldComp``.

    Args:
        row: A ``raw_listings`` row (dict-like) with ``status='gone'``.
        haircut_pct: Override for the configured inferred-comp haircut.
        min_days_listed: Minimum time a listing must have been observed for its
            disappearance to count as a sale. Filters out posts that were pulled
            within hours — a typo, a test post, or an instant removal — which are
            not evidence of a sale.

    Returns:
        A ``SoldComp`` with ``inferred=True``, or None if the row does not
        qualify (no usable price, too short-lived).
    """
    price = float(row["price_cad"] or 0)
    if price <= 0:
        return None

    cfg = CFG.lifecycle
    haircut = cfg.inferred_comp_haircut_pct if haircut_pct is None else haircut_pct
    min_days = (cfg.min_days_listed_for_inference
                if min_days_listed is None else min_days_listed)

    first_seen = _parse_ts(row["first_seen_at"])
    last_seen = _parse_ts(row["last_seen_at"])
    # When the cycle noticed the listing had gone. This is a better end-of-life
    # marker than the last sighting: a listing observed once and then absent for
    # several cycles would otherwise look as though it lasted zero days.
    gone_at = _parse_ts(_get(row, "gone_at"))
    reference = last_seen or first_seen

    end_of_life = gone_at or last_seen or first_seen
    if first_seen and end_of_life:
        listed_days = (end_of_life - first_seen).total_seconds() / 86_400.0
        if listed_days < min_days:
            return None

    return SoldComp(
        source=Platform(row["source"]),
        source_listing_id=row["source_listing_id"],
        title=row["title"],
        sold_price_cad=inferred_sale_price(price, haircut),
        # The last sighting is the closest thing to a sale date we have.
        sold_date=(reference or datetime.now(timezone.utc)).isoformat(),
        condition="",
        url=row["url"] or "",
        location=row["location"] or "",
        inferred=True,
    )


def build_inferred_comps(rows, **kwargs) -> list[SoldComp]:
    """Build inferred comps for a batch of disappeared-listing rows.

    Rows are skipped (not raised on) when they fail :func:`build_inferred_comp`.
    """
    comps: list[SoldComp] = []
    for row in rows:
        comp = build_inferred_comp(row, **kwargs)
        if comp is not None:
            comps.append(comp)
    return comps
