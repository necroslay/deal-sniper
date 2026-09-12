# Worked Example — One Listing, End to End

> **Every number in this document was produced by running the code against the
> real database**, not hand-waved. Reproduce it with the commands in §9.
>
> **Last verified:** 2026-09-11 against `app/resale_engine.db`
> (802 listings, 869 decisions, 265 comps).

This traces a single real Kijiji listing through all ten pipeline stages, verifies
the arithmetic by hand, and then — following the data — exposes **two defects that
the trace revealed**. The second half of this document is the more valuable half.

---

## 1. The listing

Pulled straight from `raw_listings` (a genuine scraped listing):

| Field | Value |
|---|---|
| `source` | `kijiji` |
| `source_listing_id` | `1743284881` |
| `title` | `RTX 3060 Gigabyte - used` |
| `price_cad` | `180.0` |
| `location` | `Oshawa / Durham Region` |
| `date_posted` | `2026-09-10T21:58:17.000Z` |

A clean, unambiguous listing: a real GPU, a clear model name, a stated condition,
a sensible asking price. It's the *easy* case — which is why it's a fair test of
the pipeline's arithmetic before we stress it.

---

## 2. Stages 1–4

### Stage 1–2 — Pre-filter

```python
pipeline._pre_filter(raw)  →  None      # None means "passes, don't skip"
```

Checks that passed: price `$180 ≥ $10`, price `≤ $100,000`, title length ≥ 5,
no "contact for price", not a trade post, not a buying service.

### Stage 3 — Product resolution

```
resolve_product("RTX 3060 Gigabyte - used")
  → standard_name = 'RTX 3060'
    category      = 'gpu'
```

**Tier 1** (regex alias match) hit immediately on the alias `"RTX 3060"`.
The brand word "Gigabyte" is correctly ignored — aliases are matched as
substrings within the title, so surrounding noise doesn't matter.

### Stage 4 — Condition classification

```
classify_condition("RTX 3060 Gigabyte - used", "")
  → grade           = 'used_good'
    confidence      = 1.0
    matched_keyword = 'used'
    signals         = []
```

Matched exactly **one** keyword family — `used` (weight `0.80`) in `used_good`.
Confidence then accumulates:

| Contribution | Amount | Running |
|---|---|---|
| base keyword weight (`used` → 0.80) | +0.80 | 0.80 |
| `DESC_BOOST` — description ≥ 0 chars | +0.00 | 0.80 |
| `DESC_BOOST` — description ≥ 20 chars | +0.10 | 0.90 |
| `DESC_BOOST` — description ≥ 100 chars | +0.15 | 1.05 → **capped 1.0** |
| `match_count >= 2` bonus | not earned (only 1 match) | — |
| `no_description` penalty | not applied (165 chars) | — |

→ `confidence = 1.0`

The description is 165 characters of real listing text:

> "Just upgraded Can deliver depending on the area I keep it in my current gpus box
> with anti static bag but this gpu will not come with a box, only the anti static bag"

**Two things worth noticing here:**

1. `DESC_BOOST` is **cumulative**, not a single best-match tier. A description
   past 100 characters contributes `+0.25` *on top of* the keyword weight — enough
   to saturate a lone `used` match at full confidence. So description *length*
   alone can drive confidence to `1.0` even when the condition wording is minimal.
2. The seller explicitly states the card **"will not come with a box"** — precisely
   what `CFG.item_adjustments.missing_original_box` (`−$15`) exists to penalise.
   It has **no effect at all**, because `ingest.py` hardcodes `adjustments=[]`
   (line 161). See §11, fix D1.

---

## 3. Stage 5 — Comp aggregation

This is where the pipeline gets its price anchor. The band is built in three steps.

**Step 1 — time-window filter** (90-day window, `CFG.comp.comp_window_days`):

```
comps retrieved from sold_comps : 20
within 90-day window            : 20   (dropped 0)
```

**Step 2 — IQR outlier removal** (`1.5 × IQR` fence):

