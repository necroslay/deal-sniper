"""Import new-retail reference prices from a CSV or JSON file.

Retail anchors let the pipeline value a listing when no sold comps exist
(``FMV = retail × M_condition``, tagged ``basis="retail"``). There is no reliable
free scrape for these (see ``PROJECT_STATUS.md`` §5), so prices are supplied
explicitly — from a retailer page, a quote, or a licensed feed.

Usage::

    python -m app.tools.import_retail_prices --file retail.json
    python -m app.tools.import_retail_prices --file retail.csv --db app\\resale_engine.db

Input format — JSON (a list) or CSV (with a header row)::

    product_key,price_cad,retailer,url
    RTX 3080,999.99,canada_computers,https://www.canadacomputers.com/...

``product_key`` must be a canonical product name. Names that do not appear in
``PRODUCT_REGISTRY`` are reported as warnings: a retail price stored under an
unreachable key would never be looked up, which is exactly the silent-registry-gap
bug the project has been bitten by before.
"""

from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys

from app.db.store import Store
from app.engines.resolver import PRODUCT_REGISTRY
from app.models.schema import RetailPrice


def load_rows(path: pathlib.Path) -> list[dict]:
    """Read rows from a ``.json`` or ``.csv`` file.

    Args:
        path: File to read.

    Returns:
        A list of dicts. Raises ValueError on an unrecognised extension or
        malformed content.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError("JSON must be a list of objects")
        return data
    if path.suffix.lower() == ".csv":
        return list(csv.DictReader(text.splitlines()))
    raise ValueError(f"Unsupported file type: {path.suffix} (use .json or .csv)")


def import_rows(store: Store, rows: list[dict]) -> dict:
    """Validate and upsert *rows* into the retail price table.

    Returns:
        ``{"imported": n, "skipped": n, "unknown_products": [names]}``.
    """
    known = {entry["standard_name"] for entry in PRODUCT_REGISTRY}
    imported = skipped = 0
    unknown: list[str] = []

    for raw in rows:
        try:
            retail = RetailPrice(
                product_key=str(raw["product_key"]).strip(),
                price_cad=float(raw["price_cad"]),
                retailer=str(raw.get("retailer", "") or "").strip(),
                url=str(raw.get("url", "") or "").strip(),
            )
        except (KeyError, TypeError, ValueError) as exc:
            print(f"  !! skipping malformed row {raw!r}: {exc}", file=sys.stderr)
            skipped += 1
            continue

        if retail.price_cad <= 0:
            print(f"  !! skipping non-positive price for {retail.product_key}",
                  file=sys.stderr)
            skipped += 1
            continue

        if retail.product_key not in known:
            unknown.append(retail.product_key)

        store.insert_retail_price(retail)
        imported += 1

    return {"imported": imported, "skipped": skipped,
            "unknown_products": sorted(set(unknown))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import new-retail reference prices (CSV or JSON).")
    parser.add_argument("--file", required=True, type=pathlib.Path,
                        help="CSV or JSON file of retail prices.")
    parser.add_argument("--db", default=None,
                        help="SQLite database path (defaults to the app DB).")
    args = parser.parse_args(argv)

    if not args.file.exists():
        print(f"ERROR: no such file: {args.file}", file=sys.stderr)
        return 2

    rows = load_rows(args.file)
    store = Store(**({"db": args.db} if args.db else {}))
    store.connect()
    try:
        summary = import_rows(store, rows)
    finally:
        store.close()

    print(f"Imported {summary['imported']} retail price(s), "
          f"skipped {summary['skipped']}.")
    if summary["unknown_products"]:
        print("\nWARNING: these product_key(s) are not in PRODUCT_REGISTRY, so no "
              "listing can ever match them:")
        for name in summary["unknown_products"]:
            print(f"   - {name}")
        print("Add them to app/engines/resolver.py or fix the spelling.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
