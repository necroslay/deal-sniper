"""Evaluate the price models on a *temporal* split, and gate on regression.

Usage::

    python -m app.eval.price_harness
    python -m app.eval.price_harness --check-baseline

Trains on the oldest listings and tests on the newest, which is how the model is
actually used: predict on newly-posted listings from older history. Everything
here is *asking-price* modelling — "underpriced relative to comparable live
listings" — never a claim about market value.

Metrics are regression metrics (MAE, pinball loss per quantile) plus interval
calibration (does the P25-P75 band actually contain ~50% of outcomes?), and the
effect of the new-retail ceiling is reported separately so a business rule can't
masquerade as model quality.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from app.config import CFG
from app.db.store import Store
from app.eval import metrics
from app.ml import features as feat
from app.ml import retail as retail_mod

QUANTILE_LABELS = ("p25", "p50", "p75")


@dataclass
class PriceEval:
    """Scores for one predictor on the held-out (newest) listings."""

    predictor: str
    n: int
    mae: float
    mae_after_ceiling: float
    pinball: dict[str, float] = field(default_factory=dict)
    coverage: float = 0.0
    capped: int = 0

    def to_dict(self) -> dict:
        return {
            "predictor": self.predictor,
            "n": self.n,
            "mae": self.mae,
            "mae_after_ceiling": self.mae_after_ceiling,
            "pinball": self.pinball,
            "coverage": self.coverage,
            "capped": self.capped,
        }


def cap_at_retail(low, mid, high, rows, retail):
    """Apply a per-product retail ceiling to a predicted band.

    Returns ``(low, mid, high, n_capped)``. Capping can invert the quantile order
    (all three above retail collapse together), so each row's trio is re-sorted.
    """
    capped = 0
    lo_out, mid_out, hi_out = [], [], []
    for index, row in enumerate(rows):
        ceiling = retail_mod.retail_for(retail, row.product)
        if ceiling is None:
            lo_out.append(low[index])
            mid_out.append(mid[index])
            hi_out.append(high[index])
            continue
        capped += 1
        trio = sorted((min(low[index], ceiling), min(mid[index], ceiling),
                       min(high[index], ceiling)))
        lo_out.append(trio[0])
        mid_out.append(trio[1])
        hi_out.append(trio[2])
    return (np.array(lo_out, dtype=float), np.array(mid_out, dtype=float),
            np.array(hi_out, dtype=float), capped)


def evaluate_predictor(predictor, rows: Sequence[feat.PriceRow], retail) -> PriceEval:
    """Score one predictor's band against the actual asking prices in *rows*."""
    low, mid, high = predictor.predict_band(rows)
    truth = np.array([r.price_cad for r in rows], dtype=float)

    capped_low, capped_mid, _, n_capped = cap_at_retail(low, mid, high, rows,
                                                        retail)
    return PriceEval(
        predictor=getattr(predictor, "name", "predictor"),
        n=len(rows),
        mae=metrics.mae(truth, mid),
        mae_after_ceiling=metrics.mae(truth, capped_mid),
        pinball={label: metrics.pinball_loss(truth, band, q)
                 for label, band, q in zip(QUANTILE_LABELS,
                                           (low, mid, high),
                                           predictor.quantiles)},
        coverage=metrics.interval_coverage(truth, low, high),
        capped=n_capped,
    )


# ── Experiment ──────────────────────────────────────────────────────────────


def run_price_experiment(rows: list[feat.PriceRow], retail,
                         test_fraction: Optional[float] = None,
                         with_model: bool = True
                         ) -> tuple[list[PriceEval], list[feat.PriceRow],
                                    list[feat.PriceRow], str]:
    """Fit on the older listings, score on the newer ones.

    Returns ``(results, train, test, note)`` where *note* explains a skipped
    model (e.g. LightGBM not installed) so the report can say so honestly.
    """
    from app.ml.price_model import ProductQuantileBaseline, QuantilePriceModel

    fraction = (CFG.ml.price_test_fraction if test_fraction is None
                else test_fraction)
    train, test = feat.temporal_split(rows, fraction)
    if not train or not test:
        return [], train, test, "not enough rows to split"

    results = [evaluate_predictor(ProductQuantileBaseline().fit(train),
                                  test, retail)]

    note = ""
    if with_model:
        try:
            model = QuantilePriceModel(seed=CFG.ml.seed).fit(train)
        except ImportError:
            note = "LightGBM not installed — scored the baseline only."
        else:
            results.append(evaluate_predictor(model, test, retail))
    return results, train, test, note