```
count after IQR trim : 20      (no outliers removed)
```

**Step 3 — band statistics:**

```
median    = $340.00
mean      = $340.00
P25 / P75 = $300.00 / $400.00
min / max = $200.00 / $480.00
std_dev   = 78.14
```

Prices used:

```
200, 220, 240, 260, 280, 300, 300, 320, 320, 340,
340, 360, 360, 380, 380, 400, 420, 440, 460, 480
```

**Remember this list.** In §6 we'll discover that ten of those twenty prices do
not belong to this product at all.

---

## 4. Stages 6–8 — Valuation and scoring

### Stage 6 — Fair Market Value

```
FMV = median × M_condition × M_trend + Σ adjustments

    = $340.00 × 0.82 × 1.0 + $0
    = $278.80

confidence interval = P25×M_cond  ..  P75×M_cond
                    = $300×0.82   ..  $400×0.82
                    = $246.00     ..  $328.00
```

`M_condition = 0.82` is `CFG.condition_multipliers.used_good`, read from config.

### Stage 7 — Expected Resale Value

```
ERV = FMV − platform_fees − shipping − return_reserve

FMV            = $278.80
platform fees  =  −$0.00     (kijiji_pct = 0.00 — local pickup, no fees)
shipping       =  −$0.00     (local pickup)
return reserve = −$13.94     (5% — 'gpu' is in the electronics_max category)
ERV            = $264.86
```

### Stage 8 — Deal score

```
travel cost  = $0.00    (distance 0 km ≤ free_radius_km 5)
holding cost = $41.86   (labor: (1.0 + 0.5 + 0.5)h × $20 = $40.00
                         capital: $278.80 × 0.08 / 12 = $1.86)

expected_profit = ERV − ask − travel − holding
                = $264.86 − $180.00 − $0 − $41.86
                = $43.00

margin_pct = $43.00 / $264.86 = 16.2%
```

Component scores:

| Component | Value | Why |
|---|---|---|
| `margin_score` | 16.2 | `profit / ERV × 100` |
| `liquidity_score` | 33.3 | 20 comps → `(20−5)/(50−5)×100` |
| `urgency_score` | 90.0 | posted 1 day ago |
| `confidence_score` | 90.0 | ≥ 20 comps → base 90 × condition conf 1.0 |
| `penalty_total` | 0.0 | no risk flags, travel 0 km |

Total (`CFG.score_weights`):

```
score = 0.50×16.2 + 0.20×33.3 + 0.15×90.0 + 0.15×90.0 + 0
      =  8.12   +  6.66   + 13.50   + 13.50
      = 41.78  →  41.8
```

Confidence tier — note this is driven by *evidence quality*, not profitability:

```
avg_confidence = confidence_score / 100 = 0.90

ALERT_IMMEDIATE? avg 0.90 ≥ 0.85 ✓, comps 20 ≥ 5 ✓, margin_score ≥ 20 ✗  → no
ALERT_CAUTION?   avg 0.90 ≥ 0.65 ✓, comps 20 ≥ 5 ✓                        → yes

→ ALERT_CAUTION
```

---

## 5. Stages 9–10 — The gate, and the outcome

```
profit  >= $50   :  $43.00 >= $50   → FALSE   ← fails here
margin  >= 15%   :  16.2%  >= 15%   → true
level in ALERT_* :  ALERT_CAUTION   → true
comps   >= 5     :  20 >= 5         → true

should_alert() → False
```

**This is the funnel working as intended.** The listing scored `ALERT_CAUTION`
(a perfectly respectable tier — real product, 20 comps, 90% confidence) but the
profit gate rejected it: **$43.00 is $7.00 short of the $50 threshold.**

This is the "near-miss" category. It's a genuinely defensible design: the system
would rather miss a marginal $43 deal than alert on noise. Stage 10 (the Discord
notifier) is never reached.

---

## 6. Hand-verification of the arithmetic

