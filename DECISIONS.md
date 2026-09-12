# Decision Log — Deal Sniper

> **Purpose:** the *why* behind each design choice, the alternatives that were
> rejected, and the tradeoff accepted. Written so the reasoning is explainable in
> an interview, not just the outcome.
>
> Each entry follows: **Context → Decision → Alternatives rejected → Tradeoff →
> "30-second version"** (what to actually say out loud).

---

## D1 — Valuation is comp-driven; the LLM never touches price

**Context.** The obvious shortcut is to ask an LLM "what's this worth?" It's one
API call and handles messy titles for free.

**Decision.** Value comes only from **completed sales** (sold comps), aggregated
statistically. LLMs are permitted *only* for unstructured-text enrichment (messy
titles, bundle decoding) and must always return schema-validated JSON.

**Alternatives rejected.**
- *LLM-as-pricer* — non-deterministic, un-auditable, and hallucinates prices for
  rare items. You cannot backtest it or explain a wrong number.
- *Active asking prices as ground truth* — asking prices are **wishes**, not
  transactions. They're useful for measuring supply/liquidity, never value.

**Tradeoff.** Requires a sold-comp data source, which is the hardest part of the
project (see D6). Chose harder-and-correct over easy-and-wrong.

**30-second version:** "Valuations come from aggregated completed transactions.
The LLM is a text parser, not a pricing model — it structurally cannot influence
a price, which also means every number is reproducible and backtestable."

---

## D2 — Deterministic-first engine, config as the single source of truth

**Context.** Pricing logic is full of magic numbers (fee %, condition
multipliers, score weights).

**Decision.** Every threshold, multiplier, fee, and weight lives in
`app/config.py` as a dataclass. Engine modules (`resolver`, `condition`,
`comp_engine`, `fair_value`, `realize`, `score`) are **pure functions** over
explicit inputs.

**Alternatives rejected.**
- *Inline constants* — untestable, and every tuning pass becomes a code change.
- *Env-var-per-parameter* — untyped, unvalidated, and painful for ~60 related
  parameters.

**Tradeoff.** More indirection: you read `CFG.alert_gate.min_margin_pct` instead
of `15` locally. Buys testability and a single tuning surface.

**Proof it was worth it.** The 81-test suite is entirely deterministic because
`tests/conftest.py` resets `CFG` to defaults before every test — no fixtures
needed for tuning parameters.

**30-second version:** "Pure functions plus one typed config object. It means the
engines are unit-testable in isolation and every business threshold is greppable
in exactly one file."

---

## D3 — Kijiji: use the internal GraphQL API, not HTML scraping

**Context.** Kijiji is a Next.js app. Three ways to get listings:
`__NEXT_DATA__` JSON embedded in the HTML, the internal Apollo GraphQL endpoint
at `/anvil/api`, or parsing rendered HTML.

**Decision.** GraphQL is the **primary** path; `__NEXT_DATA__` is retained as a
page-1 fallback if GraphQL fails.

**Alternatives rejected.**
- *`__NEXT_DATA__` only* — it's right there in the page, but it's **capped**
  (the code notes ~40 listings). That cap was the single biggest limiter on
  corpus size.
- *HTML parsing* — most brittle of the three; breaks on any markup change, and
  the markup already changed once.

**Tradeoff.** GraphQL is an undocumented internal endpoint — it can change or be
gated without notice. Mitigated by the fallback path and by isolating the source
behind an adapter so a break is contained to one module.

**Also:** used `cloudscraper` rather than `requests` to clear Cloudflare's
JS challenge without a browser — cheaper than launching Chromium for a source
that doesn't need it.

**30-second version:** "Kijiji embeds data in `__NEXT_DATA__` but caps it at ~40
results. I switched to their internal GraphQL endpoint for pagination depth, and
kept `__NEXT_DATA__` as a fallback since it's free resilience."

---

## D4 — Craigslist: a headless browser is mandatory (not a preference)

**Context.** This one is a three-act story, and worth telling in full because it
demonstrates debugging a source that actively changed under the scraper.

**Decision.** Drive a real browser via Playwright (`craigslist-scraper/browser.py`),
parse the rendered DOM with BeautifulSoup.

