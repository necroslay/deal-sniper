"""Full pipeline: (optionally) seed comps, scrape live, process, break down.

By default the run is honest: it uses only real comps already in the database
and it **does not** delete the database. Two opt-in flags change that:

* ``--fresh``      delete and rebuild the database (self-contained demo run)
* ``--seed-demo``  insert synthetic ``BASELINE_COMPS`` so a demo produces output
                   without real sold data. Never use this to draw conclusions.
"""
import sys, pathlib, logging, argparse
sys.path.insert(0, str(pathlib.Path("c:/Users/nelso/Projects/deal-sniper")))
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from app.models.schema import Platform, SoldComp
from app.pipeline.ingest import IngestionPipeline
from app.db.store import Store
from app.config import CFG
from app.pipeline.lifecycle_cycle import observe_cycle
from datetime import datetime, timezone, timedelta
from collections import Counter

DB_PATH = pathlib.Path("c:/Users/nelso/Projects/deal-sniper") / "app" / "resale_engine.db"

parser = argparse.ArgumentParser(description="Run the deal-sniper pipeline.")
parser.add_argument("--fresh", action="store_true",
                    help="Delete the existing database and rebuild from scratch.")
parser.add_argument("--seed-demo", action="store_true",
                    help="Seed synthetic BASELINE_COMPS. Real runs should omit this.")
args = parser.parse_args()

# The database accumulates across runs by default. --fresh makes the run
# self-contained (which is what a throwaway demo wants, and what a real
# accumulating store very much does not).
if args.fresh and DB_PATH.exists():
    DB_PATH.unlink()
    print(">> Removed old database (--fresh)")

store = Store()
store.connect()

# ── Step 1: Seed comps (opt-in only) ─────────────────────────────────────
print("=" * 60)
print("STEP 1: Seeding comps")
print("=" * 60)

if args.seed_demo:
    from app.tests.fixtures.baseline_comps import BASELINE_COMPS
    print("!! --seed-demo: inserting SYNTHETIC baseline comps (NOT real sold data)")
    total_comps = 0
    base = datetime.now(timezone.utc) - timedelta(days=60)
    for product, prices in BASELINE_COMPS.items():
        for i, price in enumerate(prices):
            comp = SoldComp(
                source=Platform.ebay,
                source_listing_id=f"baseline_{product.replace(' ','_')}_{i}",
                title=f"{product} {i}",
                sold_price_cad=float(price),
                sold_date=(base - timedelta(days=i * 3)).isoformat(),
                condition="used_good",
                # Product is already known here; set it explicitly so the comp does
                # not depend on the resolver re-deriving it from the title.
                product_key=product,
            )
            if store.insert_sold_comp(comp):
                total_comps += 1
        print(f"  {product:<20s} {len(prices):>2} comps")
    print(f"Total comps seeded: {total_comps}")
else:
    print(">> Not seeding synthetic comps.")
    print("   Using real sold comps already in the database (none on a fresh DB).")
    print("   Pass --seed-demo for a synthetic demo dataset.")

# ── Step 2: Scrape live listings from every working source ───────────────
print("\n" + "=" * 60)
print("STEP 2: Scraping live listings (Kijiji + Craigslist)")
print("=" * 60)

from app.adapters.sources import get_adapter

listings = []

# Kijiji — free JSON/GraphQL API, no browser required. "ALL" sweeps the
# configured GPU search terms and unions the results.
kijiji = get_adapter(Platform.kijiji)
kijiji_listings = kijiji.search("ALL", max_pages=5, high_end_only=False)
print(f"  Kijiji:        {len(kijiji_listings)} listings")
listings.extend(kijiji_listings)

# Craigslist — search pages are JavaScript-rendered, so this uses a headless
# browser (Playwright) under the hood.
craigslist = get_adapter(Platform.craigslist)
craigslist_listings = craigslist.search("graphics card", max_pages=2, category="cpd")
print(f"  Craigslist:    {len(craigslist_listings)} listings")
listings.extend(craigslist_listings)

# De-duplicate across sources by URL
seen_urls: set = set()
unique_listings = []
for raw in listings:
    if raw.url and raw.url not in seen_urls:
        seen_urls.add(raw.url)
        unique_listings.append(raw)
listings = unique_listings
print(f"  Total unique:  {len(listings)} listings")

# ── Step 3: Run pipeline on all listings ──────────────────────────────────
print("\n" + "=" * 60)
print("STEP 3: Processing through pipeline")
print("=" * 60)

pipeline = IngestionPipeline(store=store)
results = []
for i, raw in enumerate(listings):
    output = pipeline.process(raw_listing=raw, persist=True)
    results.append(output)
    if (i + 1) % 50 == 0:
        print(f"  Processed {i+1}/{len(listings)}...")

