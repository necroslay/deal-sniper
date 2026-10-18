# Interview Prep — Deal Sniper

> Everything needed to talk about this project **accurately**: what it is, why each
> decision was made, the numbers and their provenance, and one listing traced end
> to end. Written so nothing here has to be walked back under questioning.
>
> Companions: [`PROJECT_STATUS.md`](PROJECT_STATUS.md) (what works vs doesn't) and
> [`reports/README.md`](reports/README.md) (measured results).

---

## 1. The pitch

### 30 seconds

> "It's a deal-finding pipeline for second-hand computer parts. It scrapes Kijiji
> Canada-wide and Craigslist, resolves each messy title to a canonical product,
> flags junk like accessories and lots, tracks listings across scrapes to see
> what's new and what's likely sold, and shows it all in a dashboard.
>
> The interesting part is the ML layer: deterministic rules stay authoritative,
> and models only handle the cases the rules abstain on. I built an evaluation
> harness first — a frozen leakage-safe split, per-class metrics, and three CI
> gates that fail the build if model quality regresses."

### 2 minutes

Same, plus:

- **Why rules-first.** For a title like `"EVGA GeForce RTX 3080 FTW3 Ultra"`, the
  correct answer is a *lookup*, and a curated alias list is near-optimal at
  lookups. The model's job is the cases the regex can't decide — `"PS 5 Digital"`
  (spacing it misses) or `"MSI Raider GE78"` (no tracked product at all).
- **What I measured.** On 115 hand-labeled listings the TF-IDF resolver beats the
  rules (0.871 vs 0.844 macro-F1; 85 train / 30 held-out). The rule-informed guard
  hybrid beats the rules too (0.849 vs 0.790 macro-F1). The price model does
  **not** beat a simple per-product quantile baseline ($404 vs $284 MAE) — and I
  kept the simple one.
- **How I know it works.** 233 tests, plus `pytest` and three `--check-baseline`
  gates in CI. Every number is reproducible from a fixed seed and a committed
  dataset.

### What it is NOT — say this early

- **Not a valuation engine.** The price model describes the distribution of
  *asking* prices among comparable live listings. Asking prices are wishes, not
  transactions. It supports "underpriced relative to its peers" and nothing stronger.
- **Not autonomous.** No purchasing, no seller messaging, no account automation.
- **Not a deep-learning project.** No transformers, no LLMs in the decision path.
  The models are TF-IDF + logistic regression and LightGBM quantile regression.
- **Not production-accurate.** Test sets are 30–67 rows and some classes carry a
  single example. It demonstrates *method*, not market accuracy.

---

## 2. Architecture

```
   Kijiji (Canada-wide)  ─┐
                          ├─→ adapter ─→ dedupe ─→ ENRICH ─→ SQLite ─→ Streamlit
   Craigslist (GTA)      ─┘                          │
                                        rules first │ model for gaps only
                                                     ▼
                             eval harness ─→ reports/*.md + *.json ─→ CI gates
```

**Two halves, one seam.**

- **The deterministic product:** scrape → store once forever → track lifecycle →
  show a human. This works with no ML dependencies installed at all.
- **The ML layer:** opt-in, off by default (`CFG.ml.enabled = False`).

**The single seam is `app/engines/enrich.py`.** `store_listings()` calls
`resolve_and_assess(title)` once per listing. Inside: rules run first; a model is
consulted *only* for titles the resolver left at Tier-3 ("unknown") or listings
the guard didn't flag. With ML disabled it reproduces pre-ML behaviour exactly —
there is a test asserting that on a spread of titles.

> **Why one seam matters:** it's the difference between "I bolted models on" and
> "models enter at exactly one place, can't affect the audited path, and can be
> switched off." It also meant adding the price model later touched nothing in the
> scraper.

---

## 3. Decisions — why, and what I rejected

Each entry: **Context → Decision → Alternatives rejected → Tradeoff → what I say out loud.**

---

### D1 — Rules stay authoritative; models only fill abstentions

**Context.** An ML layer could replace the regex resolver outright. For a title
like `"EVGA RTX 3080 FTW3"`, ML is not obviously better — and it is definitely
less explainable.

**Decision.** The resolver's Tier-1 alias match and the guard's hard rejects are
authoritative. A learned model is consulted **only** where the rules abstain: the
resolver's Tier-3 ("unknown") bucket, and guard borderline cases.

**Alternatives rejected.**
- *Model-first, rules as fallback* — makes the common path non-deterministic and
  unauditable, for gains concentrated in a small slice.
- *Delete the rules once the model works* — throws away 52 products of curated
  alias knowledge to relearn it from a few hundred examples.

**Tradeoff.** The hybrid structurally **cannot** capture gains the model makes on
titles the rules resolve *confidently and wrongly*. That is a real cost, and I
measured it: the standalone resolver model beats the rules, but the rules-first
hybrid scores identically to the rules (0.844 macro-F1 both). I kept the
conservative hybrid anyway and report the standalone model as the upside.

**30-second version:** *"Rules first, model only where rules abstain. That keeps
the common path deterministic and auditable. The cost is that the hybrid can't
exploit the model's wins on cases the rules get confidently wrong — I measured
that, reported it, and left the choice explicit rather than hiding it."*

---

### D2 — Evaluation harness **before** any model

**Context.** The tempting order is: train a model, print accuracy, ship it. That
produces a number nobody can interpret, because there is no baseline to compare
against and no frozen test set to trust.

**Decision.** Build the measurement first: a `Predictor` interface, per-class
metrics, a frozen split, a markdown report, and a machine-readable JSON that CI
compares against. Only then add models.

**Alternatives rejected.**
- *`train_test_split` from sklearn* — random splits leak here: sellers relist and
  cross-post, so near-identical titles land on both sides and inflate the score.
- *Report-only, no gate* — a metric nobody enforces rots silently.

**Tradeoff.** Slower to the first "model works" demo. But the harness immediately
earned its keep: it caught four data defects I would otherwise have shipped
(D5–D7 and the alias bug).

**30-second version:** *"I built the measurement before the model. That sounds
slow, and it was — but the harness is what caught a split hiding four classes from
training and a metric averaging phantom classes. Without it I'd have shipped both."*

---

### D3 — The ML layer is opt-in, dependency-isolated, and config-driven

**Context.** The base project is "deliberately small": scrape, look, decide. Adding
scikit-learn, LightGBM and pandas to the base install for a feature most users
wouldn't use is the wrong default.

**Decision.** `CFG.ml.enabled = False` by default; ML dependencies live in a
separate `app/requirements-ml.txt`; every threshold, path and seed lives in one
`MLConfig` dataclass, never inline in engine code.

**Alternatives rejected.**
- *Bake the deps into `requirements.txt`* — triples the install for the 90% case.
- *Env-var-per-parameter* — untyped and unvalidated for ~20 related settings.

**Tradeoff.** More indirection — you read `CFG.ml.resolver_min_confidence` instead
of `0.8`. Buys one greppable tuning surface and a test-visible switch.

**30-second version:** *"The ML layer is off by default and lives in its own
requirements file, with all thresholds in one typed config object. The base
project still runs identically with zero ML dependencies installed."*

---

### D4 — Labels live in a committed CSV, not in the database

**Context.** Training data needs to be reproducible and diffable. The database is
a scratch artifact — it's gitignored, and every scrape rewrites it.

**Decision.** Labels are a **self-contained** committed CSV
(`data/labels/deals.csv`) that carries the title text alongside the label.
Training and evaluation never touch the database.

**Alternatives rejected.**
- *Store labels as a column in `raw_listings`* — the DB is gitignored, so labels
  would vanish with it and couldn't be reviewed in a diff.
- *Label against listing ids and join at train time* — a re-scrape or a schema
  change silently breaks the join.

**Tradeoff.** The CSV duplicates title text. Worth it: the dataset is version-
controlled, reviewable, and immune to database churn — which is why the classifier
numbers were **unchanged** after the Canada-wide harvest.

**30-second version:** *"Labels are a committed, self-contained CSV, not database
state. That's why re-scraping 1,577 new rows didn't move the classifier metrics at all."*

---

### D5 — The split is a **stratified group** split

**Context.** This dataset leaks in two directions. Sellers relist and cross-post,
so near-identical titles appear many times. And classes are tiny — some products
have one or two listings.

**Decision.** Split on the **group** (a hash of the normalised title), so a title
never spans train and test. **Then** add a stratification pass: any label with no
group in `train` has its lowest-sorted group promoted whole into train.

**Alternatives rejected.**
- *Random row split* — near-duplicate titles on both sides inflate the score.
- *Hash-only group split (what I shipped first)* — leak-free but it stranded four
  classes (`GTX 1080`, `GTX 1080 Ti`, `MacBook Pro M3`, `RX 6800 XT`) entirely in
  val/test, where no model can predict them and they score a hard zero.

**Tradeoff.** Promotion moves a few groups, so the guard and resolver test sets
differ (35 vs 30 rows). Worth it: fixing this alone took the resolver model from
0.484 to 0.871 macro-F1. The model was never bad — the split was.

**30-second version:** *"Two constraints pull against each other: no title may
appear on both sides, but every class must be learnable. I split on title-groups,
then promoted a group into train for any class that had none. That single fix took
the resolver model from 0.484 to 0.871 macro-F1 — the model had been fine, my split
was starving it."*

---

### D6 — Rare labels are folded using **training support only**

**Context.** The label space is a long tail — a handful of common GPUs and dozens
of one-off products. A class the training split can't learn from is not a fair
thing to score a model on.

**Decision.** Any label with fewer than `min_class_support` (=3) examples **in the
training split** is folded into `OTHER`. The mapping is then applied to true labels
*and* predictions in every split, so the comparison stays fair.

**Alternatives rejected.**
- *Fold using the whole dataset's counts* — leaks val/test information into a
  training decision.
- *Keep every rare class* — macro-F1 becomes dominated by classes with one example;
  the metric measures noise.

**Tradeoff.** Coarser label space, and it creates an arguably unfair target: a
title saying "3070 Ti" has the true label `OTHER` because that class was folded.

**30-second version:** *"I fold ultra-rare classes into OTHER, but compute the
support threshold from the training split only — otherwise it's a leak. The cost is
that the label space gets coarser, which I accept because scoring a one-example
class measures noise."*

---

### D7 — Macro F1 excludes zero-support classes

**Context.** The resolver report showed classes like `iPhone 14` and `iPhone 15`
with **support 0** — never true, but sometimes *predicted*. They were being scored
as hard zeros and averaged into macro-F1, dragging the rules' baseline from **0.776
down to 0.597**.

**Decision.** Macro-F1 averages only over classes that occur in the true labels. A
spurious prediction is still penalised — through the *precision* of whatever class
it stole a prediction from.

**Alternatives rejected.**
- *Keep sklearn's default behaviour* (average over labels present in true ∪
  predicted) — it lets one phantom label swing the whole metric.