**Alternatives rejected — in the order they were tried:**
1. *Plain HTTP + BeautifulSoup.* Returns an ~8 KB JavaScript shell with **zero
   result rows.** Craigslist renders results client-side. Not a workaround-able
   problem — there is no data in the response.
2. *The `?format=rss` feed.* Historically exposed search results as RSS. Now
   returns **404 / 403.** A dead escape hatch, and the reminder that undocumented
   conveniences get closed.
3. *Rendered browser.* Works. Shipping path.

**Tradeoff.** Playwright is a heavyweight dependency (~150 MB browser), adds
seconds per page, and can't run in a slim container without extra steps. Accepted
because there is no alternative that yields data at all.

**Engineering details that came out of it:**
- **One browser process, many pages.** `browser.py` keeps a single Chromium alive
  across navigations instead of launching per request — a 3-page sweep pays
  start-up once.
- **`channel="chrome"`, with fallback.** Launch the already-installed Chrome; if
  that channel is missing, transparently fall back to Playwright's bundled
  Chromium. Avoids forcing a large download on a machine that has a browser.
- **Images blocked** via route interception — pure bandwidth, useless for parsing.

**30-second version:** "Craigslist search is client-rendered, so HTTP returns an
empty shell and the old RSS endpoint now 404s. I used Playwright to execute the
page's JS, reusing one browser process across pages and reusing the machine's
installed Chrome instead of downloading a bundled browser."

---

## D5 — Multi-term sweep instead of one broad query

**Context.** The Kijiji adapter originally ran one broad query
(`search_term="graphics-card"`). Corpus: ~183 listings.

**Decision.** Sweep a configured list of nine GPU-specific search terms and union
the results, de-duplicating by URL (`KijijiScraper.scrape_terms()`).

**Why it works.** Kijiji search is keyword-ranked, not exhaustive. A broad term
returns plenty of *loosely related* matches but ranks specific models onto deep
pages; a narrow term like `rtx-4090` surfaces listings the broad term buries.
The union is larger than either, and the de-dup makes it idempotent.

**Alternatives rejected.**
- *One broad query, more pages.* Just ranks the same loosely-related items deeper;
  it does not reach the model-specific long tail.
- *More pages only* — same problem; the ranking, not the depth, was the limit.

**Tradeoff.** N terms × P pages = N×P requests, so scrape time scales linearly
and politeness/delay matters more. Accepted because the corpus went from ~183 to
754 listings — a 4× gain for a linear cost increase on a cheap-to-poll source.

**30-second version:** "A single broad search under-returns because results are
keyword-ranked. I sweep a list of specific model terms and union by URL — 4× the
corpus for a linear, predictable increase in requests."

---

## D6 — Seeded baseline comps for the POC (and why it's now the bottleneck)

**Context.** The whole premise (D1) is comp-driven. But eBay *sold* data has no
public API, and scraping it is fragile and ToS-risky.

**Decision (POC).** Ship a manually curated `BASELINE_COMPS` table (~26 GPU
models, realistic CAD price ranges) so the pipeline can be validated
end-to-end **before** taking on a fragile data-acquisition problem. Track live
sold-comp ingestion as the explicit next phase.

**Alternatives rejected.**
- *Build eBay sold scraping first* — would have blocked all downstream validation
  (FMV, ERV, scoring, alerting, persistence) on the single most fragile component.
- *Active eBay listings as a proxy* — violates D1. Asking prices are not
  transactions.

**Tradeoff — and be upfront about this.** It creates a **stated-principle gap**:
the system claims to be comp-driven while running on a static table. The numbers
show it: only 169/869 decisions (19%) produced a non-zero FMV, because the table
covers ~26 products and the scrapers return a long tail.

**This is the most important thing to understand about the project's current
state:** the bottleneck moved. It is no longer scraper throughput (754 Kijiji
listings is plenty) — it is **comp coverage per product**. The fix is a data
problem (real sold comps + a wider product registry), not more scraping.

**30-second version:** "I seeded a realistic baseline comp table so I could
validate the entire valuation and scoring pipeline before taking on eBay sold-data
acquisition, which has no public API. That was the right sequencing call, but it's
now the binding constraint: only 19% of listings have comp coverage, so the next
lever is comp data, not more scraping."

---

## D7 — SQLite now, PostgreSQL as the declared target

**Context.** The spec calls for PostgreSQL (needed later for `pg_trgm` fuzzy
matching and concurrent Celery writers).