An interviewer will ask "how do you know the numbers are right?" Here is the
independent check — recomputing every stage by hand from the config values:

| Stage | Formula | Hand calculation | Code output | ✓ |
|---|---|---|---|---|
| Comp median | median of 20 sorted prices | (340 + 340) / 2 | `$340.00` | ✓ |
| FMV | median × 0.82 | 340 × 0.82 | `$278.80` | ✓ |
| FMV low | P25 × 0.82 | 300 × 0.82 | `$246.00` | ✓ |
| FMV high | P75 × 0.82 | 400 × 0.82 | `$328.00` | ✓ |
| Return reserve | FMV × 0.05 | 278.80 × 0.05 | `$13.94` | ✓ |
| ERV | FMV − fees − ship − reserve | 278.80 − 0 − 0 − 13.94 | `$264.86` | ✓ |
| Holding cost | (2.0h × $20) + (278.80 × 0.08/12) | 40.00 + 1.859 | `$41.86` | ✓ |
| Expected profit | ERV − ask − travel − holding | 264.86 − 180 − 0 − 41.86 | `$43.00` | ✓ |
| Margin % | profit / ERV × 100 | 43.00 / 264.86 × 100 | `16.2%` | ✓ |
| Margin score | profit / ERV × 100 | 43.00 / 264.86 × 100 | `16.2` | ✓ |
| Liquidity | (count−5)/(50−5)×100 | 15 / 45 × 100 | `33.3` | ✓ |
| Total score | 0.50m + 0.20l + 0.15u + 0.15c | 8.12 + 6.66 + 13.50 + 13.50 | `41.8` | ✓ |

**Every stage reconciles exactly.** The engine's arithmetic is sound. Whatever is
wrong with this system, it is not the math.

---

## 7. The funnel — 869 listings to 9 alerts

Aggregated across the whole database:

```
869  total decisions scored
169  ...with FMV > 0                      (19%)   ← the comp-coverage bottleneck
 93  ...with FMV > 0 AND tier ALERT_*
  9  ...passing the FULL alert gate       (1%)
```

Gate rejection reasons (non-exclusive — a row can fail several):

```
fails profit  : 847
fails margin  : 854
fails level   : 776
fails comps   : 700
```

Read plainly: **99% of everything scraped is suppressed**, and the dominant reason
is that most listings never get an FMV at all. That's `DECISIONS.md` §D6 — comp
coverage, not scraper throughput.

So: 9 alerts. Quality over quantity — but only if those 9 are *correct*.

---

## 8. The finding — auditing those 9 alerts

Since the funnel produces only 9 alerts, they are small enough to inspect by hand.
**So I inspected all nine.** Here they are, in full:

| # | Canonical product | Profit | Margin | Tier | Title |
|---|---|---|---|---|---|
| 1 | RTX 4090 | $1,893 | 96.2% | CAUTION | `GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090 6+2 Male` |
| 2 | RTX 4090 | $1,893 | 96.2% | CAUTION | `GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090 4+4 Male` |
| 3 | RTX 4090 | $661 | 26.2% | CAUTION | `GeForce 4090 RTX GPU` |
| 4 | RTX 4090 | $661 | 26.2% | CAUTION | `ROG Strix Gaming RTX 4090` |
| 5 | PlayStation 5 | $341 | 80.4% | CAUTION | `Soul Hackers 2 for PS5 (brand new sealed)` |
| 6 | RTX 3070 | $307 | 83.1% | **IMMEDIATE** | `NVIDA GeForce RTX 2080Ti 11GB 3060Ti 3070 8GB, older ATI/AMD` |
| 7 | Nintendo Switch | $217 | 73.8% | CAUTION | `NEW 2-in-1 Switch 2 Dock & Capture Card USB-C Video Streaming` |
| 8 | RTX 3080 | $124 | 26.6% | **IMMEDIATE** | `Great condition RTX 3060 Ti, RTX 3070, RTX3080 for sale` |
| 9 | RTX 3080 | $104 | 17.4% | CAUTION | `Nvidia RTX3080 New` |