# ── Step 3b: Advance the listing lifecycle ───────────────────────────────
# pipeline.process() recorded a sighting for every listing above. Now close the
# cycle: anything from a source we *did* scrape that did not reappear earns a
# miss, and enough consecutive misses retires it (presumed sold). Only sources
# that actually returned listings are considered, so a source that errored or
# returned nothing can't retire its whole inventory.
scraped_sources = {l.source for l in listings}
if scraped_sources:
    cycle = observe_cycle(store, listings, sources=scraped_sources)
    print(f"  Lifecycle: {cycle['missed']} missed this cycle, "
          f"{cycle['gone']} newly retired")
    print(f"  Inferred comps: {cycle['inferred_built']} built, "
          f"{cycle['inferred_added']} new "
          f"(haircut {CFG.lifecycle.inferred_comp_haircut_pct:.0%})")
    if not CFG.lifecycle.use_inferred_comps:
        print("  (inferred comps excluded from valuation — "
              "set lifecycle.use_inferred_comps to enable)")
else:
    print("  Lifecycle: skipped (no listings scraped this run)")

# ── Step 4: Breakdown ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("BREAKDOWN")
print("=" * 60)

# Separate skipped / valid results
skipped = [r for r in results if r.deal_score.risk_flags and any(
    f in str(r.deal_score.risk_flags) for f in ("price_too_low", "price_too_high",
    "title_too_short", "contact_for_price", "trade_post", "service_not_product")
)]
valid = [r for r in results if r not in skipped]
print(f"Total inputs: {len(results)}  |  Skipped: {len(skipped)}  |  Valid: {len(valid)}")

# By resolved product (exclude skipped)
product_counts = Counter(r.canonical_product.standard_name for r in valid)
unknown_product_count = sum(1 for r in valid if r.canonical_product.category == "unknown")
print(f"\n>> Products found ({len(product_counts)} unique, {unknown_product_count} unknown):")
for product, count in product_counts.most_common(20):
    print(f"  {product:<30s} {count:>3} listings")

# Products missing from the registry (Tier 3 fallbacks)
fallback_products = [r for r in valid if r.canonical_product.resolved_from == r.canonical_product.standard_name]
if fallback_products:
    print(f"\n>> Unrecognized products (not in registry — FMV=0):")
    fallback_counts = Counter(r.canonical_product.standard_name for r in fallback_products)
    for product, count in fallback_counts.most_common(15):
        print(f"  {product[:45]:<47s} {count:>2}")

# By condition
condition_counts = Counter(r.fair_value.condition for r in valid)
print(f"\n>> Condition breakdown:")
for cond, count in condition_counts.most_common():
    pct = count / len(valid) * 100 if valid else 0
    print(f"  {cond:<25s} {count:>3} listings ({pct:4.0f}%)")

# By confidence level
confidence_counts = Counter(r.deal_score.confidence_level.value for r in valid)
print(f"\n>> Confidence levels:")
for level, count in confidence_counts.most_common():
    pct = count / len(valid) * 100 if valid else 0
    print(f"  {level:<25s} {count:>3} ({pct:4.0f}%)")

# Why alert gate is failing
alertable = [r for r in valid if pipeline.should_alert(r)]
near_miss = [r for r in valid if r.deal_score.confidence_level.value in ("ALERT_IMMEDIATE", "ALERT_CAUTION")
             and not pipeline.should_alert(r)]
print(f"\n!! Deals passing alert gate: {len(alertable)}")
if near_miss:
    print(f"   Near-misses (ALERT_CAUTION/IMMEDIATE but gate rejected): {len(near_miss)}")
    for r in near_miss[:5]:
        profit = r.deal_score.expected_profit_cad
        margin = r.deal_score.margin_pct
        comps = r.fair_value.comps_used
        print(f"     {r.raw_listing.title[:40]:<42s} "
              f"profit=${profit:>+.0f} margin={margin:.0f}% comps={comps} "
              f"score={r.deal_score.score:.0f}")

for r in alertable:
    print(f"  {r.raw_listing.title[:50]:<52s} | "
          f"${r.raw_listing.price_cad:>5,.0f} | "
          f"FMV=${r.fair_value.fmv_cad:>5,.0f} | "
          f"Profit=${r.deal_score.expected_profit_cad:>+5,.0f} | "
          f"Score={r.deal_score.score:>3.0f}")