def render_report(results: Sequence[PriceEval], train, test, note: str) -> str:
    """Markdown comparison plus the split and ceiling context."""
    lines = [
        "# Price model — temporal evaluation", "",
        "Predicting the **asking-price** distribution of comparable live "
        "listings (not market value).", "",
        "## Split", "",
        f"- **Train** on the {len(train)} oldest listings "
        f"(up to `{train[-1].posted_at[:10]}`).",
        f"- **Test** on the {len(test)} newest listings "
        f"(from `{test[0].posted_at[:10]}`).",
        "- Split by `date_posted`, so a listing can never appear on both sides.",
        "",
    ]
    if note:
        lines += [f"> {note}", ""]

    lines += ["## Comparison", "",
              "| Predictor | n | MAE (P50) | MAE after ceiling | "
              "Pinball P25 / P50 / P75 | P25–P75 coverage | Capped |",
              "|---|---|---|---|---|---|---|"]
    for result in results:
        pin = result.pinball
        lines.append(
            f"| `{result.predictor}` | {result.n} | {result.mae:,.0f} | "
            f"{result.mae_after_ceiling:,.0f} | "
            f"{pin.get('p25', 0):,.0f} / {pin.get('p50', 0):,.0f} / "
            f"{pin.get('p75', 0):,.0f} | {result.coverage:.2f} | "
            f"{result.capped} |")
    lines += [
        "",
        "Lower MAE / pinball is better. Coverage is the share of test asking "
        "prices inside the P25–P75 band; a well-calibrated band should sit near "
        "**0.50**. `Capped` counts test rows with a new-retail reference whose "
        "band was clamped at that price.", "",
    ]
    return "\n".join(lines)


def write_reports(results: Sequence[PriceEval], train, test, note: str,
                  reports_dir: Optional[Path] = None) -> tuple[Path, Path]:
    reports_dir = Path(reports_dir or CFG.ml.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = reports_dir / "price_eval.md"
    md_path.write_text(render_report(results, train, test, note),
                       encoding="utf-8")
    json_path = reports_dir / "price_metrics.json"
    json_path.write_text(
        json.dumps([r.to_dict() for r in results], indent=2, sort_keys=True),
        encoding="utf-8")
    return md_path, json_path


def check_baseline(current: Sequence[dict], baseline: Sequence[dict],
                   tolerance: float = 0.0) -> list[str]:
    """Return regressions, where MAE rose above the committed baseline."""
    by_name = {r["predictor"]: r for r in baseline}
    current_by_name = {r["predictor"]: r for r in current}
    regressions: list[str] = []
    for name, base in by_name.items():
        now = current_by_name.get(name)
        if now is None:
            regressions.append(f"{name}: missing from this run")
            continue
        if now["mae"] > base["mae"] + tolerance:
            regressions.append(
                f"{name}: MAE {now['mae']:,.0f} > baseline {base['mae']:,.0f}")
    return regressions


def _load_rows(db: Optional[str]):
    store = Store(**({"db": db} if db else {})).connect()
    try:
        return store.all_listings()
    finally:
        store.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the price models on a temporal split.")
    parser.add_argument("--db", default=None,
                        help="SQLite database (default: app/listings.db).")
    parser.add_argument("--reports-dir", default=None)
    parser.add_argument("--retail", default=None,
                        help="Retail CSV (default: data/retail_prices.csv).")
    parser.add_argument("--test-fraction", type=float, default=None)
    parser.add_argument("--no-model", action="store_true",
                        help="Score the baseline only.")
    parser.add_argument("--check-baseline", action="store_true",
                        help="Exit 1 if MAE regressed vs the committed JSON.")
    parser.add_argument("--tolerance", type=float, default=0.0)
    args = parser.parse_args(argv)

    rows = feat.build_price_rows(_load_rows(args.db))
    if not rows:
        print("No priceable listings — scrape some first "
              "(`python -m app.scrape`).")
        return 2
    rows = feat.trim_outliers(rows)

    retail_path = Path(args.retail) if args.retail else CFG.ml.retail_path
    retail = retail_mod.load_retail(retail_path)

    results, train, test, note = run_price_experiment(
        rows, retail, args.test_fraction, with_model=not args.no_model)
    if not results:
        print(f"Not enough rows to evaluate ({len(rows)} usable).")
        return 2

    reports_dir = (Path(args.reports_dir) if args.reports_dir
                   else CFG.ml.reports_dir)
    metrics_path = reports_dir / "price_metrics.json"
    baseline = None
    if args.check_baseline and metrics_path.exists():
        baseline = json.loads(metrics_path.read_text(encoding="utf-8"))

    md_path, json_path = write_reports(results, train, test, note, reports_dir)

    print(f"{len(rows)} usable listings after re-resolving and outlier trim")
    print(f"retail references: {len(retail)}"
          + (f" (from {retail_path.name})" if retail else " (none — ceiling off)"))
    print(f"train {len(train)}  ->  test {len(test)}   "
          f"(test from {test[0].posted_at[:10]})")
    for result in results:
        print(f"  {result.predictor:<28} MAE={result.mae:>9,.0f}  "
              f"after-ceiling={result.mae_after_ceiling:>9,.0f}  "
              f"coverage={result.coverage:.2f}  capped={result.capped}")
    print(f"wrote {md_path} and {json_path}")

    if not args.check_baseline:
        return 0
    if baseline is None:
        print("No committed baseline yet — this run becomes one.")
        return 0
    current = json.loads(json_path.read_text(encoding="utf-8"))
    regressions = check_baseline(current, baseline, args.tolerance)
    if regressions:
        print("REGRESSION against baseline:")
        for line in regressions:
            print(f"  - {line}")
        return 1
    print("No regressions against baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