- *Filter them out of the report* — hides a genuine signal, namely that something
  is making impossible predictions.

**Tradeoff.** A slightly non-standard macro-F1, so it's documented and unit-tested
rather than left implicit.

**30-second version:** *"Macro-F1 was averaging in classes that were predicted but
never true, scoring them as zeros and understating the baseline by 0.18. I now
average only over classes actually present in the labels. A false positive still
costs you — it costs the class it stole from."*

---

### D8 — The price model is validated on a **temporal** split

**Context.** A price model predicts for *newly posted* listings. A random split
tests it on listings from the same period as training, which is not the situation
it will face.

**Decision.** Split by `date_posted`: oldest rows train, newest rows test
(`price_test_fraction` = 0.25). The field is present on 100% of rows and spans
2013–2026, so no waiting was needed to do this.

**Alternatives rejected.**
- *Random split* — would have hidden real drift, and the model would look better
  than it is.
- *Wait for multiple scrape days before validating at all* — a misconception I
  corrected: `date_posted` already supplies a time axis. Repeated observation is a
  *different* need (price cuts, time-to-sell), not a prerequisite for a temporal split.

**Tradeoff.** The newest listings are where prices have moved, so the test is
strictly harder. That is the point.

**Proof it mattered:** the baseline's P25–P75 band covers only **30%** of newer
listings against a 50% target. Under a random split it would sit near 50% by
construction. The temporal split turned an invisible assumption into a measured
finding — the band is miscalibrated out of time.

