"""Full pipeline: seed comps, scrape 200, process, break down."""
import sys, pathlib, logging
sys.path.insert(0, str(pathlib.Path("c:/Users/nelso/Projects/deal-sniper")))
logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")

from app.models.schema import Platform, SoldComp
from app.pipeline.ingest import IngestionPipeline
from app.db.store import Store
from datetime import datetime, timezone, timedelta
from collections import Counter

DB_PATH = pathlib.Path("c:/Users/nelso/Projects/deal-sniper") / "app" / "resale_engine.db"

# Remove old database so each run starts fresh
if DB_PATH.exists():
    DB_PATH.unlink()
    print(">> Removed old database")

store = Store()
store.connect()

# ── Step 1: Seed baseline comps ──────────────────────────────────────────
print("=" * 60)
print("STEP 1: Seeding baseline comps")
print("=" * 60)

BASELINE_COMPS = {
    "RTX 4090": [2200, 2350, 2400, 2450, 2500, 2550, 2600, 2650, 2700, 2750, 2800, 2850, 2900, 3000, 3200],
    "RTX 4080 Super": [1500, 1550, 1600, 1650, 1700, 1750, 1800, 1850, 1900, 1950],
    "RTX 4080": [1200, 1250, 1300, 1350, 1400, 1450, 1500, 1550, 1600, 1650],
    "RTX 4070 Ti Super": [900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350],
    "RTX 4070": [700, 750, 800, 850, 900, 950, 1000, 1050, 1100, 1150],
    "RTX 3080": [450, 480, 500, 520, 540, 560, 580, 600, 620, 650],
    "RTX 3080 Ti": [550, 580, 600, 620, 640, 660, 680, 700, 720, 750],
    "RTX 3070": [350, 380, 400, 420, 440, 460, 480, 500, 520, 540],
    "RTX 3070 Ti": [400, 430, 450, 470, 490, 510, 530, 550, 570, 590],
    "RTX 3060 Ti": [300, 320, 340, 360, 380, 400, 420, 440, 460, 480],
    "RTX 3060": [200, 220, 240, 260, 280, 300, 320, 340, 360, 380],
    "RTX 2070": [180, 200, 220, 240, 260, 280, 300, 320, 340, 360],
    "GTX 1660 Super": [120, 130, 140, 150, 160, 170, 180, 190, 200, 210],
    "GTX 1660": [100, 110, 120, 130, 140, 150, 160, 170, 180, 190],
    "GTX 1070": [100, 110, 120, 130, 140, 150, 160, 170, 180, 190],
    "GTX 1080": [130, 140, 150, 160, 170, 180, 190, 200, 210, 220],
    "GTX 1080 Ti": [180, 200, 220, 240, 260, 280, 300, 320, 340, 360],
    "RX 7900 XTX": [900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350],
    "RX 7900 XT": [750, 800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200],
    "RX 7800 XT": [550, 580, 600, 620, 640, 660, 680, 700, 720, 750],
    "RX 6800 XT": [400, 430, 450, 470, 490, 510, 530, 550, 570, 590],
    "RX 6800": [350, 370, 390, 410, 430, 450, 470, 490, 510, 530],
    "RX 580": [60, 70, 80, 90, 100, 110, 120, 130, 140, 150],
    "PlayStation 5": [350, 370, 380, 400, 420, 430, 440, 450, 460, 480],
    "PS5 Pro": [600, 650, 700, 750, 800, 850, 900, 950, 1000, 1050],
    "Nintendo Switch OLED": [250, 260, 270, 280, 290, 300, 310, 320, 330, 340],
}

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
            # We already know the canonical product here; set it explicitly so the
            # comp does not depend on the resolver re-deriving it from the title.
            product_key=product,
        )
        if store.insert_sold_comp(comp):
            total_comps += 1
    print(f"  {product:<20s} {len(prices):>2} comps")
print(f"Total comps seeded: {total_comps}")

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
print(f"  Decisions with FMV>0: {fmv_count} / {len(results)}")

store.close()
print("\nDONE")