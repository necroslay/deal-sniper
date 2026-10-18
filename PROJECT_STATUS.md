# Project Status — Deal Sniper

> **Purpose:** an honest map of what works, what doesn't, and how to prove it —
> written so "what does this do today?" can be answered without overstating
> anything. Companion to [`reports/README.md`](reports/README.md) (ML results) and
> [`archive/engine-docs/`](archive/engine-docs/) (the archived valuation engine).

---

## 1. One paragraph

Scrapes second-hand computer parts from **Kijiji (Canada-wide)** and
**Craigslist (GTA)** into SQLite, resolves each title to a canonical product,
flags junk, and shows everything in a Streamlit dashboard. On top sits an
**opt-in ML layer** — a resolver, a listing guard, and a quantile price model —
attached at exactly one seam and measured by an evaluation harness that **fails
the build** on regression. The deterministic rules remain authoritative; the
models only fill the gaps the rules abstain on.

---

## 2. Component status

Legend: ✅ works · ⚠️ works with caveats · 🟡 partial · ❌ not built

| Component | Status | Evidence / caveat |
|---|---|---|
| Kijiji scraper | ✅ | **Canada-wide** now (`region`/`location_id` config). Live-verified: 40 listings across 24 locations. ~1,430 available for `graphics-card` vs 602 for GTA |
| Craigslist scraper | ⚠️ | **GTA only**, Playwright-driven, not exercised this session |
| SQLite store + lifecycle | ✅ | Store-once-forever, miss-counter retirement, coverage-aware |
| Product resolver (rules) | ✅ | Regex waterfall; bare-numeric alias bug **fixed** |
| Listing guard (rules) | ✅ | Accessory / lot / platform / component-in-host |
| Enrichment seam (`engines/enrich.py`) | ✅ | Single integration point; ML-off reproduces pre-ML behaviour exactly (tested) |
| ML resolver (TF-IDF) | ✅ | Beats the rules on the labeled set: 0.871 vs 0.844 macro F1 |
| ML guard (rule-informed) | ✅ | Hybrid beats the rules: 0.849 vs 0.790 macro F1 |
| Price model (LightGBM quantile) | ⚠️ | Works end-to-end but **loses to the simple baseline** (MAE $404 vs $284) |
| Per-product quantile baseline | ✅ | The best price predictor available at current data volume |
| New-retail ceiling | 🟡 | Wired and tested in isolation; **never exercised on real data** (no reference file) |
| Evaluation harness + CI gate | ✅ | 3 gates (resolver, guard, price), all green |
| Re-enrichment command | ✅ | `python -m app.re_resolve`; repaired 61 stale rows |
| Embedding model (Phase 1b) | ❌ | Registered stub only |
| Craigslist multi-city sweep | ❌ | Not built |
| Scheduled / daily scraping | ❌ | Not built — this is what a time axis needs |
| Serving (FastAPI) | ❌ | Not built |
| Experiment tracking / drift (MLflow) | ❌ | Not built |

---

## 3. Verified numbers

```text
tests                        233 passed  (was 77 at the start of this session)

resolver (test n=30)         baseline  acc 0.733 / macro F1 0.844
                             tfidf     acc 0.867 / macro F1 0.871   <- model wins
                             hybrid    acc 0.733 / macro F1 0.844   <- conservative

guard (test n=35)            baseline  acc 0.829 / macro F1 0.790
                             model     acc 0.829 / macro F1 0.816
                             hybrid    acc 0.857 / macro F1 0.849   <- hybrid wins

price (train 200 -> test 67) product-quantile-baseline  MAE $284  coverage 0.30
                             lightgbm-quantile          MAE $404  coverage 0.36

dataset (classifier)         115 hand-labeled listings in data/labels/deals.csv
                             (68 keep / 47 reject), DB-independent
                             resolver: 85 train / 30 evaluated
                             guard:    80 train / 35 evaluated

database                     1,577 listings across 226 locations
                             (Canada-wide run: kijiji 1,522 incl. 15 gone,
                             craigslist 55)
                             resolved 502 · unresolved 1,075 · priceable 267
                             flagged 225 (component-in-host 166, accessory 35,
                             lot 22, platform-accessory 2)
                             re-resolve: 61 rows repaired (54 phantom iPhones),
                             now idempotent — 0 remaining
```

> **Two different 115s — do not conflate them.** The classifier's hand-labeled set
> is **115 rows** (`data/labels/deals.csv`, self-contained, unaffected by
> scraping). The price model's *usable row count* was **coincidentally also 115**
> before the Canada-wide harvest, and is now **267**. They are unrelated numbers
> with no shared provenance.

---

## 4. Bugs found and fixed this session

1. **Bare-numeric registry aliases.** `iPhone 15` / `iPhone 14` carried the
   aliases `"15"` and `"14"`, so `Dell XPS 15`, `iMac 2015` and
   `ThinkCentre M715q` all resolved to an iPhone. Fixed, plus a registry
   invariant test (no numeric alias shorter than four digits) and the
   `re_resolve` command to repair rows already stored.
2. **Macro F1 averaged phantom classes.** Classes with support 0 — predicted but
   never true — were scored as hard zeros, dragging the resolver baseline from
   0.776 to 0.597.