**30-second version:** *"I split by posting date, because that's how it's used:
predict new listings from old ones. It immediately showed the band is mis-calibrated
— 30% coverage against a 50% target. A random split would have reported ~50% and
told me nothing."*

---

### D9 — Strict framing: these are **asking** prices, never a valuation

**Context.** The obvious pitch for a price model is "what's it worth?" That would
be wrong here, and the project already has a documented history of getting burned
by it: an earlier comp-driven valuation engine was archived precisely because no
free source exposes real *sold* data.

**Decision.** The model predicts the distribution of **asking prices among
comparable live listings**. The supported claim is "underpriced relative to its
peers" and nothing stronger. Class names, report text and docstrings all say so.

**Alternatives rejected.**
- *Call it FMV / market value* — asking prices are wishes, not transactions. A
  wrong number that looks authoritative is worse than no number.
- *Blend in the archived retail anchor as if it were value* — retail is a
  **ceiling**, not a price.

**Tradeoff.** Weaker-sounding feature. But the alternative is a claim I can't
defend in a follow-up question.

**30-second version:** *"It models asking prices, not value. Asking prices are
wishes, not transactions — so the honest claim is 'underpriced relative to
comparable live listings'. I'd rather say the weaker true thing than the stronger
false one."*

---

### D10 — A per-product quantile baseline is the incumbent to beat

**Context.** With only a few hundred rows and a handful of comps per product, a
per-product empirical quantile is a strong predictor. Any model has to justify its
dependency.

**Decision.** Ship the baseline *first*, always report it, and treat "the model
loses" as a publishable result rather than something to tune away.

**Alternatives rejected.**
- *Report only the model* — flattering and useless; there's nothing to compare to.
- *Keep tuning until the model wins* — that is fitting the test set.

**Tradeoff.** The headline price result is a **negative one**: LightGBM loses
($404 vs $284 MAE) on 200 training rows. I ran a capacity sweep (7 → 31 leaves
spans $398–$408) to show it isn't a tuning problem, and left the default small
because it is better *calibrated* (0.36 vs 0.25 coverage) and cheaper.

**30-second version:** *"I shipped the simple baseline first, and it wins — $284 vs
$404 MAE. Capacity is flat across a 4× range, so it's not a tuning problem; the
boosted model just has no signal beyond the product-level price. I kept the simple
one and reported the negative result."*

---

### D11 — The retail ceiling is opt-in, and I did not fabricate prices

**Context.** A used item shouldn't be worth more than the cheapest new
alternative, so new-retail is a natural ceiling. But there is no reliable free
retail scrape — the archived engine documented Canada Computers and Vuugo
responding while Memory Express, Newegg, Amazon and Best Buy blocked or CAPTCHA'd.

**Decision.** Read a hand-maintained `data/retail_prices.csv`. Commit a clearly
labelled `.sample.csv` for the format. **When the file is absent the ceiling simply
doesn't apply** — the model runs on peer data alone.

