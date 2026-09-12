# Deal Sniper — Comp-Driven Resale Valuation Engine

A pipeline that finds underpriced second-hand hardware by valuing listings
against **completed sales**, scoring the profit net of real costs, and alerting a
human. Canada / CAD.

It is **not** a scraper collection, **not** an autonomous buying bot, and **not**
an AI price-guesser.

> **New here?** Read [`PROJECT_STATUS.md`](PROJECT_STATUS.md) first — it's a
> verified map of what works today versus what's stubbed. Then
> [`DECISIONS.md`](DECISIONS.md) for the reasoning behind each design choice.

---

## The core idea

Most "deal finders" compare an asking price to *other asking prices*. That's
circular — asking prices are wishes, and they're set by sellers who want more
than the item is worth.

This system instead values against **transactions that actually completed**,
aggregates them statistically with outlier rejection, then subtracts the costs
that turn a headline price into a real margin:

```
asking price  ──┐
                │        ┌─ sold comps (completed transactions)
                │        │      ↓  IQR outlier trim
                │        │   price band (median, P25–P75)
                │        │      ↓  × condition multiplier
                │        └─ FMV  (fair market value)
                │               ↓  − fees − shipping − return reserve
                └──────→  ERV  (expected resale value, net)
                                ↓  − travel − labor − capital lockup
                          expected profit  →  deal score  →  alert tier
```

**Valuation ≠ deal scoring.** Working out what something is worth and deciding
whether acting on it is worth your time are two separate problems, and this code
keeps them separate.

---

## Current status at a glance

| | |
|---|---|
| **Live sources** | Kijiji, Craigslist |
| **Gated (not built)** | eBay, Facebook Marketplace — both require accounts |
| **Last verified run** | 867 listings scraped, 867 decisions scored, both sources live (2 pass the alert gate) |
| **Tests** | 176 engine tests + 39 scraper-parser tests, all passing |
| **Known bottleneck** | Comp *data access* — no free source exposes real sold comps (eBay gated, PriceCharting/130point behind Cloudflare). A lifecycle "disappearance" proxy exists but is off by default |

Full detail, including tech debt and honest caveats, is in
[`PROJECT_STATUS.md`](PROJECT_STATUS.md). **Read it before presenting this
project** — it lists the things that would otherwise be found the hard way.

---

## Quick start

```bash
# 1. Install
cd app
pip install -r requirements.txt

# 2. Run the test suite (no network needed)
python -m pytest -q                      # 176 passed

# 3. Run the full pipeline: scrape live -> value -> persist (accumulating DB)
python run_full_pipeline.py

# 3b. Demo mode: synthetic comps, self-contained DB (do NOT draw conclusions)
python run_full_pipeline.py --fresh --seed-demo

# 4. Serve the API
uvicorn app.main:app --port 8000
```

The Craigslist scraper needs a browser. It drives the **Chrome already installed
on your machine** via Playwright (`channel="chrome"`), so normally nothing extra
to install; if Playwright's package is missing, `pip install playwright`.

> The database **accumulates** by default — that is what lets the pipeline track
> listings across runs and infer sales from disappearances. Pass `--fresh` for a
> throwaway, self-contained run.

---

## Repository layout

```
deal-sniper/
├── app/                          # the valuation engine + API
│   ├── engines/                  # resolver, condition, comp aggregation
│   ├── valuation/                # FMV, ERV, deal scoring
│   ├── pipeline/ingest.py        # end-to-end orchestration
│   ├── adapters/sources.py       # marketplace adapters (isolates each source)
│   ├── db/                       # SQLite store + Postgres schema
│   ├── alerting/notifier.py      # Discord / Telegram
│   ├── tests/                    # 81 tests
│   ├── main.py                   # FastAPI
│   ├── dashboard.py              # Streamlit review UI
│   └── DEVELOPER.md              # module-by-module reference
│
├── kijiji-scraper/               # GraphQL-based, isolated project
├── craigslist-scraper/           # Playwright-based, isolated project
├── ebay-scraper/                 # account-gated (not production-ready)
├── facebook-marketplace-scraper/ # account-gated (not production-ready)
│
├── PROJECT_STATUS.md             # what's real vs stubbed (verified)
├── DECISIONS.md                  # why each choice was made + rejected alternatives
├── copilotinstructions.md        # golden principles, tech stack, roadmap
└── WORKED_EXAMPLE.md             # one listing traced end-to-end + audit
```