**Six of the nine are not deals at all:**

- **#1, #2** — a **GPU power *cable*** ($20). Resolved to `RTX 4090` because the
  title mentions "RTX 3090 Ti, 4080 and 4090". The system believes it found a
  $2,070 GPU for $20.
- **#5** — a **video game** ("Soul Hackers 2 **for** PS5"). Resolved to
  `PlayStation 5`. A $40 disc valued as a $446 console.
- **#6** — a **multi-GPU lot**, not a single card.
- **#7** — an **accessory** (dock + capture card "**for** Switch").
- **#8** — a **bundle** ("RTX 3060 Ti, RTX 3070, RTX3080 **for sale**").

Only **#3, #4, #9** look like genuine single-GPU listings.

**Alert precision ≈ 3/9 ≈ 33%.** Two out of three alerts are wrong. Because the
system is intentionally human-in-the-loop, no money was lost — a person reading
these would dismiss the cable immediately. But it does mean **the system's core
output cannot currently be trusted without manual review**, which defeats much of
its purpose.

---

## 9. Finding A — comp bands are polluted by product variants

Look again at the 20 comp prices from §3:

```
200, 220, 240, 260, 280, 300, 300, 320, 320, 340,
340, 360, 360, 380, 380, 400, 420, 440, 460, 480
```

The seeded baseline table defines **`RTX 3060` with exactly 10 comps**
(`200…380`) and **`RTX 3060 Ti` with 10 comps** (`300…480`). The band above is
those two lists merged. **The `RTX 3060` valuation is being computed using
`RTX 3060 Ti` prices.**

### Root cause

`app/db/store.py`:

```python
def get_sold_comps(self, standard_name=None, max_days_old=90, limit=100):
    ...
    clauses.append("title LIKE ?"); params.append(f"%{standard_name}%")
```

A **substring** match. `"RTX 3060 Ti 5"` contains the substring `"RTX 3060"`, so
it matches a lookup for `RTX 3060`. The contamination is one-directional — a
lookup for `RTX 3060 Ti` does *not* match `RTX 3060` — so **base models absorb
their own variants.**

### Scope: 9 of 26 products (35%) are affected

| Product | Exact comps | `LIKE` comps | Median (correct) | Median (actual) | Distortion |
|---|---|---|---|---|---|
| GTX 1080 | 10 | 20 | $175 | $205 | **+17.1%** |
| GTX 1660 | 10 | 20 | $145 | $155 | +6.9% |
| RTX 3060 | 10 | 20 | $290 | $340 | **+17.2%** |
| RTX 3070 | 10 | 20 | $450 | $475 | +5.6% |
| RTX 3080 | 10 | 20 | $550 | $600 | +9.1% |
| RTX 4070 | 10 | 20 | $925 | $1,025 | **+10.8%** |
| RTX 4080 | 10 | 20 | $1,425 | $1,575 | +10.5% |
| RX 6800 | 10 | 20 | $440 | $470 | +6.8% |
| RX 7900 XT | 10 | 20 | $975 | $1,050 | +7.7% |

The pattern is systematic: **every base model with a Ti / Super / XT variant.**
A Ti card is worth *more* than its base model, so absorbing Ti comps inflates the
base model's FMV — in the worst cases by ~17%.

This is directionally dangerous: it **manufactures profit that doesn't exist**,
pushing marginal listings over the alert thresholds.

### Impact on the listing we traced

Re-running the *exact same listing* with the comp set corrected:

| | Contaminated (current) | Clean (correct) | |
|---|---|---|---|
| comps in band | 20 | 10 | |
| median | $340.00 | $290.00 | |
| **FMV** | **$278.80** | **$237.80** | −$41.00 |
| ERV | $264.86 | $225.91 | |
| **expected profit** | **$43.00** | **$4.32** | −$38.68 |
| **margin** | **16.2%** | **1.9%** | −14.3 pp |
| margin score | 16.2 | 1.9 | |
| liquidity score | 33.3 | 11.1 | |
| **deal score** | **41.8** | **28.7** | −13.1 |