**Alternatives rejected.**
- *Ship invented "typical retail" numbers* — would make the ceiling look exercised
  when it isn't. It's currently 0 rows capped, and the report says so.
- *Scrape retail anyway* — known dead end from prior work.

**Tradeoff.** A built-but-unproven component. Better than a proven-looking fiction.

**30-second version:** *"The ceiling is wired in and tested in isolation, but it's
off because I refuse to invent retail prices — there's no reliable free source. The
report shows 0 rows capped, so it costs nothing and proves nothing yet."*

---

### D12 — CI gates, not report-only

**Context.** A benchmark you don't enforce rots. The first model change that
regresses quality will be invisible unless something fails.

**Decision.** Three gates run in CI: `harness --task resolver`, `--task guard`, and
`price_harness`, each with `--check-baseline`, comparing against the committed
`reports/*_metrics.json` and **exiting non-zero on regression**. A predictor present
in the baseline but missing from a run is *also* a regression, so a model can't be
quietly dropped from the report.

**Alternatives rejected.**
- *Report artefacts in CI without failing* — nobody reads them.
- *Absolute thresholds* ("macro-F1 must exceed 0.8") — brittle and arbitrary;
  regression-against-committed-baseline is the honest invariant.
- *Auto-update the baseline in CI* — makes the gate vacuous.

**Tradeoff.** Changing the data or the labels requires a deliberate re-baseline
commit, which shows up as a reviewable diff. That's a feature.

**30-second version:** *"Three gates fail the build on regression against a
committed baseline. Re-baselining is an explicit commit, so a quality drop can't
sneak in unnoticed — and dropping a model from the report counts as a regression."*

---

### D13 — Scraper scope is config-driven, and the location segment is mandatory

**Context.** `config.yaml` advertised a configurable `url_template`, but
`scraper.py` **hardcoded** the GTA path and location id in `_fetch_page_graphql` —
so editing the config silently did nothing. Separately, expanding to Canada-wide
needed a URL form that actually searches.

**Decision.** Build the URL from `region` + `location_id` in config. **Always emit
the location segment** (`k0l0` = every location in the region); coerce an empty
value to `0` so it can't be dropped. Default is Canada-wide.

**Alternatives rejected.**
- *`/b-canada/{term}/k0`* (drop the location segment) — **this is the trap.** It
  reports 4,148 hits for `graphics-card` and returns *cars, jewellery and
  magazines*. It's a browse, not a scoped search. I nearly shipped it because the
  hit count looked great.
- *Hardcode a list of city location ids* — two of the ids I guessed were wrong;
  probing is what settled it.

**Tradeoff.** Canada-wide is noisier: 1,577 rows but only 502 resolve. Depth is now
the binding constraint on volume, not the location filter.

**30-second version:** *"The location was hardcoded, so the config setting was a
lie — I fixed that. And I nearly shipped a Canada-wide URL that reported 4,148 hits
because dropping the location segment turns the search into a browse; a GPU query
came back with cars and jewellery. I caught it by checking titles, not hit counts,
and there's a test pinning the correct form."*

---

### D14 — Enrichment is derived state: recompute it, don't trust it

**Context.** `product_key` and `flag_reason` are written **once**, at scrape time,
by whatever resolver and guard existed then. So a resolver fix never reaches rows
already stored.

**Decision.** Treat them as derived. Two mechanisms:
1. **`app.re_resolve`** — a maintenance command that re-derives both columns from
   the stored title, with `--dry-run`. It repaired **61 rows, 54 of them phantom
   iPhones**, and is now idempotent (0 remaining).
2. **The price feature builder re-resolves titles at build time** rather than
   reading the stored column, so stale enrichment can't poison features even if
   nobody runs the maintenance command.

**Alternatives rejected.**
- *Re-scrape to fix it* — slower, and it throws away history (`first_seen_at`).
- *Trust the stored column in features* — silently trains on known-bad labels.

**Tradeoff.** Re-resolving on every feature build costs a little time; the rows are
hundreds, not millions.

**30-second version:** *"Those columns are derived at scrape time, so a resolver fix
doesn't reach existing rows. I added a re-enrichment command that repaired 54
phantom iPhones, and made the feature builder re-resolve rather than trust the
stored value — derived state gets recomputed, not trusted."*

---

## 4. Bugs worth telling stories about

These are the strongest interview material — real, non-obvious, and each produced a
permanent guard.

1. **The bare-numeric alias.** `iPhone 15` / `iPhone 14` carried the aliases `"15"`
   and `"14"`, so *any* title containing those digits resolved to an iPhone:
   `Dell XPS 15`, `iMac 2015`, `ThinkCentre M715q`, `MacBook Pro 15-inch`. That's
   why the earlier `product_key` census showed `iPhone 15 ×36` and `iPhone 14 ×18`.
   The resolver's own code comments warn about exactly this for `1080`/`1080p` —
   the iPhone entries escaped the rule. **Fix:** removed them, added a registry
   invariant test (numeric aliases must be ≥4 digits), plus the re-resolve command.

2. **The metric averaging phantom classes.** See D7 — cost the baseline 0.18 macro-F1.

3. **A split that starved four classes.** See D5 — cost the model 0.39 macro-F1.

