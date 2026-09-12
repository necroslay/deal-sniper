"""Reusable listing-lifecycle orchestration.

The scrape → observe → retire → infer cycle is needed by both the demo runner
(``run_full_pipeline.py``) and the scheduled task layer
(``scheduler/tasks.py``), so it lives here rather than being duplicated inline.
It is thin glue over ``Store`` and ``engines/lifecycle`` — deliberately not a
pure engine, because it performs I/O.
"""

from __future__ import annotations

from typing import Optional

from app.config import CFG
from app.db.store import Store
from app.engines.lifecycle import build_inferred_comps


def refresh_inferred_comps(store: Store, *, min_days_listed: Optional[float] = None,
                           haircut_pct: Optional[float] = None) -> dict:
    """Build inferred comps from retired listings and persist any new ones.

    Args:
        store: Connected store.
        min_days_listed: Override the configured minimum time-on-market.
        haircut_pct: Override the configured inferred-comp haircut.

    Returns ``{"inferred_built": n, "inferred_added": n}``. Safe to call at any
    time: re-running only inserts comps that are not already stored, and inferred
    comps stay out of valuation unless ``lifecycle.use_inferred_comps`` is set.
    """
    gone = store.get_gone_listings(since_days=CFG.lifecycle.inference_window_days)
    comps = build_inferred_comps(gone, min_days_listed=min_days_listed,
                                 haircut_pct=haircut_pct)
    added = sum(1 for c in comps if store.insert_sold_comp(c))
    return {"inferred_built": len(comps), "inferred_added": added}


def observe_cycle(store: Store, listings, *, misses_before_gone: Optional[int] = None,
                  sources=None, min_days_listed: Optional[float] = None,
                  haircut_pct: Optional[float] = None) -> dict:
    """Close one observation cycle and derive inferred comps.

    Call this only after a *successful* scrape. Every listing in ``listings`` is
    treated as still on offer; every other active listing from ``sources`` earns
    a miss, and enough consecutive misses retire it as presumed sold.

    Args:
        store: Connected store.
        listings: The raw listings observed this cycle.
        misses_before_gone: Override for the configured retirement threshold.
        sources: The sources actually scraped. Restricting to these means a
            source that returned nothing (or failed) cannot retire its inventory.
        min_days_listed: Override for the inferred-comp minimum time-on-market.
        haircut_pct: Override for the inferred-comp haircut.

    Returns:
        ``{"missed", "gone", "inferred_built", "inferred_added"}``.
    """
    if not listings and not sources:
        # An empty cycle is a failed scrape, not evidence the whole market sold.
        # Refuse to advance retirement; only refresh already-retired comps.
        summary = {"missed": 0, "gone": 0}
        summary.update(refresh_inferred_comps(
            store, min_days_listed=min_days_listed, haircut_pct=haircut_pct))
        return summary

    seen = {(l.source.value, l.source_listing_id) for l in listings}
    effective_sources = sources
    if effective_sources is None and listings:
        effective_sources = {l.source for l in listings}

    summary = store.close_observation_cycle(
        seen_keys=seen, misses_before_gone=misses_before_gone,
        sources=effective_sources)
    summary.update(refresh_inferred_comps(
        store, min_days_listed=min_days_listed, haircut_pct=haircut_pct))
    return summary
