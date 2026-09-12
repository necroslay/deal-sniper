"""SQLite persistence store for the valuation engine."""
from __future__ import annotations
import json, logging, re, sqlite3, uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from app.engines.resolver import resolve_product
from app.models.schema import FeedbackEvent, Platform, RawListing, SoldComp, ValuationOutput
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
    UNIQUE(source, source_listing_id));
CREATE INDEX IF NOT EXISTS idx_comps_name ON sold_comps(title);"""

class Store:
    def __init__(self, db=DB):
        self._db = db; self._conn = None; self._cur = None
    def connect(self):
        self._conn = sqlite3.connect(self._db)
        self._conn.row_factory = sqlite3.Row
        self._cur = self._conn.cursor()
        self._cur.executescript(SQL); self._conn.commit()
        self._migrate()

    def _migrate(self):
        """Bring an older database up to date and backfill derived columns.

        ``product_key`` was added to ``sold_comps`` to fix comp-lookup
        contamination. Databases created before that change need the column
        added and their existing rows resolved, so that exact-match lookups can
        still find them.
        """
        cols = {r["name"] for r in self._cur.execute("PRAGMA table_info(sold_comps)")}
        if "product_key" not in cols:
            self._cur.execute("ALTER TABLE sold_comps ADD COLUMN product_key TEXT")

        # Created here rather than in SQL: on a legacy database the column above
        # does not exist until this migration runs, and CREATE INDEX on a missing
        # column would fail.
        self._cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_comps_product_key "
            "ON sold_comps(product_key)"
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
                "sold_date, condition, url, location, scraped_at, product_key) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (cid, comp.source.value, comp.source_listing_id,
                 comp.title, comp.sold_price_cad, comp.sold_date,
                 comp.condition, comp.url or "", comp.location or "",
                 comp.scraped_at, product_key))
            self._conn.commit(); return self._cur.rowcount > 0
        except sqlite3.IntegrityError: return False

    def get_sold_comps(self, standard_name=None, max_days_old=90, limit=100):
        """Return sold comps for a canonical product.

        Matches on the resolved ``product_key`` — an **exact** match. Matching on
        ``title`` with ``LIKE '%name%'`` would let a base model absorb its own
        variants, because "RTX 3060 Ti" contains the substring "RTX 3060". That
        silently inflates the base model's comp median (and therefore its FMV).
        """
        self._check()
        clauses = []; params = []
        if standard_name:
            clauses.append("product_key = ?"); params.append(standard_name)
        if max_days_old:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=max_days_old)).isoformat()
            clauses.append("sold_date >= ?"); params.append(cutoff)
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
            scraped_at=row["scraped_at"] or "") for row in self._cur.fetchall()]

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