4. **The Canada-wide URL trap.** See D13 — would have filled the database with cars.

5. **Module shadowing across scraper projects.** Both scraper projects define
   top-level modules named `config` and `models`. Loading both in one process made
   the second project silently import the first's settings — no exception, just
   wrong data. The adapter evicts those names from `sys.modules` and loads by path.

6. **Craigslist had three date formats and changed its markup.** Search pages are
   JS-rendered (a plain GET returns an ~8KB shell with zero rows), `?format=rss`
   404s, and dates arrive as relative labels, year-less `M/D`, or absolute
   datetimes. Solved with Playwright + a normaliser.

---

## 5. Numbers, and exactly where each comes from

**Say the provenance with the number.** Every figure below is reproducible.

### The corpus (`app/listings.db`, after the Canada-wide run)

| Figure | Value | How it's produced |
|---|---|---|
| Listings | **1,577** | one Canada-wide run |
| Distinct locations | **226** | across the country |
| By source | kijiji 1,522 (15 `gone`) · craigslist 55 | — |
| Resolved | **502** (32%) | re-run `resolve_and_assess` over every title |
| Unresolved | 1,075 | Tier-3 fallback: `product_key == title` |
| Flagged junk | **225** | current guard: component-in-host 166, accessory 35, lot 22, platform 2 |
| Priceable | **267** | resolved **AND** unflagged **AND** inside the per-product IQR fence |

### The models

| Task | Predictor | Accuracy | Macro F1 |
|---|---|---|---|
| Resolver (n=30) | rules baseline | 0.733 | 0.844 |
| | **TF-IDF model** | **0.867** | **0.871** |
| | rules-first hybrid | 0.733 | 0.844 |
| Guard (n=35) | rules baseline | 0.829 | 0.790 |
| | rule-informed model | 0.829 | 0.816 |
| | **hybrid** | **0.857** | **0.849** |

| Price (n=67) | MAE (P50) | P25–P75 coverage |
|---|---|---|
| **per-product quantile baseline** | **$284** | 0.30 |
| LightGBM quantile | $404 | 0.36 |

### Dataset sizes — and the one number to be careful with

```
hand-labeled (data/labels/deals.csv)   115 rows   (68 keep / 47 reject)
   resolver split:  85 train / 19 val / 11 test   -> evaluated on 30
   guard split:     80 train / 22 val / 13 test   -> evaluated on 35

price rows (derived from the DB)       267 rows
   temporal split:  200 train / 67 test
```

> ⚠️ **Two different 115s exist and they are unrelated.** The classifier's
> hand-labeled set is **115**. The price model's usable row count was
> *coincidentally also 115* before the harvest, and is now **267**.
>
> Also: 115 is the **labeled** set, not the training set — the resolver trains on
> **85** and is scored on **30**. If asked "where does 115 come from?", answer
> *"115 hand-labeled listings; the stratified group split holds out val+test, so 85
> train and 30 are scored."* Never imply training on 115, and never use 115 for the
> price model.

### Settings you may be asked to justify

| Setting | Value | Why |
|---|---|---|
| `CFG.ml.enabled` | `False` | ML is opt-in; base project needs no ML deps |
| `resolver_min_confidence` | `0.8` | conservative — below this the hybrid degrades the metric |
| `guard_reject_threshold` | `0.5` | rule rejections already win; this only adjudicates the rest |
| `min_class_support` | `3` | rare-label fold threshold, counted on **train** only |
| `seed` | `42` | determinism — makes CI gating meaningful |
| `price_test_fraction` | `0.25` | newest 25% held out, by `date_posted` |
| `misses_before_gone` | `3` | consecutive deep scrapes before a listing is 'gone' |
| Kijiji scope | `canada` / `location_id "0"` | `l0` = every location in the region |

**Model internals** (if someone asks "what kind of model is it?"):
- **Resolver:** TF-IDF word (1–2) grams ∪ char (2–5) grams, `sublinear_tf`,
  `min_df=1`, `max_features=20000` → `LogisticRegression(C=4.0,
  class_weight="balanced", max_iter=2000)`.
- **Guard:** `FeatureUnion[ the guard's own 6 pattern signals, word TF-IDF, char
  TF-IDF ]` → `LogisticRegression` — the model is built *on top of* the rules.
- **Price:** LightGBM `objective="quantile"`, one model per quantile
  (0.25/0.5/0.75), `n_estimators=150`, `num_leaves=7`, `learning_rate=0.05`,
  `min_child_samples=10`.

---

## 6. One listing, end to end

Thread this example through — it exercises most branches:

> **`"EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB"`**, $520, Toronto, Kijiji id `1743389618`

### Stage 1 — CLI entry (`app/scrape.py`)
`python -m app.scrape --source kijiji` → `main()` builds
`platforms = [Platform.kijiji]`, resolves page depth (`--pages`, else
`CFG.scraper.kijiji_pages` = 8), and opens the `Store` on `app/listings.db`
(creating/migrating the schema on connect).

### Stage 2 — Adapter dispatch (`app/adapters/sources.py`)
`get_adapter(Platform.kijiji)` → `KijijiAdapter`. First, `_import_scraper("kijiji")`
**evicts `config`/`models`/`scraper`/`browser` from `sys.modules`** if they came
from a scraper project, then loads `kijiji-scraper/scraper.py` by full path.