**Decision.** `db/store.py` is SQLite and is what the pipeline actually runs on.
`db/migrations.sql` + `db/session.py` describe the Postgres target (6 tables,
indexes, FK constraints, JSONB payload columns) but are not yet exercised.

**Why SQLite first.** Zero setup for a single-machine, single-process POC; the
whole dataset is a few MB. Postgres would have added a container dependency
before there was any concurrency to justify it.

**Tradeoff / honest caveat.** The two schemas have **drifted**, and it shows.
The SQLite shim has no FK enforcement, so `valuation_decisions` has no real link
to `raw_listings` — which is exactly why decisions (869) exceed listings (802).
In Postgres that drift would be a constraint violation, not a silent duplicate.

**30-second version:** "SQLite for the single-process POC, with the Postgres
schema and async session already written for the multi-worker phase. The tradeoff
is real: SQLite doesn't enforce the FK, so I currently have 869 decisions against
802 listings. Postgres would reject that outright — and on the migration I'll add
the dedup."

---

## D8 — Scraper isolation via module eviction (a deliberate hack)

**Context.** Each scraper lives in its own project directory and each ships
top-level modules named `config`, `models`, `scraper`, `browser`. They import each
other **by absolute name** (`from config import load_config`).

**The bug.** Loading Kijiji and Craigslist in one process meant the first project
to load won: the second silently imported the *first* project's `config` and
`models`. The failure mode was nasty — no exception, just wrong settings and wrong
model classes.

**Decision.** `_import_scraper()` in `app/adapters/sources.py` (a) evicts any
cached `sys.modules` entry that traces back to *any* scraper project, and
(b) puts the target project's directory first on `sys.path` before loading
`scraper.py` by file path via `importlib.util.spec_from_file_location`.

**Alternatives rejected.**
- *Rename the scraper modules* (e.g. `kijiji_config.py`) — the correct long-term
  fix, but touches four projects plus their imports and tests: a large diff to
  solve a problem contained to one call site.
- *Run each scraper in a subprocess* — real isolation, but adds IPC/serialization
  overhead and complicates every pipeline call.
- *Ignore it and scrape one source per process* — makes correctness depend on
  invocation order, an invisible landmine.

**Tradeoff.** `_import_scraper()` is non-obvious code with a genuine smell: it
mutates global interpreter state, which can surprise other in-process code. It is
correct (both sources now ingest from one process) and explicitly commented, but
it is **documented tech debt** — the proper fix is renaming the modules or making
each scraper a real package.

**30-second version:** "Four scraper projects each defined top-level modules named
`config` and `models`, so loading two in one process made the second silently
import the first's settings — no exception, just wrong data. I fixed it in the
adapter by evicting stale modules and controlling `sys.path`. It's contained and
commented, but I'd call it tech debt; packaging each scraper is the real fix."

---

## D9 — Trim comp outliers with IQR, not mean ± stddev

**Context.** Comp sets contain junk: parts-only cards at $100 sitting beside
working cards at $2,500. Outliers must not drag the value.

**Decision.** Use the **median** as the central value and trim with the
1.5×IQR fence (drop anything outside `Q1 − 1.5·IQR` … `Q3 + 1.5·IQR`). Publish
P25/P75 as the confidence interval rather than a symmetric ± band.

**Alternatives rejected.**
- *Mean ± 2σ* — the mean and σ are themselves dragged by the outliers you're
  trying to remove (not robust). One $3,000 listing distorts the band for a GPU
  that really sells at $450.
- *Hard binned thresholds* — arbitrary per-product cutoffs that don't generalise
  to a 30th product.
- *Take the mean and hope* — the naive baseline that IQR exists to beat.

**Tradeoff.** With very few comps IQR is unstable (`n < 5` gives poor quartile
estimates), and if *everything* is an outlier the code falls back to the untrimmed
prices. That's mitigated by a minimum-comp-count gate before valuation is trusted.

**30-second version:** "Comps include parts-only and mis-listed items, so I trim
with the 1.5×IQR fence and use the median rather than the mean — mean ± stddev
isn't robust, since a single $3,000 outlier distorts it. I report P25–P75 as the
confidence interval."

---

## D10 — Tiered alert gating instead of a binary "deal / no deal"

