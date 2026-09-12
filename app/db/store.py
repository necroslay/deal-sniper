"""SQLite persistence store for the valuation engine."""
from __future__ import annotations
import json, logging, re, sqlite3, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from app.config import CFG
from app.engines.resolver import resolve_product
from app.models.schema import (
    FeedbackEvent,
    Platform,
    RawListing,
    RetailPrice,
    SoldComp,
    ValuationOutput,
)
from app.valuation.gate import passes_alert_gate
logger = logging.getLogger("store")
DB = str(Path(__file__).resolve().parent.parent / "resale_engine.db")


def derive_product_key(title: str) -> str:
    """Return the canonical product key for a comp title.

    Resolution normally comes from the product registry. If no alias matches, the
    title is returned *with any trailing index stripped* — without that fallback
    every unresolved comp would get a unique key (``"GTX 1080 0"``, ``"GTX 1080 1"``)
    and become unreachable by an exact-match lookup.
    """
    resolved = resolve_product(title).standard_name
    if resolved != title:
        return resolved
    stripped = re.sub(r"\s+\d+$", "", title.strip())
    return stripped or title

SQL = """CREATE TABLE IF NOT EXISTS raw_listings (
    id TEXT PRIMARY KEY, source TEXT NOT NULL,
    source_listing_id TEXT NOT NULL, title TEXT NOT NULL,
    price_cad REAL NOT NULL, url TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '', description TEXT NOT NULL DEFAULT '',
    date_posted TEXT, image_urls TEXT DEFAULT '[]',
    scraped_at TEXT NOT NULL,
    first_seen_at TEXT, last_seen_at TEXT,
    miss_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active', gone_at TEXT,
    -- Why the pipeline refused to value this listing (accessory, lot, price too
    -- low, ...). Rejected listings are kept for audit but must never feed the
    -- inferred-comp path — a $40 "GPU power cable" is not an RTX 4090 sale.
    skip_reason TEXT,
    UNIQUE(source, source_listing_id));
CREATE TABLE IF NOT EXISTS valuation_decisions (
    id TEXT PRIMARY KEY, listing_id TEXT NOT NULL,
    canonical_product TEXT NOT NULL, fmv_cad REAL, erv_cad REAL,
    deal_score REAL, confidence_level TEXT,
    alert_sent INTEGER NOT NULL DEFAULT 0, decision_payload TEXT);
CREATE TABLE IF NOT EXISTS source_health (
    id TEXT PRIMARY KEY, source TEXT NOT NULL UNIQUE,
    last_success_at TEXT, last_failure_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    is_blocked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sold_comps (
    id TEXT PRIMARY KEY, source TEXT NOT NULL,
    source_listing_id TEXT NOT NULL, title TEXT NOT NULL,
    sold_price_cad REAL NOT NULL, sold_date TEXT NOT NULL,
    condition TEXT NOT NULL DEFAULT '', url TEXT, location TEXT,
    scraped_at TEXT, product_key TEXT,
    inferred INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, source_listing_id));
CREATE TABLE IF NOT EXISTS retail_prices (
    id TEXT PRIMARY KEY, product_key TEXT NOT NULL,
    price_cad REAL NOT NULL, retailer TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL,
    UNIQUE(product_key, retailer));
CREATE INDEX IF NOT EXISTS idx_comps_name ON sold_comps(title);
CREATE INDEX IF NOT EXISTS idx_retail_product ON retail_prices(product_key);"""