**The bug overstates expected profit by ~10× ($4.32 → $43.00)** and lifts a 1.9%
margin to 16.2% — landing it just above the 15% gate, where the correct 1.9%
would have been nowhere near.

On this listing the error happened to be conservative (the gate still said no),
but that's luck. The distortion pushed the *margin* over the threshold; only the
independent profit check caught it. For a listing priced in between — say $150 —
the inflated band plausibly creates an alert that should never fire.

### Why this matters more than it looks

This silently corrupts the **core valuation input** for a third of the product
registry. Unlike the false positives in §8 (which are visible the moment you read
a title), this one is invisible: a `$290` median and a `$340` median both look
perfectly plausible. You only find it by checking the comp count against the
source table — which is exactly how it was found here.

---

## 10. Finding B — product resolution has no notion of *what the item is*

From §8, the resolver matched:

| Title | Resolved to | Actually is |
|---|---|---|
| `GPU power cable … RTX 3090 Ti, 4080 and 4090` | RTX 4090 | a **cable** |
| `Soul Hackers 2 for PS5` | PlayStation 5 | a **game** |
| `Switch 2 Dock & Capture Card` | Nintendo Switch | an **accessory** |
| `Alienware Laptop … RTX 3060` | RTX 3060 | a **laptop** |

`engines/resolver.py` matches aliases as **bare substrings** of the title, with no
context. Three distinct failure modes:

1. **Accessory / game / part** — the part *mentions* the product. Fix: require
   product words to be *the item being sold*.
2. **Component inside a larger item** — a laptop containing an `RTX 3060`.
   Fix: detect and reject host-item nouns, or treat as a different category.
3. **Multi-item bundles** — `"3060 Ti, 3070, RTX3080 for sale"` resolves to
   whichever alias the regex hits first (`RTX 3080`), then values the *whole lot*
   as one card.

A quick keyword scan across all alerts puts **26 of 93 (28%)** in the
accessory/bundle category — consistent with the 6-of-9 found by hand above.

Note the *scoring* engine is not at fault here: it faithfully valued what it was
told the product was. The defect is entirely in **product identification**, one
stage upstream. Garbage in, carefully-computed garbage out.

---

## 11. Prioritised fixes

| # | Fix | Where | Status |
|---|---|---|---|
| A1 | **Exact comp matching.** `product_key` column + `=` match, replacing `title LIKE '%name%'`. | `db/store.py`, `models/schema.py` | **DONE** |
| B1 | **Accessory/part guard.** Rejects cable/dock/cooler titles. | `engines/listing_guard.py` | **DONE** |
| B2 | **Bundle detection.** ≥ 2 distinct products ⇒ lot, rejected. | `engines/listing_guard.py` | **DONE** |
| B3 | **Host-item detection.** GPU inside a laptop/PC is not a GPU. | `engines/listing_guard.py` | **DONE** |
| C1 | **Consolidate the alert gate** into one shared function. | `valuation/gate.py` | **DONE** |
| D1 | **Wire up `ItemAdjustments`** — detect and apply deductions. | `engines/adjustments.py` | **DONE** |

Additional bugs surfaced *by* these fixes:

| # | Fix | Status |
|---|---|---|
| E1 | Registry gaps (`GTX 1070/1080/1080 Ti/1660/1660 Super`, `RTX 2070`, `RX 580/6800/6800 XT`) made those products unvaluable once exact matching was in place. | **DONE** |
| E2 | Registry ordering: `PlayStation 5` before `PS5 Pro` collapsed the two. Now guarded by an invariant test. | **DONE** |
| E3 | `max_days_old` was accepted and silently ignored. | **DONE** |

**Outcome:** alerts passing the gate went from **9 (6 of them junk) to 2**, and both
survivors are legitimate RTX 4090 listings. Alert precision moved from ~33% to
100% on this dataset, and decisions with a usable FMV rose from 92 to 122 once the
registry gaps were closed.