**Context.** A single boolean "is this a deal?" either floods the user with noise
or misses everything, and gives no way to express "look at this, but verify first".

**Decision.** Four confidence levels — `ALERT_IMMEDIATE`, `ALERT_CAUTION`,
`REVIEW`, `SUPPRESS` — assigned from a confidence score (driven by comp volume ×
condition confidence, so **evidence quality**, not just price, decides the tier).
Profit alerts additionally require **all** of: expected profit ≥ $50 **and**
margin ≥ 15% **and** an `ALERT_*` level **and** ≥ 5 comps.

**Why comp-count is part of the gate.** A 40% "discount" computed from 2 comps is
noise. Requiring ≥ 5 comps makes the system refuse to act on thin evidence, which
is what protects precision as the corpus grows.

**Alternatives rejected.**
- *Binary threshold on discount %* — no way to triage; forces a false
  precision/recall choice.
- *Let the LLM judge risk* — violates D2. Risk is rule-based and reproducible.
- *Alert everything above profit threshold* — floods the channel and destroys
  trust in the alerts, which is the project's core output.

**Resolved.** The gate was implemented **twice** — once in `should_alert()` (4
conditions) and once inline in `store.insert_decision()` (3 conditions, omitting
the comp-count check). It now lives in one place, `valuation/gate.py`, and both
callers delegate to it. The two copies had never actually disagreed, but
duplicated business rules drift the first time a threshold changes — that risk is
now gone.

*Correction to an earlier draft:* it claimed the gates disagreed "9 vs 2". That
was wrong — it compared a gate-pass count to the `ALERT_IMMEDIATE` tier count, and
`ALERT_CAUTION` rows legitimately pass the gate too.

**30-second version:** "Four confidence tiers rather than a yes/no, and the profit
gate also requires at least 5 comps — a 40% discount from 2 comps is noise. The
caveat is that I implemented that gate in two places and they drifted: the DB
flags 9 alerts where the pipeline only reaches 2 immediate. Classic duplicated
business logic; consolidating it is a known fix."

---

## D11 — Persist the full decision payload, not just the score

**Context.** The scoring weights, thresholds, and condition multipliers are all
*guesses* at first. They can only be improved if you can see how past decisions
were actually made.

**Decision.** Every decision is stored with its **entire `ValuationOutput`
serialised to JSON** (`decision_payload`), alongside denormalised summary columns
(`fmv_cad`, `erv_cad`, `deal_score`, `confidence_level`) that are cheap to query
and index.

**Why both.** Summary columns make aggregate analytics fast (`WHERE deal_score >
80`); the JSON payload preserves full fidelity so a future model can be trained on
exactly what the engine saw, without a schema migration. This is the
**intentional foundation of the ML phase** — the traceability log *is* the future
training set.

**Alternatives rejected.**
- *Summary columns only* — loses the inputs; you can never retroactively
  understand or reproduce a decision.
- *Normalise everything into columns* — a schema migration for every new
  condition signal or risk flag.
- *Log to files* — not queryable, not joinable, and lost on redeploy.

**Tradeoff.** JSON payloads are denormalised and can drift from the schema over
time, and they inflate row size. Accepted: full-fidelity history is worth more
than storage at this scale.

**30-second version:** "I store the whole valuation output as JSON next to
indexed summary columns. Summary columns make analytics fast; the JSON keeps full
fidelity so I can train a model on exactly what the engine saw without a
migration. The decision log is deliberately the ML training set."

---

## D12 — Hard scope boundary: alert-only, no autonomous purchasing

**Context.** The obvious "next feature" is to auto-message sellers or auto-buy
underpriced listings.

**Decision.** **No.** The system is human-in-the-loop by design: it alerts, a
person decides. No checkout automation, no seller messaging, no marketplace
account automation. eBay and Facebook are also left unbuilt rather than
credential-scraped, because both require gated accounts.

**Why this is a design choice, not a missing feature.**
- **Legal/ToS.** Automated marketplace actions and account automation breach
  platform terms; the compliant path is read-only discovery plus human action.
- **Capital risk.** A wrong automated purchase costs real money; a wrong alert
  costs a glance. The asymmetry justifies the gate.
- **It keeps the system's claim honest.** The project's value is
  *valuation quality*, not execution. Buying poorly-valued items faster is not a
  product.

