"""Uncertainty-based sampling of unlabeled listings for hand-labeling.

Labeling is the bottleneck, so labels should be spent where they change the
model. Before a model exists, "uncertainty" is the rules' own *abstention*: a
title the resolver leaves at Tier-3 is exactly the kind of row worth labeling,
and a title that trips one weak guard signal is a near-miss worth adjudicating.

Once a trained model exists, the same queue can be driven by model confidence
instead of rule abstention — the scoring is isolated here precisely so that
swap is a small change.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Optional, Sequence

from app.engines.listing_guard import assess_listing, rule_signals
from app.engines.resolver import find_products, resolve_product

QUEUE_COLUMNS = ("id", "source", "title", "priority", "reason",
                 "baseline_product", "baseline_flag")


def resolver_uncertainty(title: str) -> tuple[float, str]:
    """How unsure the resolver is about *title* (0.0 = fully resolved)."""
    product = resolve_product(title or "")
    if product.category == "unknown":
        return 1.0, "resolver_unknown"
    if len(find_products(title or "")) >= 2:
        return 0.5, "multiple_products"
    return 0.0, ""


def guard_uncertainty(title: str) -> tuple[float, str]:
    """How close the guard came to firing without firing (near-miss value)."""
    product = resolve_product(title or "")
    if assess_listing(title or "", product):
        return 0.0, ""  # the rules were confident — no labeling value

    signals = rule_signals(title or "")
    if signals["accessory"] + signals["host"] == 1:
        return 0.5, "single_weak_signal"
    if signals["product_count"] >= 2:
        return 0.4, "possible_lot"
    return 0.0, ""


SCORERS = {"resolver": resolver_uncertainty, "guard": guard_uncertainty}


def labeling_queue(rows: Iterable, task: str = "resolver",
                   limit: int = 200) -> list[dict]:
    """Rank unlabeled rows by labeling value, highest first.

    Only rows worth labeling (score > 0) are returned; ties break on title so
    the queue is stable across runs.
    """
    scorer = SCORERS[task]
    scored: list[dict] = []
    for row in rows:
        d = dict(row)
        title = d.get("title", "") or ""
        score, reason = scorer(title)
        if score <= 0:
            continue
        scored.append({
            "id": d.get("id", ""),
            "source": d.get("source", ""),
            "title": title,
            "priority": score,
            "reason": reason,
            "baseline_product": d.get("product_key", "") or "",
            "baseline_flag": d.get("flag_reason", "") or "",
        })
    scored.sort(key=lambda r: (-r["priority"], r["title"]))
    return scored[:limit]


def export_queue_csv(rows: Iterable, path: str | Path, task: str = "resolver",
                     limit: int = 200) -> int:
    """Write the prioritized labeling queue to a CSV; return rows written."""
    queue = labeling_queue(rows, task, limit)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(QUEUE_COLUMNS))
        writer.writeheader()
        writer.writerows(queue)
    return len(queue)
