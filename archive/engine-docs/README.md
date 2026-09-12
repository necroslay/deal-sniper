# Archived — the full valuation engine

These documents describe the **comp-driven resale valuation engine** that this
repository used to be: product resolution, sold-comp aggregation with IQR outlier
trimming, FMV/ERV maths, deal scoring, an alert gate, a FastAPI service, listing
lifecycle inference and retail anchoring — 203 tests' worth of logic.

They were archived when the project was simplified to **scraper + viewer**. The
code is not gone: it lives at git tag **`full-engine-v1`**.

```bash
git show full-engine-v1 --stat              # what it contained
git log full-engine-v1 -1                   # the commit
git checkout full-engine-v1 -- app/         # restore it into the working tree
git checkout full-engine-v1 -- app/ tests/  # (or just the parts you want)
```

## What's in here

| Document | Contents |
|---|---|
| `PROJECT_STATUS.md` | Verified works-vs-stubbed map, metrics, and the source-access investigation (why eBay/PriceCharting/130point are unusable) |
| `DECISIONS.md` | Why each design choice was made, alternatives rejected, interview Q&A |
| `WORKED_EXAMPLE.md` | One listing traced end-to-end; the two real bugs the trace exposed |
| `copilotinstructions.md` | Original principles, tech stack, phased roadmap |
| `DEVELOPER.md` | Module-by-module reference for the engine |

## Why it was simplified

The engine's whole premise is *comp-driven valuation* — pricing against completed
sales rather than other people's asking prices. That premise hit a wall: no free,
reachable source exposes real sold data (eBay gates it behind sign-in and a
CAPTCHA; PriceCharting and 130point sit behind Cloudflare; Swappa shows asking
prices only). The details are in `PROJECT_STATUS.md` §5.

Two weaker fallbacks were built and tested — inferring sales from a listing's
*disappearance*, and anchoring to **new-retail** prices (`FMV = retail ×
M_condition`, capped below `ALERT_IMMEDIATE`). Both are honest and useful, but
neither is a completed sale, so the alert-generation layer was making confident
noise from soft inputs.

Meanwhile the parts that were unambiguously working — **scraping Kijiji and
Craigslist, and looking at what came back** — were buried under that machinery.
So the working project was cut back to the part that earns its keep: collect
listings, show them, and let a human decide. The archived engine is preserved in
full for reference and portfolio purposes.

## Two ideas worth carrying forward

Even in a simplified project, two designs here are worth remembering:

- **Provenance over precision.** Every valuation carried a `basis` —
  `sold_comps`, `retail`, or `inferred` — with different confidence caps, so
  weaker evidence could never masquerade as a completed sale. The failure mode of
  a valuation system is not being wrong; it's being *silently* wrong.
- **Detect junk before pricing it.** `listing_guard` rejects accessories, lots,
  and "X for PS5" listings before any number is computed. A GPU power cable that
  merely *mentions* an RTX 4090 was once valued at $1,893.
