"""Scrape Kijiji and Craigslist into the local database.

Usage::

    python -m app.scrape                      # both sources, configured depth
    python -m app.scrape --source kijiji
    python -m app.scrape --pages 1            # quick run
    python -m app.scrape --db other.db

Every listing is stored once (keyed on source + listing id). Repeat runs refresh
``last_seen_at`` instead of duplicating, and listings that stop appearing are
retired to ``status='gone'`` — which is how the viewer can show what's new and
what has disappeared.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys
from collections import Counter

# Allow both `python -m app.scrape` and `python app/scrape.py`.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.adapters.sources import get_adapter
from app.config import CFG
from app.db.store import Store
from app.engines.enrich import resolve_and_assess
from app.models.schema import Platform

# Search terms per source. Kijiji's search is keyword-based, so several narrow
# terms surface more distinct listings than one broad term; Craigslist's relevant
# listings live in the "cpd" (computer parts) category.
KIJIJI_QUERY = "ALL"          # sweeps the term list in kijiji-scraper/config.yaml
CRAIGSLIST_QUERY = "graphics card"
CRAIGSLIST_CATEGORY = "cpd"


def _pages_for(platform: Platform, override: int | None) -> int:
    """Pages to scrape, from the CLI override or config."""
    if override:
        return override
    if platform == Platform.kijiji:
        return CFG.scraper.kijiji_pages
    return CFG.scraper.craigslist_pages


def scrape_source(platform: Platform, pages: int) -> list:
    """Scrape one marketplace. Returns a list of ``RawListing``."""
    adapter = get_adapter(platform)
    if platform == Platform.kijiji:
        return adapter.search(KIJIJI_QUERY, max_pages=pages, high_end_only=False)
    return adapter.search(CRAIGSLIST_QUERY, max_pages=pages,
                          category=CRAIGSLIST_CATEGORY)


def dedupe_by_url(listings: list) -> list:
    """Drop repeats — the same listing can surface under several search terms."""
    seen = set()
    unique = []
    for listing in listings:
        if listing.url and listing.url in seen:
            continue
        seen.add(listing.url)
        unique.append(listing)
    return unique


def store_listings(store: Store, listings: list) -> tuple[set, int, int, Counter]:
    """Persist *listings*, enriched with a product name and a junk flag.

    Product resolution and the junk check happen here rather than in the Store,
    so the persistence layer stays a plain store.

    Listings with no source id are skipped and counted: they cannot be
    identified, and filing them under a blank key would let each one silently
    overwrite the previous.

    Returns ``(seen_keys, new, skipped, product_counts)``.
    """
    seen_keys: set = set()
    new = skipped = 0
    products: Counter = Counter()

    for listing in listings:
        if not (listing.source_listing_id or "").strip():
            skipped += 1
            continue
        product, flag = resolve_and_assess(listing.title)
        if store.observe_listing(listing, product_key=product.standard_name,
                                 flag_reason=flag):
            new += 1
        seen_keys.add((listing.source.value, listing.source_listing_id))
        products[product.standard_name] += 1

    return seen_keys, new, skipped, products


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scrape marketplace listings into the local database.")
    parser.add_argument("--source", choices=["kijiji", "craigslist", "all"],
                        default="all", help="Which marketplace(s) to scrape.")
    parser.add_argument("--pages", type=int, default=None,
                        help="Pages per source (overrides config).")
    parser.add_argument("--db", default=None,
                        help="SQLite database path (defaults to app/listings.db).")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    platforms = ([Platform.kijiji, Platform.craigslist]
                 if args.source == "all" else [Platform(args.source)])

    store = Store(**({"db": args.db} if args.db else {})).connect()

    collected: list = []
    scraped: list[Platform] = []
    pages_used: dict = {}
    for platform in platforms:
        pages = _pages_for(platform, args.pages)
        print(f"Scraping {platform.value} ({pages} page(s))...")
        try:
            found = scrape_source(platform, pages)
        except Exception as exc:  # a dead source must not kill the run
            print(f"  !! {platform.value} failed: {type(exc).__name__}: {exc}")
            continue
        print(f"  {platform.value}: {len(found)} listing(s)")
        if found:
            scraped.append(platform)
            collected.extend(found)
            pages_used[platform.value] = pages

    if not collected:
        print("\nNothing scraped — the database was left unchanged.")
        store.close()
        return 1

    listings = dedupe_by_url(collected)
    seen_keys, new, skipped, products = store_listings(store, listings)

    # Retire listings that stopped appearing. Restricted to the sources we
    # actually read, so a source that returned nothing cannot retire inventory —
    # and skipped entirely for a source swept less deeply than last time, since a
    # shallow sweep is not evidence that anything is gone.
    cycle = store.close_observation_cycle(
        seen_keys=seen_keys, sources=scraped, pages_by_source=pages_used)

    counts = store.counts()
    print(f"\nScraped {len(collected)} listing(s), {len(listings)} unique "
          f"({len(collected) - len(listings)} duplicate URL(s) dropped)")
    print(f"  new: {new}   refreshed: {len(listings) - new - skipped}")
    if skipped:
        print(f"  !! {skipped} listing(s) skipped — no source id, so they "
              f"cannot be identified. Check that scraper's id parsing.")
    print(f"  lifecycle: {cycle['missed']} missed, {cycle['gone']} newly gone")
    if cycle["skipped_sources"]:
        print(f"  !! retirement skipped for {', '.join(cycle['skipped_sources'])}: "
              f"fewer pages than the previous run, so unseen listings were not "
              f"counted as gone.")
    print(f"  database: {counts['total']} total, {counts['active']} active, "
          f"{counts['gone']} gone  ({counts['by_source']})")

    print("\nTop products:")
    for name, n in products.most_common(10):
        print(f"  {name[:38]:<40s} {n:>3}")

    store.close()
    print("\nNow run:  streamlit run app/dashboard.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