Each scraper is a **self-contained project** with its own config, models, and
storage, so a markup change in one marketplace can't break the others.

---

## How a listing flows through the system

Each stage is a pure function; all thresholds live in `app/config.py`.

| Stage | Module | What it does |
|---|---|---|
| 1. Ingest | `adapters/sources.py` | Scrape a marketplace, normalize to `RawListing` |
| 2. Pre-filter | `pipeline/ingest.py` | Drop junk: price < $10, "contact for price", trade posts, services |
| 3. Resolve | `engines/resolver.py` | Title → canonical product, e.g. "EVGA RTX 3080 FTW3" → `RTX 3080` |
| 4. Condition | `engines/condition.py` | Grade from keywords → `used_good`, with a confidence 0–1 |
| 5. Comps | `engines/comp_engine.py` | IQR-trim outliers, compute median / P25 / P75 band |
| 6. FMV | `valuation/fair_value.py` | `median × condition_multiplier ± adjustments` |
| 7. ERV | `valuation/realize.py` | `FMV − platform fees − shipping − return reserve` |
| 8. Score | `valuation/score.py` | Weighted margin/liquidity/urgency/confidence − penalties |
| 9. Gate | `pipeline/ingest.py` | Profit ≥ $50 **and** margin ≥ 15% **and** ≥ 5 comps |
| 10. Alert | `alerting/notifier.py` | Discord embed to a human who decides |

Product resolution is a **3-tier waterfall** (exact alias regex → fuzzy
`SequenceMatcher` → fallback to the raw title), so an unknown product degrades
to "unresolved" rather than being mis-priced. No LLM is involved in resolution
or pricing.

---

## Design principles

These are the non-negotiables the code is held to:

1. **Comp-driven, never AI-guessed.** Valuations come from completed
   transactions. LLMs never compute or override a price, a score, or a threshold.
2. **Deterministic-first.** Rules, regex dictionaries, and statistics do the
   work. Every engine function is pure, so every number is reproducible.
3. **Valuation ≠ deal scoring.** What it's worth and whether it's worth your
   time are separate concerns.
4. **Human-in-the-loop.** Alert only. No autonomous checkout, seller messaging,
   or account automation.
5. **Traceability from day one.** Every decision is logged with its full payload
   — that log *is* the future ML training set.
6. **Config is the single source of truth.** No magic numbers in engine logic.

---

## Documentation index

| Document | Read it for |
|---|---|
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | What works today, verified metrics, known tech debt |
| [`DECISIONS.md`](DECISIONS.md) | Why each choice was made, alternatives rejected, interview Q&A |
| [`WORKED_EXAMPLE.md`](WORKED_EXAMPLE.md) | One listing traced end-to-end, math hand-verified, plus two real bugs it exposed |
| [`app/DEVELOPER.md`](app/DEVELOPER.md) | Module-by-module reference, valuation math, API, extending |
| [`copilotinstructions.md`](copilotinstructions.md) | Golden principles, tech stack, phased roadmap |
| [`kijiji-scraper/README.md`](kijiji-scraper/README.md) | Kijiji GraphQL architecture |
| [`craigslist-scraper/README.md`](craigslist-scraper/README.md) | Craigslist browser-rendering architecture |

---

## Scope and compliance

**In scope:** read-only discovery of public listings, statistical valuation,
deal scoring, and alerting a human.

**Explicitly out of scope — by design, not by omission:**

- Autonomous purchasing, checkout, or seller messaging.
- Marketplace account automation.
- Credential-gated sources (this is why eBay and Facebook are unimplemented
  rather than scraped around).

A wrong valuation should cost a glance, not money.
