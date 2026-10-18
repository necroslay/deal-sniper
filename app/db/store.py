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
CREATE TABLE IF NOT EXISTS price_history (
    listing_id TEXT NOT NULL,
    price_cad REAL NOT NULL,
    seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scrape_coverage (
    source TEXT PRIMARY KEY,
    pages INTEGER NOT NULL,
    observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL, finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    sources TEXT NOT NULL DEFAULT '', collected INTEGER NOT NULL DEFAULT 0,
    unique_listings INTEGER NOT NULL DEFAULT 0, new_listings INTEGER NOT NULL DEFAULT 0,
    newly_gone INTEGER NOT NULL DEFAULT 0, alerts_sent INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS user_marks (
    listing_id TEXT PRIMARY KEY,
    mark TEXT NOT NULL,
    marked_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts_sent (
    listing_id TEXT PRIMARY KEY,
    alerted_at TEXT NOT NULL,
    price_cad REAL NOT NULL);"""


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
            ("fulfillment", "TEXT NOT NULL DEFAULT ''"),
            ("condition", "TEXT NOT NULL DEFAULT ''"),
            ("payment", "TEXT NOT NULL DEFAULT ''"),
            ("seller_id", "TEXT NOT NULL DEFAULT ''"),
            ("seller_type", "TEXT NOT NULL DEFAULT ''"),
            ("price_drop", "INTEGER NOT NULL DEFAULT 0"),
            ("shipping_cad", "REAL"),
            ("seller_rating", "REAL"),
            ("seller_reviews", "INTEGER"),
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
        # Seed price history for rows stored before it existed, so a later price
        # change has an earlier point to compare against.
        self._cur.execute(
            "INSERT INTO price_history (listing_id, price_cad, seen_at) "
            "SELECT id, price_cad, COALESCE(first_seen_at, scraped_at) "
            "FROM raw_listings WHERE id NOT IN "
            "(SELECT DISTINCT listing_id FROM price_history)")
        # Indexes are created here, not in SQL above: on a legacy database the
        # columns they reference do not exist until this migration has run.
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_status "
            "ON raw_listings(status)")
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_product "
            "ON raw_listings(product_key)")
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_price_history_listing "
            "ON price_history(listing_id)")
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
            "SELECT id, price_cad FROM raw_listings "
            "WHERE source = ? AND source_listing_id = ?",
            (listing.source.value, listing.source_listing_id)).fetchone()

        extras = (getattr(listing, "fulfillment", "") or "",
                  getattr(listing, "condition", "") or "",
                  getattr(listing, "payment", "") or "",
                  getattr(listing, "seller_id", "") or "",
                  getattr(listing, "seller_type", "") or "",
                  int(bool(getattr(listing, "price_drop", False))),
                  getattr(listing, "shipping_cad", None),
                  getattr(listing, "seller_rating", None),
                  getattr(listing, "seller_reviews", None))

        if row is None:
            listing_pk = str(uuid.uuid4())
            self._cur.execute(
                "INSERT INTO raw_listings "
                "(id, source, source_listing_id, title, price_cad, url, location, "
                "description, date_posted, image_urls, scraped_at, product_key, "
                "flag_reason, first_seen_at, last_seen_at, miss_count, status, "
                "fulfillment, condition, payment, seller_id, seller_type, price_drop, "
                "shipping_cad, seller_rating, seller_reviews) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,'active',?,?,?,?,?,?,?,?,?)",
                (listing_pk, listing.source.value,
                 listing.source_listing_id, listing.title, listing.price_cad,
                 listing.url, listing.location or "", listing.description or "",
                 listing.date_posted, json.dumps(listing.image_urls),
                 listing.scraped_at, product_key, flag_reason, now, now) + extras)
            self._cur.execute(
                "INSERT INTO price_history (listing_id, price_cad, seen_at) "
                "VALUES (?,?,?)", (listing_pk, listing.price_cad, now))
            self._conn.commit()
            return True

        self._cur.execute(
            "UPDATE raw_listings SET last_seen_at = ?, miss_count = 0, "
            "status = 'active', gone_at = NULL, title = ?, price_cad = ?, "
            "description = ?, product_key = ?, flag_reason = ?, "
            "fulfillment = ?, condition = ?, payment = ?, seller_id = ?, "
            "seller_type = ?, price_drop = ?, shipping_cad = ?, "
            "seller_rating = ?, seller_reviews = ? WHERE id = ?",
            (now, listing.title, listing.price_cad, listing.description or "",
             product_key, flag_reason) + extras + (row["id"],))
        # Only a changed price is history; an unchanged one is just a re-sighting.
        if row["price_cad"] != listing.price_cad:
            self._cur.execute(
                "INSERT INTO price_history (listing_id, price_cad, seen_at) "
                "VALUES (?,?,?)", (row["id"], listing.price_cad, now))
        self._conn.commit()
        return False

    def price_history(self, listing_id: str) -> list:
        """Every recorded price for one listing, oldest first."""
        self._check()
        return self._cur.execute(
            "SELECT price_cad, seen_at FROM price_history WHERE listing_id = ? "
            "ORDER BY seen_at", (listing_id,)).fetchall()

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

    # ── run history ─────────────────────────────────────────────────────────

    def start_run(self, sources: list[str]) -> int:
        """Open a ``scrape_runs`` row and return its id."""
        self._check()
        cur = self._cur.execute(
            "INSERT INTO scrape_runs (started_at, sources) VALUES (?, ?)",
            (datetime.now(timezone.utc).isoformat(), ",".join(sources)))
        self._conn.commit()
        return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, collected: int = 0,
                   unique_listings: int = 0, new_listings: int = 0,
                   newly_gone: int = 0, alerts_sent: int = 0,
                   errors: Optional[list[str]] = None) -> None:
        """Close a run with its outcome. *status* is ok / partial / failed."""
        self._check()
        self._cur.execute(
            "UPDATE scrape_runs SET finished_at = ?, status = ?, collected = ?, "
            "unique_listings = ?, new_listings = ?, newly_gone = ?, "
            "alerts_sent = ?, errors = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), status, collected,
             unique_listings, new_listings, newly_gone, alerts_sent,
             "\n".join(errors or []), run_id))
        self._conn.commit()

    def recent_runs(self, limit: int = 10):
        """Most recent runs first."""
        self._check()
        return self._cur.execute(
            "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    # ── your own triage (saved / contacted / dismissed) ─────────────────────

    MARKS = ("saved", "contacted", "dismissed")

    def set_mark(self, listing_id: str, mark: Optional[str]) -> None:
        """Remember what you decided about a listing; ``None`` clears it."""
        self._check()
        if mark is None:
            self._cur.execute("DELETE FROM user_marks WHERE listing_id = ?",
                              (listing_id,))
        elif mark in self.MARKS:
            self._cur.execute(
                "INSERT INTO user_marks (listing_id, mark, marked_at) VALUES (?,?,?) "
                "ON CONFLICT(listing_id) DO UPDATE SET mark = excluded.mark, "
                "marked_at = excluded.marked_at",
                (listing_id, mark, datetime.now(timezone.utc).isoformat()))
        else:
            raise ValueError(f"unknown mark {mark!r}")
        self._conn.commit()

    def marks(self) -> dict:
        """``{listing_id: mark}`` for every listing you've triaged."""
        self._check()
        return {r["listing_id"]: r["mark"] for r in
                self._cur.execute("SELECT listing_id, mark FROM user_marks")}

    # ── alert dedup ─────────────────────────────────────────────────────────

    def alerted_ids(self) -> set:
        """Listing ids that have already triggered a notification."""
        self._check()
        return {r["listing_id"] for r in
                self._cur.execute("SELECT listing_id FROM alerts_sent")}

    def mark_alerted(self, listing_id: str, price_cad: float) -> None:
        self._check()
        self._cur.execute(
            "INSERT OR IGNORE INTO alerts_sent (listing_id, alerted_at, price_cad) "
            "VALUES (?,?,?)",
            (listing_id, datetime.now(timezone.utc).isoformat(), float(price_cad)))
        self._conn.commit()

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