### How to evaluate the fixes (there is no ground truth yet)

Precision cannot be measured from the database alone — nobody recorded whether an
alert was *actually* a good deal. So:

1. Hand-label ~50 scraped listings: is the *identified product* correct? is it a
   genuine single item? (Cheap, and it's the honest baseline.)
2. Record the labels in `feedback_events` — the table already exists for exactly this.
3. Re-run and compare alert precision before/after.

That labeled set is also the seed of the ML phase: the same labels that make the
resolver *measurable* make a learned resolver *trainable*. It's why `DECISIONS.md`
D11 insists on logging the full payload — without it, none of this analysis would
have been possible.

---

## 12. Reproducing this document

Every number here came from running the live code against the real database. Two
checks cover the important findings:

**Verify Finding A (comp contamination):**

```sql
-- The seeded table defines exactly 10 comps for RTX 3060...
-- but this returns 20, because it also matches "RTX 3060 Ti":
SELECT COUNT(*) FROM sold_comps WHERE title LIKE 'RTX 3060 %';
```

**Verify Finding B (false positives):**

```bash
# Print every alert that passes the gate, next to its title, and read them.
# Six of nine are obviously not GPUs.
cd deal-sniper && python - <<'PY'
import json, sqlite3
c = sqlite3.connect("app/resale_engine.db"); c.row_factory = sqlite3.Row
for r in c.execute("SELECT decision_payload FROM valuation_decisions").fetchall():
    p = json.loads(r["decision_payload"]); ds, fv = p["deal_score"], p["fair_value"]
    if (ds["expected_profit_cad"] >= 50 and ds["margin_pct"] >= 15
            and ds["confidence_level"] in ("ALERT_IMMEDIATE", "ALERT_CAUTION")
            and fv["comps_used"] >= 5):
        print(f"${ds['expected_profit_cad']:>8,.0f}  {p['canonical_product']['standard_name']:<16}"
              f"  {p['raw_listing']['title'][:60]!r}")
PY
```

To regenerate the database from scratch first:
`cd app && python run_full_pipeline.py` (this scrapes live sources).

---

## 13. Thirty-second version (interview)

> "I traced one real listing through all ten stages and hand-verified every
> number — the arithmetic reconciles exactly. Then, because the funnel only
> produces nine alerts, I read all nine. Two-thirds were junk: a GPU power *cable*
> valued as a $2,070 RTX 4090, a video *game* valued as a PS5, an accessory dock
> valued as a Switch. The resolver does bare substring matching, so anything that
> *mentions* a product gets priced as that product.
>
> While verifying the comp band I found a second, worse bug: `get_sold_comps()`
> uses `LIKE '%RTX 3060%'`, which also matches `RTX 3060 Ti`. Nine of twenty-six
> products have bands polluted by their own Ti/Super/XT variants, inflating FMV by
> up to 17%. On the listing I traced it overstated expected profit ten-fold —
> $4.32 of real margin shown as $43.00, lifting a 1.9% margin to 16.2% and
> crossing the 15% gate.
>
> The takeaway: the scoring layer is sound and well-tested, but the two stages
> feeding it — product identification and comp retrieval — were trusted without
> validation, and that's where the error actually lives. The comp fix is a
> one-line query change. The harder problem is that there's no ground truth, so
> I'd hand-label ~50 listings into `feedback_events` and measure alert precision
> before and after."

---

## 14. Why this document exists

It would have been easy to write a worked example showing the pipeline succeeding
and stop there. The arithmetic *does* succeed — that part is genuinely solid.

But an example that only demonstrates the happy path teaches nothing about whether
the system works. The real payoff of tracing one listing all the way through was
that it made the **comp count** checkable, and `20 ≠ 10` is what exposed the
contamination bug.

The lesson worth carrying into an interview: **verify the inputs to a
calculation, not just the calculation.**