# Price range (valid listings only)
all_prices = [r.raw_listing.price_cad for r in valid if r.raw_listing.price_cad > 0]
if all_prices:
    print(f"\n$$ Price range (valid listings only):")
    print(f"  Min: ${min(all_prices):,.0f}")
    print(f"  Max: ${max(all_prices):,.0f}")
    print(f"  Avg: ${sum(all_prices) / len(all_prices):,.0f}")
    print(f"  Median: ${sorted(all_prices)[len(all_prices)//2]:,.0f}")

# Good deals — show all components for debugging
good_deals = [r for r in valid if r.deal_score.expected_profit_cad > 100]
print(f"\n** Deals with profit > $100: {len(good_deals)}")
for r in sorted(good_deals, key=lambda x: x.deal_score.expected_profit_cad, reverse=True)[:10]:
    margin = r.deal_score.margin_pct
    comps = r.fair_value.comps_used
    conf = r.deal_score.confidence_level.value
    print(f"  {r.raw_listing.title[:45]:<47s} | "
          f"Ask=${r.raw_listing.price_cad:>5,.0f} | "
          f"FMV=${r.fair_value.fmv_cad:>5,.0f} | "
          f"Profit=${r.deal_score.expected_profit_cad:>+5,.0f} | "
          f"Score={r.deal_score.score:>3.0f} | "
          f"Margin={margin:.0f}% | {comps}comps | {conf}")

# DB Stats
print("\n" + "=" * 60)
print("DB STATS")
print("=" * 60)
cur = store._cur
cur.execute("SELECT COUNT(*) FROM raw_listings")
print(f"  Listings: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM valuation_decisions")
print(f"  Decisions: {cur.fetchone()[0]}")
cur.execute("SELECT COUNT(*) FROM sold_comps")
print(f"  Comps: {cur.fetchone()[0]}")

# Also show how many products got FMV > 0
cur.execute("SELECT COUNT(*) FROM valuation_decisions WHERE fmv_cad > 0")
fmv_count = cur.fetchone()[0]
decisions_total = cur.execute("SELECT COUNT(*) FROM valuation_decisions").fetchone()[0]
print(f"  Decisions with FMV>0: {fmv_count} / {decisions_total}")

# ── Coverage: the real bottleneck ────────────────────────────────────────
# A listing with no comps for its exact product is suppressed no matter how good
# the deal looks, so comp coverage — not scraper throughput — bounds this system.
print("\n" + "=" * 60)
print("COVERAGE (the binding constraint)")
print("=" * 60)
coverage_pct = (fmv_count / decisions_total * 100) if decisions_total else 0.0
print(f"  FMV coverage: {fmv_count} / {decisions_total} decisions ({coverage_pct:.0f}%)")

# How that coverage was achieved. "retail" means valued from a new-retail
# reference (FMV = retail × condition multiplier) rather than observed sales —
# weaker evidence, and never allowed above ALERT_CAUTION.
basis_counts: dict = {}
for r in results:
    basis_counts[r.fair_value.basis] = basis_counts.get(r.fair_value.basis, 0) + 1
print("  Basis breakdown:  " +
      "  ".join(f"{k}={v}" for k, v in sorted(basis_counts.items())))

retail_products = cur.execute(
    "SELECT COUNT(DISTINCT product_key) AS n FROM retail_prices").fetchone()["n"]
print(f"  Retail references: {retail_products} product(s) "
      f"(import with: python -m app.tools.import_retail_prices --file <csv|json>)")

# Comp origins: synthetic rows are created only by --seed-demo; inferred rows
# come from the listing lifecycle and are excluded from valuation unless enabled.
origin = store.count_comps_by_origin()
print(f"  Comps by origin:  real={origin['real']}  "
      f"inferred={origin['inferred']}  synthetic={origin['synthetic']}")
if origin["real"] == 0:
    print("  !! No real sold comps ingested — valuations rest on "
          "synthetic and/or inferred data only.")

# Listing lifecycle: how much inventory has been retired (presumed sold).
listing_states = {r["status"]: r["n"] for r in cur.execute(
    "SELECT status, COUNT(*) AS n FROM raw_listings GROUP BY status").fetchall()}
print("  Listings by status:  " +
      "  ".join(f"{k}={v}" for k, v in sorted(listing_states.items())))

# Comps per product, with a marker where coverage is below the gate.
print("\n  Comps per product (top 15):")
for product, n in cur.execute(
    "SELECT product_key, COUNT(*) AS n FROM sold_comps "
    "GROUP BY product_key ORDER BY n DESC LIMIT 15"
).fetchall():
    flag = "" if n >= 5 else "   <- below the 5-comp gate"
    print(f"    {str(product):<30s} {n:>3}{flag}")

store.close()
print("\nDONE")