"""Re-run the resolver and guard over listings already in the database.

Usage::

    python -m app.re_resolve              # rewrite stale product_key / flag_reason
    python -m app.re_resolve --dry-run    # report what would change, change nothing

``product_key`` and ``flag_reason`` are written once, at scrape time, by whatever
resolver and guard existed then. A resolver fix therefore does **not** reach rows
already stored. The clearest example: the registry used to carry bare ``"15"`` /
``"14"`` aliases for the iPhones, so every ``Dell XPS 15``, ``iMac 2015`` and
``ThinkCentre M715q`` was filed as an iPhone — and those rows stay wrong until
they are re-scraped or re-enriched. This command closes that gap.

Nothing here touches prices or timestamps: it re-derives the two enrichment
columns from the stored title and nothing else.
"""

from __future__ import annotations

import argparse
import logging
from typing import Optional

from app.db.store import Store
from app.engines.enrich import resolve_and_assess

logger = logging.getLogger(__name__)


def re_resolve(store: Store, dry_run: bool = False) -> dict:
    """Re-derive enrichment for every stored listing.

    Returns a summary: ``total``, ``changed``, and ``phantom_iphones`` (rows whose
    old key was an iPhone that the current resolver no longer assigns — the
    regression this command exists to repair).
    """
    total = changed = phantom_iphones = 0
    for row in store.all_listings():
        total += 1
        product, flag = resolve_and_assess(row["title"])
        new_key = product.standard_name or ""
        old_key = row["product_key"] or ""
        old_flag = row["flag_reason"] or ""

        if old_key.startswith("iPhone") and not new_key.startswith("iPhone"):
            phantom_iphones += 1

        if new_key == old_key and (flag or "") == old_flag:
            continue
        changed += 1
        if not dry_run:
            store.update_enrichment(row["id"], new_key, flag)
    return {"total": total, "changed": changed,
            "phantom_iphones": phantom_iphones}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Re-resolve stored listings with the current resolver.")
    parser.add_argument("--db", default=None,
                        help="SQLite database (default: app/listings.db).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report changes without writing them.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING)
    store = Store(**({"db": args.db} if args.db else {})).connect()
    try:
        summary = re_resolve(store, dry_run=args.dry_run)
    finally:
        store.close()

    verb = "would change" if args.dry_run else "updated"
    print(f"listings examined: {summary['total']}")
    print(f"{verb}:           {summary['changed']}")
    print(f"phantom iPhones repaired: {summary['phantom_iphones']}")
    if args.dry_run:
        print("\n(dry run — nothing was written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
