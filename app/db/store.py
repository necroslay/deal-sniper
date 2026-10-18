"""SQLite persistence for scraped listings.

Deliberately narrow: this project stores listings and tracks whether each one is
still on offer, so you can see what's new and what has disappeared. The valuation
tables that used to live here (sold comps, retail references, decisions, source
health) belong to the full engine at git tag ``full-engine-v1``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import CFG

logger = logging.getLogger("store")

DB = str(Path(__file__).resolve().parent.parent / "listings.db")

SQL = """CREATE TABLE IF NOT EXISTS raw_listings (
    id TEXT PRIMARY KEY, source TEXT NOT NULL,
    source_listing_id TEXT NOT NULL, title TEXT NOT NULL,
    price_cad REAL NOT NULL, url TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    date_posted TEXT, image_urls TEXT DEFAULT '[]',
    scraped_at TEXT NOT NULL,
    product_key TEXT,
    flag_reason TEXT,
    first_seen_at TEXT, last_seen_at TEXT,
    miss_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', gone_at TEXT,
    UNIQUE(source, source_listing_id));
CREATE TABLE IF NOT EXISTS scrape_coverage (
    source TEXT PRIMARY KEY,
    pages INTEGER NOT NULL,
    observed_at TEXT NOT NULL);"""


class Store:
    """One row per unique listing, keyed on ``(source, source_listing_id)``."""

    def __init__(self, db: str = DB):
        self._db = db
        self._conn = None
        self._cur = None

    # ── lifecycle ───────────────────────────────────────────────────────────

    def connect(self) -> "Store":
        self._conn = sqlite3.connect(self._db)
        self._conn.row_factory = sqlite3.Row
        self._cur = self._conn.cursor()
        self._cur.executescript(SQL)
        self._conn.commit()
        self._migrate()
        return self

    def _migrate(self) -> None:
        """Add columns missing from a database written by an earlier version.

        SQLite has no ``ADD COLUMN IF NOT EXISTS``, so each is checked against
        ``PRAGMA table_info`` first.
        """
        existing = {r["name"] for r in
                    self._cur.execute("PRAGMA table_info(raw_listings)")}
        for name, decl in (
            ("product_key", "TEXT"),
            ("flag_reason", "TEXT"),
            ("first_seen_at", "TEXT"),
            ("last_seen_at", "TEXT"),
            ("miss_count", "INTEGER NOT NULL DEFAULT 0"),
            ("status", "TEXT NOT NULL DEFAULT 'active'"),
            ("gone_at", "TEXT"),
        ):
            if name not in existing:
                self._cur.execute(
                    f"ALTER TABLE raw_listings ADD COLUMN {name} {decl}")
        # Seed the lifecycle clock from the scrape time so rows written before
        # this tracking existed are not treated as absent.
        self._cur.execute(
            "UPDATE raw_listings SET first_seen_at = scraped_at "
            "WHERE first_seen_at IS NULL")
        self._cur.execute(
            "UPDATE raw_listings SET last_seen_at = scraped_at "
            "WHERE last_seen_at IS NULL")
        # Indexes are created here, not in SQL above: on a legacy database the
        # columns they reference do not exist until this migration has run.
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_status "
            "ON raw_listings(status)")
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_product "
            "ON raw_listings(product_key)")
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
        self._conn = self._cur = None

    def __enter__(self) -> "Store":
        return self.connect()

    def __exit__(self, *exc) -> None:
        self.close()

    def _check(self) -> None:
        if not self._cur:
            raise RuntimeError("Store not connected")

    # ── writing ─────────────────────────────────────────────────────────────

    def observe_listing(self, listing, observed_at: Optional[str] = None,
                        product_key: Optional[str] = None,
                        flag_reason: Optional[str] = None) -> bool:
        """Record one sighting of *listing*.

        Returns True if the listing is new, False if it was already known. Each
        sighting refreshes ``last_seen_at`` and clears the miss counter, which is
        what makes a disappeared listing detectable.

        Args:
            listing: A ``RawListing``.
            observed_at: Timestamp override (defaults to now, UTC).
            product_key: Canonical product name from the resolver, stored so the
                viewer can group listings by product.
            flag_reason: Why the guard flagged this listing (accessory, lot, ...)
                so the viewer can hide junk.
        """
        self._check()

        if not (listing.source_listing_id or "").strip():
            # A listing with no source id cannot be identified. Storing it would
            # file it under the blank key ``(source, '')``, where every later
            # id-less listing would silently overwrite it — one row instead of
            # hundreds, with no error. Refuse it loudly instead.
            logger.warning(
                "Skipping listing with no source id (source=%s, title=%r)",
                listing.source.value, (listing.title or "")[:60],
            )
            return False

        now = observed_at or datetime.now(timezone.utc).isoformat()
        row = self._cur.execute(
            "SELECT id FROM raw_listings WHERE source = ? AND source_listing_id = ?",
            (listing.source.value, listing.source_listing_id)).fetchone()

        if row is None:
            self._cur.execute(
                "INSERT INTO raw_listings "
                "(id, source, source_listing_id, title, price_cad, url, location, "
                "description, date_posted, image_urls, scraped_at, product_key, "
                "flag_reason, first_seen_at, last_seen_at, miss_count, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'active')",
                (str(uuid.uuid4()), listing.source.value,
                 listing.source_listing_id, listing.title, listing.price_cad,
                 listing.url, listing.location or "", listing.description or "",
                 listing.date_posted, json.dumps(listing.image_urls),
                 listing.scraped_at, product_key, flag_reason, now, now))
            self._conn.commit()
            return True

        self._cur.execute(
            "UPDATE raw_listings SET last_seen_at = ?, miss_count = 0, "
            "status = 'active', gone_at = NULL, title = ?, price_cad = ?, "
            "description = ?, product_key = ?, flag_reason = ? WHERE id = ?",
            (now, listing.title, listing.price_cad, listing.description or "",
             product_key, flag_reason, row["id"]))
        self._conn.commit()
        return False

    def update_enrichment(self, listing_id: str, product_key: Optional[str],
                          flag_reason: Optional[str]) -> None:
        """Overwrite one listing's ``product_key`` / ``flag_reason``.

        Those two columns are written once, at scrape time, by whatever resolver
        and guard existed then — so a resolver fix does **not** reach rows already
        stored. This is the write path for ``app.re_resolve``, which brings them
        up to date without re-scraping.
        """
        self._check()
        self._cur.execute(
            "UPDATE raw_listings SET product_key = ?, flag_reason = ? "
            "WHERE id = ?", (product_key, flag_reason, listing_id))
        self._conn.commit()

    # ── scrape coverage ─────────────────────────────────────────────────────

    def previous_coverage(self, source: str) -> Optional[int]:
        """Pages scraped from *source* on the previous run, or None if unknown."""
        self._check()
        row = self._cur.execute(
            "SELECT pages FROM scrape_coverage WHERE source = ?", (source,)
        ).fetchone()
        return int(row["pages"]) if row else None

    def record_coverage(self, pages_by_source: dict) -> None:
        """Remember how deep this run went, so the next run can compare.

        Args:
            pages_by_source: ``{"kijiji": 3, "craigslist": 2}``.
        """
        self._check()
        now = datetime.now(timezone.utc).isoformat()
        for source, pages in (pages_by_source or {}).items():
            self._cur.execute(
                "INSERT INTO scrape_coverage (source, pages, observed_at) "
                "VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET "
                "pages = excluded.pages, observed_at = excluded.observed_at",
                (source, int(pages), now))
        self._conn.commit()

    def close_observation_cycle(self, seen_keys, misses_before_gone: Optional[int] = None,
                                sources=None, pages_by_source=None) -> dict:
        """Retire listings that have not reappeared for several scrapes.

        Args:
            seen_keys: ``(source, source_listing_id)`` pairs seen this cycle.
            misses_before_gone: Override for the configured threshold.
            sources: Sources actually scraped. Only those are considered, so a
                source that returned nothing cannot retire its whole inventory.
            pages_by_source: Pages scraped per source this run. A source swept
                **less deeply than its previous run is skipped** — a shallow
                sweep is not evidence that anything is gone, so its unseen
                listings must not accrue a miss. Without this, running
                ``--pages 1`` after a 3-page run would falsely retire every
                listing that only the deeper pages had reached.

        Returns:
            ``{"missed": n, "gone": n, "skipped_sources": [names]}``.
        """
        self._check()
        threshold = (misses_before_gone if misses_before_gone is not None
                     else CFG.lifecycle.misses_before_gone)
        now = datetime.now(timezone.utc).isoformat()
        seen = set(seen_keys or ())

        candidates = [s.value if hasattr(s, "value") else str(s)
                      for s in (sources or [])]

        # Which sources may we draw conclusions from this run?
        skipped_sources: list[str] = []
        eligible = candidates
        if candidates and pages_by_source:
            eligible = []
            for name in candidates:
                current = pages_by_source.get(name)
                previous = self.previous_coverage(name)
                if current is not None and previous is not None and current < previous:
                    skipped_sources.append(name)
                else:
                    eligible.append(name)

        missed = gone = 0

        # An empty *candidates* list means "no source restriction", which is the
        # historical behaviour; a non-empty list with nothing eligible means this
        # run could not judge anything.
        if eligible or not candidates:
            sql = ("SELECT id, source, source_listing_id, miss_count "
                   "FROM raw_listings WHERE status = 'active'")
            params: list = []
            if candidates:
                sql += " AND source IN (%s)" % ",".join("?" for _ in eligible)
                params = list(eligible)

            for row in self._cur.execute(sql, params).fetchall():
                if (row["source"], row["source_listing_id"]) in seen:
                    continue
                missed += 1
                misses = (row["miss_count"] or 0) + 1
                if misses >= threshold:
                    self._cur.execute(
                        "UPDATE raw_listings SET miss_count = ?, status = 'gone', "
                        "gone_at = ? WHERE id = ?", (misses, now, row["id"]))
                    gone += 1
                else:
                    self._cur.execute(
                        "UPDATE raw_listings SET miss_count = ? WHERE id = ?",
                        (misses, row["id"]))

        # Record this run's depth regardless — even for skipped sources, since
        # the *next* run compares against this one.
        if pages_by_source:
            self.record_coverage(pages_by_source)
        else:
            self._conn.commit()

        return {"missed": missed, "gone": gone,
                "skipped_sources": skipped_sources}

    # ── reading ─────────────────────────────────────────────────────────────

    def all_listings(self, include_gone: bool = True):
        """Return every stored listing, newest first.

        Sorting falls back to ``first_seen_at`` because Craigslist listings
        sometimes arrive without a posted date.
        """
        self._check()
        where = "" if include_gone else "WHERE status = 'active'"
        return self._cur.execute(
            f"SELECT * FROM raw_listings {where} "
            "ORDER BY COALESCE(date_posted, first_seen_at) DESC").fetchall()

    def counts(self) -> dict:
        """Totals for the viewer header.

        Returns ``total``, ``active``, ``gone`` and ``by_source``.
        """
        self._check()
        row = self._cur.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) AS active, "
            "SUM(CASE WHEN status = 'gone' THEN 1 ELSE 0 END) AS gone "
            "FROM raw_listings").fetchone()
        by_source = {r["source"]: r["n"] for r in self._cur.execute(
            "SELECT source, COUNT(*) AS n FROM raw_listings GROUP BY source")}
        return {
            "total": int(row["total"] or 0),
            "active": int(row["active"] or 0),
            "gone": int(row["gone"] or 0),
            "by_source": by_source,
        }