class Store:
    def __init__(self, db=DB):
        self._db = db; self._conn = None; self._cur = None
    def connect(self):
        self._conn = sqlite3.connect(self._db)
        self._conn.row_factory = sqlite3.Row
        self._cur = self._conn.cursor()
        self._cur.executescript(SQL); self._conn.commit()
        self._migrate()

    def _ensure_columns(self, table, columns):
        """Add any missing columns to *table*.

        SQLite has no ``ADD COLUMN IF NOT EXISTS``, so each column is checked
        against ``PRAGMA table_info`` first. Needed because the schema above uses
        ``CREATE TABLE IF NOT EXISTS`` and so never alters an existing table.
        """
        existing = {r["name"] for r in self._cur.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                self._cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def _migrate(self):
        """Bring an older database up to date and backfill derived columns.

        ``product_key`` was added to ``sold_comps`` to fix comp-lookup
        contamination. ``inferred`` separates lifecycle-inferred comps from
        observed sales. The ``raw_listings`` lifecycle columns make it possible
        to tell a listing that vanished (likely sold) from one still on offer.
        Databases created before any of these need the columns added and
        existing rows backfilled.
        """
        self._ensure_columns("sold_comps", {
            "product_key": "TEXT",
            "inferred": "INTEGER NOT NULL DEFAULT 0",
        })
        self._ensure_columns("raw_listings", {
            "first_seen_at": "TEXT",
            "last_seen_at": "TEXT",
            "miss_count": "INTEGER NOT NULL DEFAULT 0",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "gone_at": "TEXT",
            "skip_reason": "TEXT",
        })
        # Seed the lifecycle clock from the scraped timestamp so pre-existing
        # rows are not instantly considered overdue/absent.
        self._cur.execute(
            "UPDATE raw_listings SET first_seen_at = scraped_at "
            "WHERE first_seen_at IS NULL"
        )
        self._cur.execute(
            "UPDATE raw_listings SET last_seen_at = scraped_at "
            "WHERE last_seen_at IS NULL"
        )

        # Created here rather than in SQL: on a legacy database the column above
        # does not exist until this migration runs, and CREATE INDEX on a missing
        # column would fail.
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_comps_product_key "
            "ON sold_comps(product_key)"
        )
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_listings_status "
            "ON raw_listings(status)"
        )

        stale = self._cur.execute(
            "SELECT id, title, product_key FROM sold_comps "
            "WHERE product_key IS NULL OR product_key = '' OR product_key = title"
        ).fetchall()
        for row in stale:
            self._cur.execute(
                "UPDATE sold_comps SET product_key = ? WHERE id = ?",
                (derive_product_key(row["title"]), row["id"]),
            )
        if stale:
            logger.info("Backfilled product_key for %d sold comps", len(stale))
        self._conn.commit()
    def close(self):
        if self._conn: self._conn.close()
        self._conn = self._cur = None
    def __enter__(self): self.connect(); return self
    def __exit__(self, *a): self.close()
    def _check(self):
        if not self._cur: raise RuntimeError("Store not connected")

    def insert_listing(self, listing):
        self._check(); rid = str(uuid.uuid4())
        try:
            self._cur.execute(
                "INSERT OR IGNORE INTO raw_listings "
                "(id, source, source_listing_id, title, price_cad, url, "
                "location, description, date_posted, image_urls, scraped_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (rid, listing.source.value, listing.source_listing_id,
                 listing.title, listing.price_cad, listing.url,
                 listing.location or "", listing.description or "",
                 listing.date_posted, json.dumps(listing.image_urls),
                 listing.scraped_at))
            self._conn.commit(); return self._cur.rowcount > 0
        except sqlite3.IntegrityError: return False

    def insert_sold_comp(self, comp):
        self._check(); cid = str(uuid.uuid4())
        # Resolve the canonical product at write time so lookups can use an
        # exact match rather than a substring match (see get_sold_comps). Callers
        # that already know the product can pass it explicitly.
        product_key = comp.product_key or derive_product_key(comp.title)
        try:
            self._cur.execute(
                "INSERT OR IGNORE INTO sold_comps "
                "(id, source, source_listing_id, title, sold_price_cad, "
                "sold_date, condition, url, location, scraped_at, product_key, inferred) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (cid, comp.source.value, comp.source_listing_id,
                 comp.title, comp.sold_price_cad, comp.sold_date,
                 comp.condition, comp.url or "", comp.location or "",
                 comp.scraped_at, product_key, 1 if comp.inferred else 0))
            self._conn.commit(); return self._cur.rowcount > 0
        except sqlite3.IntegrityError: return False

    def get_sold_comps(self, standard_name=None, max_days_old=90, limit=100,
                       include_inferred=False):
        """Return sold comps for a canonical product.

        Matches on the resolved ``product_key`` — an **exact** match. Matching on
        ``title`` with ``LIKE '%name%'`` would let a base model absorb its own
        variants, because "RTX 3060 Ti" contains the substring "RTX 3060". That
        silently inflates the base model's comp median (and therefore its FMV).

        ``include_inferred`` defaults to **False**, so comps inferred from a
        listing's disappearance (see ``config.LifecycleConfig``) are excluded
        unless the caller asks for them. Real observed sales and inferred ones
        must never be silently mixed.
        """
        self._check()
        clauses = []; params = []
        if standard_name:
            clauses.append("product_key = ?"); params.append(standard_name)
        if max_days_old:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_days_old)).isoformat()
            clauses.append("sold_date >= ?"); params.append(cutoff)
        if not include_inferred:
            clauses.append("inferred = 0")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        self._cur.execute(
            f"SELECT * FROM sold_comps {where} ORDER BY sold_date DESC LIMIT ?",
            params + [limit])
        return [SoldComp(
            source=Platform(row["source"]),
            source_listing_id=row["source_listing_id"],
            title=row["title"], sold_price_cad=row["sold_price_cad"],
            sold_date=row["sold_date"], condition=row["condition"],
            url=row["url"] or "", location=row["location"] or "",
            product_key=row["product_key"],
            inferred=bool(row["inferred"]),
            scraped_at=row["scraped_at"] or "") for row in self._cur.fetchall()]

    # ── Listing lifecycle ───────────────────────────────────────────────────

    def observe_listing(self, listing, observed_at: Optional[str] = None,
                        skip_reason: Optional[str] = None) -> bool:
        """Record one sighting of *listing* on this scrape cycle.

        Returns True if the listing was newly discovered, False if it was already
        known. Each sighting refreshes ``last_seen_at`` and resets the
        consecutive-miss counter; that is what makes disappearance detectable.
        A listing not seen for ``misses_before_gone`` cycles is retired by
        :meth:`close_observation_cycle`.

        ``skip_reason`` records why the pipeline declined to value the listing
        (see ``IngestionPipeline._pre_filter`` and ``assess_listing``). Rejected
        listings are retained for audit but excluded from
        :meth:`get_gone_listings`, so an accessory can never masquerade as a sale.
        """
        self._check()
        now = observed_at or datetime.now(timezone.utc).isoformat()
        row = self._cur.execute(
            "SELECT id FROM raw_listings WHERE source = ? AND source_listing_id = ?",
            (listing.source.value, listing.source_listing_id)).fetchone()
        if row is None:
            self._cur.execute(
                "INSERT INTO raw_listings "
                "(id, source, source_listing_id, title, price_cad, url, "
                "location, description, date_posted, image_urls, scraped_at, "
                "first_seen_at, last_seen_at, miss_count, status, skip_reason) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,'active',?)",
                (str(uuid.uuid4()), listing.source.value,
                 listing.source_listing_id, listing.title, listing.price_cad,
                 listing.url, listing.location or "", listing.description or "",
                 listing.date_posted, json.dumps(listing.image_urls),
                 listing.scraped_at, now, now, skip_reason))
            self._conn.commit()
            return True

        self._cur.execute(
            "UPDATE raw_listings SET last_seen_at = ?, miss_count = 0, "
            "status = 'active', gone_at = NULL, title = ?, price_cad = ?, "
            "description = ?, skip_reason = ? WHERE id = ?",
            (now, listing.title, listing.price_cad,
             listing.description or "", skip_reason, row["id"]))
        self._conn.commit()
        return False

    def close_observation_cycle(self, seen_keys, misses_before_gone=None,
                                sources=None) -> dict:
        """Advance lifecycle state after a full scrape pass.

        ``seen_keys`` is the set of ``(source, source_listing_id)`` pairs observed
        this cycle. Every *other* active listing has its consecutive-miss counter
        incremented; once the counter reaches ``misses_before_gone`` the listing
        moves to ``status='gone'`` and is stamped with ``gone_at``.

        ``sources`` should list the sources actually scraped this cycle. Only
        listings from those sources are considered — otherwise a source that
        happened to return nothing would retire its whole inventory as "sold".

        Returns ``{"missed": n, "gone": n}``.
        """
        self._check()
        threshold = (misses_before_gone if misses_before_gone is not None
                     else CFG.lifecycle.misses_before_gone)
        now = datetime.now(timezone.utc).isoformat()
        seen = set(seen_keys or ())

        sql = "SELECT id, source, source_listing_id, miss_count FROM raw_listings " \
              "WHERE status = 'active'"
        params: list = []
        if sources:
            srcs = [s.value if hasattr(s, "value") else str(s) for s in sources]
            sql += " AND source IN (%s)" % ",".join("?" for _ in srcs)
            params = srcs
        rows = self._cur.execute(sql, params).fetchall()

        missed = gone = 0
        for row in rows:
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
        self._conn.commit()
        return {"missed": missed, "gone": gone}

    def get_gone_listings(self, since_days=None, limit=1000):
        """Return listings that disappeared after being observed.

        This is the raw material for inferred comps: a listing that was on offer
        and then vanished most likely sold. ``last_seen_at`` is the proxy sale
        date, and the last observed ``price_cad`` the proxy sale price.

        Listings the pipeline *rejected* (``skip_reason`` set — accessories, lots,
        junk prices) are excluded. Their titles frequently name a product they
        merely fit, so letting them through would price a $40 power cable as an
        RTX 4090 sale.
        """
        self._check()
        clauses = ["status = 'gone'", "skip_reason IS NULL"]
        params: list = []
        if since_days:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()
            clauses.append("gone_at >= ?")
            params.append(cutoff)
        where = "WHERE " + " AND ".join(clauses)
        return self._cur.execute(
            f"SELECT * FROM raw_listings {where} ORDER BY gone_at DESC LIMIT ?",
            params + [limit]).fetchall()

    def count_comps_by_origin(self) -> dict:
        """Return ``{"real": n, "inferred": n, "synthetic": n}`` comp counts.

        Synthetic rows are the ``baseline_`` seed rows (see
        ``app/tests/fixtures/baseline_comps.py``); they exist only in
        ``--seed-demo`` runs and must never be mistaken for evidence.
        """
        self._check()
        row = self._cur.execute(
            "SELECT "
            "  SUM(CASE WHEN source_listing_id LIKE 'baseline_%' THEN 1 ELSE 0 END) AS synthetic, "
            "  SUM(CASE WHEN source_listing_id NOT LIKE 'baseline_%' AND inferred = 0 THEN 1 ELSE 0 END) AS real, "
            "  SUM(CASE WHEN source_listing_id NOT LIKE 'baseline_%' AND inferred = 1 THEN 1 ELSE 0 END) AS inferred "
            "FROM sold_comps"
        ).fetchone()
        return {k: int(row[k] or 0) for k in ("real", "inferred", "synthetic")}

    # ── Retail price reference ──────────────────────────────────────────────

    def insert_retail_price(self, retail: RetailPrice) -> bool:
        """Upsert a new-retail price for ``(product_key, retailer)``.

        Prices move, so unlike comps this is an upsert rather than an ignore: a
        re-scrape refreshes the price and timestamp instead of being dropped.

        Returns True if a row was written.
        """
        self._check()
        self._cur.execute(
            "INSERT INTO retail_prices "
            "(id, product_key, price_cad, retailer, url, observed_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(product_key, retailer) DO UPDATE SET "
            "price_cad = excluded.price_cad, url = excluded.url, "
            "observed_at = excluded.observed_at",
            (str(uuid.uuid4()), retail.product_key, retail.price_cad,
             retail.retailer or "", retail.url or "", retail.observed_at))
        self._conn.commit()
        return self._cur.rowcount > 0

    def get_retail_prices(self, product_key=None, max_age_days=None,
                          limit=50) -> list[RetailPrice]:
        """Return retail quotes, cheapest first.

        Cheapest-first is deliberate: the relevant ceiling for "should I buy this
        used one?" is the *best new alternative*, not the most expensive.
        """
        self._check()
        clauses = []
        params: list = []
        if product_key:
            clauses.append("product_key = ?")
            params.append(product_key)
        if max_age_days:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=max_age_days)).isoformat()
            clauses.append("observed_at >= ?")
            params.append(cutoff)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._cur.execute(
            f"SELECT * FROM retail_prices {where} "
            f"ORDER BY price_cad ASC LIMIT ?", params + [limit]).fetchall()
        return [RetailPrice(
            product_key=r["product_key"], price_cad=r["price_cad"],
            retailer=r["retailer"] or "", url=r["url"] or "",
            observed_at=r["observed_at"]) for r in rows]

    def get_retail_price(self, product_key, max_age_days=None
                         ) -> Optional[RetailPrice]:
        """Return the cheapest fresh retail quote for *product_key*, or None."""
        quotes = self.get_retail_prices(product_key=product_key,
                                        max_age_days=max_age_days, limit=1)
        return quotes[0] if quotes else None

    def insert_decision(self, output):
        self._check(); did = str(uuid.uuid4())
        ds = output.deal_score
        # Single shared gate — see app/valuation/gate.py. This used to be a second
        # inline copy (hardcoded 50 / 15) that omitted the comp-count condition.
        alert = 1 if passes_alert_gate(output) else 0
        self._cur.execute(
            "INSERT INTO valuation_decisions "
            "(id, listing_id, canonical_product, fmv_cad, erv_cad, "
            "deal_score, confidence_level, alert_sent, decision_payload) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (did, output.raw_listing.source_listing_id,
             output.canonical_product.standard_name, ds.fmv_cad, ds.erv_cad,
             ds.score, ds.confidence_level.value, alert,
             output.model_dump_json(indent=2)))
        self._conn.commit(); return did

    def record_source_success(self, source):
        self._check(); n = datetime.now(timezone.utc).isoformat()
        self._cur.execute(
            "INSERT INTO source_health (id,source,last_success_at,consecutive_failures) "
            "VALUES (?,?,?,0) ON CONFLICT(source) DO UPDATE SET "
            "last_success_at=excluded.last_success_at, consecutive_failures=0",
            (str(uuid.uuid4()), source.value, n))
        self._conn.commit()

    def record_source_failure(self, source):
        self._check(); n = datetime.now(timezone.utc).isoformat()
        self._cur.execute(
            "INSERT INTO source_health (id,source,last_failure_at,consecutive_failures) "
            "VALUES (?,?,?,1) ON CONFLICT(source) DO UPDATE SET "
            "last_failure_at=excluded.last_failure_at, consecutive_failures=consecutive_failures+1",
            (str(uuid.uuid4()), source.value, n))
        self._conn.commit()