3. **The split hid whole classes from training.** A hash-only split put four
   classes entirely in val/test, where no model can predict them. The split is
   now stratified by label and guarantees every label a training group; that
   alone took the resolver model from 0.484 to 0.871.
4. **Kijiji's location was hardcoded**, so `config.yaml`'s `url_template` was
   silently ignored. Now built from `region` + `location_id`.
5. **A Canada-wide URL trap.** `/b-canada/{term}/k0` (no location segment) is not
   a scoped search — it returns cars and jewellery for a GPU query while
   reporting 4,148 hits. The code always emits the `l` segment; a test pins it.
6. **Stale enrichment in the database.** Fix #1 only affects *new* scrapes, so 54
   rows kept their phantom iPhone key until re-enriched. Now repaired.
7. **Dead code removed:** `PriceRow.posted`, `retail.cap_band` (superseded by the
   per-row `cap_at_retail`).

---

## 5. Known limitations — say these before anyone else finds them

**Data volume is the binding constraint, everywhere.** Three separate results
landed on the same cause:

- The resolver model needed a *split fix*, not a better algorithm, to beat the
  rules.
- The price model **loses to a per-product empirical quantile** ($404 vs $284
  MAE) on 200 training rows. Model capacity is no longer the issue — sweeping
  7 → 31 leaves spans only $398–$408 — so this is not a tuning problem. The
  boosted model has no signal beyond the product-level price.
- Every headline figure rests on 30–35 test rows and some classes carry a single
  example. These demonstrate the **method**, not production accuracy.

**Volume grew faster than *usable* volume.** The Canada-wide run took the corpus
from 625 to **1,577 rows across 226 locations**, but only **502 resolve (32%)**
and only **267 are priceable**. A broad-term sweep at national scale drags in a
long tail: trading cards, camcorders, lenses, "BUYING" wanted ads, whole PCs and
laptops, and untracked hardware (GTX 1650/960, RTX 2080, RX Vega 64, PS4). Better
search terms and the missing "not a computer part" verdict are the levers here,
not more pages.

**The price band is miscalibrated out of time.** Its P25–P75 interval covers only
**30%** of the newer listings against a 50% target. That is a finding, not a bug:
a random split would have hidden it. But it means the band is currently too
narrow to be trustworthy on new listings.

**"Not a computer part" is not a guard category.** Services (a "10,000 Flyers"
printing ad appears 11 times), books, monitors and cameras resolve to their own
title, carry **no flag**, and show up in the viewer as unresolved rows. The guard
knows accessory / lot / platform / component-in-host; it has no "out of scope"
verdict.

**A single scrape only sees survivors.** Listings that sold are invisible, so
older cohorts are biased toward ads that *didn't* sell. Product mix also shifts
with posting age (GPU share 21% → 25% → 54% → 32% across age buckets), so any
age→price effect is part mix, part survivorship.

**No repeated-observation axis.** `date_posted` gives a real time axis (used for
the temporal split — it spans 2013–2026), but every row's `scraped_at` falls on a
**single day**. Price cuts and time-to-sell are therefore invisible, and
`status='gone'` is still inferred from miss counters rather than observed sales.
A second run on a later date is what changes this.

**Other gaps:**
- Craigslist is GTA-only (~55 rows) and a national sweep is not built.
- The retail ceiling has never been exercised against real data.
- Craigslist's year-less `M/D` dates are a weaker time signal than Kijiji's
  absolute `activationDate`.
- The database is a **scratch artifact** (gitignored): re-scraping changes the
  numbers, so the committed metrics are a regression baseline for *that* data,
  not a claim about the market.
- Kijiji ads cross-posted across cities are stored as distinct listings
  (correctly), so raw row counts overstate independent observations — price
  features should be deduped by normalised title before that matters.

---

## 6. How to verify

```bash
cd app && python -m pytest -q                   # 233 passed

# evaluation gates (fail the build on regression)
python -m app.eval.harness --task resolver --check-baseline
python -m app.eval.harness --task guard --check-baseline
python -m app.eval.price_harness --check-baseline

# repair stale enrichment after a resolver change
python -m app.re_resolve --dry-run
python -m app.re_resolve

# harvest (then re-run the gates to re-baseline)
python -m app.scrape --source kijiji
```

---

## 7. Next steps, in order

1. ~~Run the Canada-wide harvest~~ **DONE** — 625 → 1,577 rows, 226 locations.
   The gates have been re-baselined to this data.
2. **Schedule a daily scrape** — now the highest-value next step. It is the only
   way to add a repeated-observation axis, which unlocks price-cut detection and
   time-to-sell, and reduces survivorship bias.
3. **Tighten the search terms / add a "not a computer part" verdict** — only 32%
   of the new rows resolve. Cheaper than scraping more pages.
4. **Fill in `data/retail_prices.csv`** (format in `.sample.csv`) to actually
   exercise the ceiling.
5. **Add a Craigslist subdomain sweep** to take it national.
6. Only after that: embeddings (Phase 1b), serving (FastAPI), MLOps (MLflow,
   drift, Docker).