Because `scrape.py` passes the sentinel `KIJIJI_QUERY = "ALL"`, the adapter calls
`scrape_terms(...)` — the **term sweep** — not a single query.

### Stage 3 — Term sweep (`kijiji-scraper/scraper.py`)
`scrape_terms()` walks the 9 GPU terms in `config.yaml`, calls
`scrape(term, max_pages=8, high_end_only=False)` for each, and **unions the results
de-duped by URL**. Narrow terms surface listings the broad term ranks off the first
pages.

### Stage 4 — Per-page fetch
`scrape()` loops pages 1..8 → `_fetch_page_graphql(term, page)`:
- Builds the URL from config: `CFG.search.search_url(term, page)` →
  `/b-canada/rtx-3080/k0l0`, later pages `/b-canada/rtx-3080/page-3/k0l0`.
- POSTs to `https://www.kijiji.ca/anvil/api` with `operationName:
  SearchResultsPage`, `variables.by.url = <that URL>`, `pagination.limit = 40`,
  via **`cloudscraper`** to get past Cloudflare.
- If page 1's GraphQL call fails → falls back to fetching the HTML and extracting
  the `__NEXT_DATA__` Apollo blob.

> Pagination lives in the **URL path** (`/page-N/`); the `pagination.offset` field
> is ignored by the API. And the `l0` segment is mandatory — see D13.

### Stage 5 — Parse into the scraper's own model
`_parse_graphql_listing()` maps the JSON → `KijijiListing`:

| Field | Source | Sample value |
|---|---|---|
| `title` | `title` | `EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB` |
| `price_cad` | `price.amount` **in cents** ÷ 100 | `52000` → `520.0` |
| `url` | relative, prefixed with base | `https://www.kijiji.ca/v-...` |
| `location` | `location.name` | `Toronto` |
| `date_posted` | `activationDate` | ISO string |
| `listing_id` | `str(id)` | `1743389618` |
| `image_urls` | `imageUrls` | list |

### Stage 6 — Normalise to the app model
The adapter converts each `KijijiListing` → **`RawListing`** (Pydantic), mapping
`listing_id` → `source_listing_id` via field alias and setting
`source=Platform.kijiji`. Any exception is logged and returns `[]` — plus
`scrape.py` wraps each source in try/except, so a dead source can't kill the run.

### Stage 7 — Dedupe within the run
`dedupe_by_url(collected)` collapses the same listing surfacing under several
search terms. *(Dedup layer 1. Layer 2 is the DB constraint, Stage 9.)*

### Stage 8 — Enrichment: the one seam (`app/engines/enrich.py`) ⭐
`store_listings()` calls **`resolve_and_assess(title)`** once per listing:

1. **`_load_models()`** — no-op unless `CFG.ml.enabled`. If enabled, lazily loads
   `models/resolver_model.pkl` + `guard_model.pkl`; a missing package or corrupt
   pickle is treated as *"no model"*, never a crash.
2. **Rules first** — `resolve_product(title)`:
   - **Tier 1** regex over `PRODUCT_REGISTRY` aliases (spaces relaxed to
     `[\s\-]*`, case-insensitive) → `RTX 3080`, category `gpu`.
   - (Tier 2 fuzzy substring >0.8; Tier 3 fallback = raw title, category `unknown`.)
3. **Model only for gaps** — `category != "unknown"`, so the model is **not
   consulted**.
4. `assess_listing(title, product)` — accessory? → platform-accessory
   (`"X for PS5"`)? → component-in-host (a GPU inside a laptop)? → multi-item lot?
   All no → **`None`**.

**Result: `(product="RTX 3080", flag=None)`.**

### Stage 9 — Persist (`app/db/store.py::observe_listing`)
- **Refuses a blank `source_listing_id`** (counted *skipped*) — filing it under the
  blank key would let every id-less listing overwrite one row, collapsing hundreds
  into one with no error.
- Looks up by `(source, source_listing_id)`:
  - **not found → INSERT** — UUID id, `first_seen_at = last_seen_at = now`,
    `miss_count = 0`, `status='active'`, plus `product_key`/`flag_reason`.
    Counted **new**.
  - **found → UPDATE** — refresh `last_seen_at`, reset `miss_count = 0`,
    `status='active'`, `gone_at = NULL`, refresh title/price/description and
    enrichment. Counted **refreshed**.
- `UNIQUE(source, source_listing_id)` is what makes this *store once, forever*
  *(dedup layer 2)*.

### Stage 10 — Lifecycle close (`close_observation_cycle`)
- **Coverage-aware:** if a source was swept *shallower* than its previous run, that
  source is skipped entirely — a shallow sweep isn't evidence anything disappeared.
- Every `active` listing **not** seen this run: `miss_count += 1`; at
  `CFG.lifecycle.misses_before_gone` (3) → `status='gone'`, `gone_at = now`.
- Records this run's depth for the next comparison.