**Tradeoff.** Slower end-to-end loop, and the ceiling on automation is lower.
Accepted deliberately.

**30-second version:** "It's alert-only by design — no auto-messaging or
auto-checkout. Read-only discovery plus a human decision sidesteps ToS violations
and means a valuation error costs a glance instead of real money. I also left eBay
and Facebook unbuilt rather than credential-scrape them."

---

## Quick reference

| # | Decision | One-line reason |
|---|---|---|
| D1 | Comp-driven valuation; LLM can't price | Deterministic, auditable, backtestable |
| D2 | Pure functions + one typed config | Testable; every threshold in one file |
| D3 | Kijiji via internal GraphQL, `__NEXT_DATA__` fallback | `__NEXT_DATA__` is capped at ~40 |
| D4 | Craigslist via headless Playwright | HTTP returns an empty shell; RSS now 404s |
| D5 | Multi-term search sweep + URL union | Keyword ranking hides model-specific listings |
| D6 | Seeded baseline comps for the POC | Validate the pipeline before the fragile data problem |
| D7 | SQLite now, Postgres schema pre-written | No container dep before there's concurrency |
| D8 | Module eviction in the scraper adapter | Same-named modules silently cross-contaminated |
| D9 | IQR fence + median, not mean ± σ | Mean/σ aren't robust to the outliers being removed |
| D10 | Four confidence tiers + 4-condition gate | Triage, and refuse to act on thin comp evidence |
| D11 | Store full decision payload as JSON | The decision log is the future ML training set |
| D12 | Alert-only; no autonomous purchasing | ToS compliance and capital-risk asymmetry |

---

## Anticipated interview questions

**"What does your system do?"**
"A comp-driven resale valuation engine. It scrapes marketplace listings, resolves
each to a canonical product, values it from aggregated *completed* sales using
outlier-trimmed price bands, subtracts fees and costs, then scores the deal and
tiers it for alerting. Two sources are live — Kijiji and Craigslist — 802 listings
and 869 scored decisions in the last run."

**"Why not just ask an LLM what it's worth?"**
"Because then I can't explain or reproduce a number, and I can't backtest it.
Prices come only from aggregated completed transactions; the LLM's role is bounded
to text parsing with schema-validated output, so it structurally can't influence a
price."

**"What was the hardest bug?"**
"Two candidates. First, a silent one: four scraper projects each defined top-level
modules named `config` and `models`, so loading two in one process made the second
import the first's settings — no exception, just wrong data. Second, Craigslist
changed its result markup *and* its date formats; listings were parsing but with
empty IDs and nearly no dates. Dates turned out to arrive three different ways on
the same site, so the parser now normalises all three."

**"How do you know it actually works?"**
"81 engine tests plus 39 parser tests, all deterministic because config is reset
per test. Then an end-to-end run against live sites, verified by querying the DB
directly — 802 listings, both sources present, and 54/54 Craigslist listings
carrying dates after the fix."

**"What's the biggest weakness?"**
"Comp coverage, and I'd rather say it than have you find it. Only 169 of 869
decisions produced a non-zero FMV, because my comp table covers ~26 products while
the scrapers return a long tail. The bottleneck moved from scraping to comp data —
so the next lever is real sold comps and a wider product registry, not more
scraping throughput."

**"How would this scale?"**
"Move persistence to the Postgres schema that's already written — it has FK
constraints that SQLite silently ignores, which is the cause of my
869-decisions-vs-802-listings discrepancy — then wire the Celery beat schedule,
which currently exists as documented no-op stubs, and put the scrapers behind
per-source rate budgets."

**"What would you do differently?"**
"Package each scraper instead of relying on the `sys.path` eviction trick — that
was the pragmatic fix for a contained bug, but it mutates global interpreter state
and it's the kind of thing that bites later. And I'd define the alert gate exactly
once; it currently exists in two places and they've drifted."

---

## Related documents

| Document | Contents |
|---|---|
| `PROJECT_STATUS.md` | What works today, verified metrics, tech debt |
| `WORKED_EXAMPLE.md` | Real listing traced end-to-end; bugs found by doing it |
| `copilotinstructions.md` | Golden principles, tech stack, build order/phases |
| `app/DEVELOPER.md` | Module-by-module reference, valuation math, API |
| `kijiji-scraper/README.md`, `craigslist-scraper/README.md` | Per-source architecture |