### Stage 11 — Summary and dashboard
The CLI prints new/refreshed/skipped, lifecycle, DB totals, top products. The
Streamlit dashboard caches a DataFrame for 30s, derives `flagged` from
`flag_reason`, and its **"Run scrape now"** button invokes the *same* CLI as a
**subprocess** — Playwright's sync API dislikes Streamlit's process, and a scrape
crash can't take the viewer down.

### The three exceptional paths
1. **Junk** — `"GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090"` resolves to a
   product, but `assess_listing` returns `accessory_or_part` → stored in
   `flag_reason` → hidden by the dashboard's "Hide accessories / lots".
2. **Unknown** — `"Wooden desk"` → Tier 3 → `category="unknown"`,
   `product_key` = the raw title, no flag.
3. **ML-enabled** — an unresolved title gets a model product only above **0.8**
   confidence; a listing the rules *kept* can get `flag_reason="ml_guard"`.

### Second scrape run
Same id → **refreshed**, `miss_count` reset. Absent for 3 consecutive deep runs →
**gone** ("likely sold").

### Where the ML touches this trace (and where it doesn't)

| Stage | With ML disabled (default) | With ML enabled |
|---|---|---|
| Enrich | `resolve_product` + `assess_listing` | same, **then** model only for unknowns / unflagged |
| `product_key` | regex product, or the raw title | possibly a model-resolved product |
| `flag_reason` | rule reason or `NULL` | also `"ml_guard"` |
| Store, lifecycle, dashboard | **unchanged** | **unchanged** |

> The point to make: enabling ML changes *two columns* and nothing else. The
> audited path — dedup, persistence, retirement — is untouched.

---

## 7. Questions you should expect

### The easy ones

**"What does your system do?"** → Use the 30-second pitch (§1). Then offer the trace (§6).

**"What kind of model is it?"** →
*"Resolver: TF-IDF over word and character n-grams into a logistic regression. Char
n-grams matter because sellers write `RTX3080`, `rtx-3080`, `RTX 3080`. Guard: the
same plus the guard's own pattern hits as numeric features. Price: LightGBM quantile
regression, one model per quantile, P25/P50/P75. Deliberately small models — I have
115 labeled rows for the classifiers and 267 price rows; anything bigger would
memorise."*

**"Why not just ask an LLM what it's worth?"** →
*"Two reasons. It's non-deterministic and un-auditable — I can't explain or reproduce
a number, and I can't backtest it. And the valuations that do exist in this project
come from aggregated asking prices, so an LLM would be adding a hallucination layer
on top of data I can already aggregate."*

**"How do you know it works?"** →
*"233 tests, plus three CI gates that fail the build if macro-F1 or MAE regresses
against a committed baseline. The harness reports per-class precision/recall and
interval calibration, not just one aggregate number. Everything is seeded and
reproducible from a committed dataset."*

**"How would this scale?"** →
*"Three bottlenecks in order. Persistent storage — SQLite is fine for thousands of
rows but the lifecycle queries want indexes, and the Postgres schema already exists
from the archived engine. Scheduling — scrapers currently run by hand; a per-source
rate budget and a scheduler come next. And the actual constraint, which is labeled
data and resolved-product coverage, not throughput."*

**"What was the hardest bug?"** →
*"The bare-numeric alias. Two registry entries for the iPhone 15 and 14 carried bare
`15` and `14` as aliases, so anything containing those digits resolved to an iPhone —
a Dell XPS 15, an iMac 2015, a ThinkCentre M715q. The resolver's own comments warn
about exactly that for `1080`/`1080p`; the iPhone entries escaped the rule. The nasty
part is that the fix only affects new scrapes, so I also had to write a
re-enrichment command to repair 54 rows already in the database."*

### The hard ones — have these ready

**"Your model loses to a simple baseline. Why ship it?"** →
*"That is the result, and I'd rather report it than tune it away. A per-product
empirical quantile beats LightGBM, $284 vs $404 MAE. I checked it isn't a tuning
problem — sweeping capacity from 7 to 31 leaves spans $398–$408, so the boosted
model isn't over- or under-fitting, it just has no signal beyond the product-level
price. I kept the simple baseline as the shipped predictor. It needs more data, or
features that actually vary."*

**"Isn't 30 test rows too few to claim anything?"** →
*"Yes, for accuracy claims — and I say so in the report. 30 to 67 rows, some classes
with a single example, means wide confidence intervals. What it does demonstrate is
the method: a frozen leakage-safe split, a baseline, honest metrics, and a gate. I'd
want hundreds per class before quoting a number as a capability."*

**"You're modelling asking prices. Isn't that meaningless?"** →
*"It's weak evidence, which is why I'm strict about the claim. Asking prices are
wishes, not transactions, so I only claim 'underpriced relative to comparable live
listings'. I don't call it a valuation. The project genuinely has history here: an
earlier comp-driven valuation engine was archived because no free source exposes real
sold data, so I already know how that story ends."*

**"Why not use a transformer or an LLM for the resolver?"** →
*"With 115 labeled examples, fine-tuning would overfit and I couldn't attribute
errors. TF-IDF plus char n-grams captures exactly what matters — model numbers written
inconsistently — and I can read the learned weights. The one place an LLM would earn
its keep is bounded extraction from descriptions with schema validation, and that's a
documented next step rather than something I'd claim now."*

**"68% of your data is unresolved — isn't that a failure?"** →
*"It's the honest cost of a national broad-term sweep, and it's a data problem rather
than a model one. The unresolved tail is trading cards, camcorders, lenses, 'BUYING'
wanted ads, whole PCs, and untracked hardware. Two fixes: tighten the search terms,
and add a 'not a computer part' verdict — the guard knows accessory, lot, platform and
component-in-host, but has no out-of-scope category. Resolving more rows is cheaper
than scraping more pages."*

**"Your corpus is one day. How do you know it generalises?"** →
*"For the classifier, because the labels are a committed CSV independent of the
scrape, and they're a curated read of real titles. For price I don't claim temporal
generalisation — `date_posted` spans 2013–2026 so I can split temporally, and that
split shows the band is mis-calibrated out of time: 30% coverage against a 50%
target. That's a real finding a random split would have hidden. What one day can't
tell me is price cuts or time-to-sell; those need a second scrape on a later date."*

**"What's the biggest weakness?"** →
*"Data volume, and it shows up three times: the resolver model needed a split fix
rather than a better model to beat the rules; the price model still loses to a
per-product quantile; and every metric rests on 30–67 test rows. The methodology is
sound, the evidence is thin."*

**"What would you do differently?"** →
*"Build the evaluation harness on day one rather than day two — it caught four data
defects I'd otherwise have shipped, two of which were mine. And I'd fix the scraper's
module shadowing properly by making each scraper project a real package instead of
evicting names from `sys.modules`; that works, but it's a global-state hack that will
bite."*

**"Isn't this just a scraper?"** →
*"The scraper is the easy half. The parts I'd point at are the dedup and persistence
model — store once forever, keyed on the marketplace's own id — the coverage-aware
retirement that refuses to infer a listing is gone from a shallow sweep, and the
evaluation harness. Those are the parts with decisions in them."*

---

## 8. Volunteer these before you're asked

Leading with limitations is the cheapest credibility you can buy. Roughly in this order:

1. **The price model loses to a simple baseline.** $404 vs $284 MAE. Not a tuning
   problem (capacity sweep is flat). The simple one is what ships.
2. **Every metric rests on 30–67 test rows**, with some classes having a single
   example. Method, not production accuracy.
3. **68% of the corpus is unresolved** at national scale — a data/search-term
   problem, and I know which two levers fix it.
4. **The price band is miscalibrated out of time**: 30% coverage against a 50%
   target. Found *because* the split is temporal.
5. **One scrape day.** No price-cut or time-to-sell signal yet — that needs a second
   run on a later date.
6. **No serving layer.** Models run offline and through the enrichment seam; there's
   no HTTP endpoint or monitoring yet.
7. **No deep learning.** Deliberate, but it means this project doesn't evidence
   transformer or LLM work.
8. **The retail ceiling is unexercised** (0 rows capped) because I won't invent
   retail prices.

---

## 9. Verifying anything on the spot

```bash
cd app && python -m pytest -q                       # 233 passed

# the three gates (fail on regression vs committed baseline)
python -m app.eval.harness --task resolver --check-baseline
python -m app.eval.harness --task guard --check-baseline
python -m app.eval.price_harness --check-baseline

# read the results
#   reports/README.md, reports/resolver_eval.md, reports/guard_eval.md, reports/price_eval.md
#   PROJECT_STATUS.md  (works vs doesn't)

# derived state is recomputed, not trusted
python -m app.re_resolve --dry-run                   # expect 0 changes
```

### A 5-minute demo, if asked

1. `streamlit run app/dashboard.py` — show the listing table, the "Hide accessories
   / lots" toggle, and the "New (24h)" / "Gone (likely sold)" metrics.
2. Open `reports/resolver_eval.md` — point at the per-class table, not the headline.
3. Run `python -m app.eval.price_harness` live — show the temporal split line
   (`train 200 -> test 67`) and the coverage column.
4. Change a label in `data/labels/deals.csv`, re-run the resolver harness, and show
   the gate **fail**. That's the moment that lands: the metric is enforced, not
   decorative.

---

## 10. Glossary — if a term comes up

| Term | Meaning here |
|---|---|
| **Resolver** | Maps a raw title to a canonical product (`"EVGA … RTX 3080 …"` → `RTX 3080`) |
| **Guard** | Rejects listings that merely *mention* a product (accessories, lots, "for PS5", a GPU inside a laptop) |
| **Enrichment** | The single seam where resolution + guard (+ optional ML) run |
| **Tier 3 / unknown** | The resolver's fallback: no alias matched, so `product_key` is the raw title |
| **Group** | A hash of the normalised title, used as the split unit so relists can't leak |
| **Macro F1** | Per-class F1 averaged equally across classes — treats rare classes as important |
| **Pinball loss** | The loss a quantile model minimises; asymmetric, unlike MAE |
| **Coverage** | Share of outcomes inside a predicted interval; a P25–P75 band should hit ~0.50 |
| **MAE** | Mean absolute error, in CAD here |
| **Re-baseline** | Deliberately committing new metrics after a data or label change |
| **`gone`** | Retired after N consecutive deep sweeps without a sighting — a *proxy* for sold, not an observed sale |
