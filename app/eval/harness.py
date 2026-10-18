"""Run predictors over a frozen test split and report honest metrics.

Usage::

    python -m app.eval.harness --task resolver
    python -m app.eval.harness --task guard --labels data/labels/guard.csv
    python -m app.eval.harness --task resolver --check-baseline

Writes ``reports/<task>_eval.md`` (for humans) and ``reports/<task>_metrics.json``
(for CI). ``--check-baseline`` compares the fresh numbers against the committed
JSON and exits non-zero on a regression, which is what turns the report from a
number into a gate.

The ML model is optional: if scikit-learn is not installed the harness still
scores the deterministic baselines, so it runs anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from app.config import CFG
from app.eval import metrics
from app.eval.predictors import REJECT, ResolverBaseline, GuardBaseline
from app.ml import dataset as ds


@dataclass
class EvalResult:
    """The scored outcome of one predictor on one task."""

    predictor: str
    task: str
    n: int
    accuracy: float
    macro_f1: float
    per_class: dict[str, dict]
    labels: list[str]
    matrix: list[list[int]]
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "predictor": self.predictor,
            "task": self.task,
            "n": self.n,
            "accuracy": self.accuracy,
            "macro_f1": self.macro_f1,
            "per_class": self.per_class,
            "labels": self.labels,
            "matrix": self.matrix,
            "extras": self.extras,
        }


def evaluate(predictor, texts: Sequence[str], labels: Sequence[str], task: str,
             label_map: Optional[dict[str, str]] = None,
             positive_label: str = REJECT) -> EvalResult:
    """Score *predictor* on (*texts*, *labels*), returning an ``EvalResult``.

    Args:
        label_map: Optional collapsing of rare labels to OTHER, applied to both
            the true labels and the predictions so the comparison stays fair.
        positive_label: The class the guard's PR curve treats as positive.
    """
    predictions = list(predictor.predict(list(texts)))
    y_true = [label_map.get(l, l) for l in labels] if label_map else list(labels)
    y_pred = [label_map.get(p, p) for p in predictions] if label_map else predictions

    classes, matrix = metrics.confusion_matrix(y_true, y_pred)
    per_class = metrics.per_class_prf(y_true, y_pred, classes)

    result = EvalResult(
        predictor=getattr(predictor, "name", "predictor"),
        task=task,
        n=len(y_true),
        accuracy=metrics.accuracy(y_true, y_pred),
        macro_f1=metrics.macro_f1(y_true, y_pred, classes),
        per_class=per_class,
        labels=classes,
        matrix=matrix.tolist(),
    )

    if task == "guard":
        _add_precision_at_recall(result, predictor, texts, y_true, positive_label)
    return result


def _add_precision_at_recall(result: EvalResult, predictor, texts, y_true,
                             positive_label: str) -> None:
    """If the predictor exposes probabilities, score it at the target recall."""
    proba = predictor.predict_proba(list(texts))
    classes = getattr(predictor, "classes", None)
    if not proba or not classes or positive_label not in classes:
        return
    column = classes.index(positive_label)
    scores = [row[column] for row in proba]
    result.extras["precision_at_recall"] = metrics.precision_at_recall(
        y_true, scores, positive_label, CFG.ml.guard_target_recall)


def render_report(results: Sequence[EvalResult], title: str,
                  notes: str = "") -> str:
    """Render a comparison table plus a per-class breakdown per predictor."""
    lines = [f"# {title}", ""]
    if notes:
        lines += [notes, ""]

    lines += ["## Comparison", "",
              "| Predictor | n | Accuracy | Macro F1 |",
              "|---|---|---|---|"]
    for result in results:
        lines.append(f"| `{result.predictor}` | {result.n} | "
                     f"{result.accuracy:.3f} | {result.macro_f1:.3f} |")
    lines.append("")

    for result in results:
        lines.append("---")
        lines.append(metrics.classification_report_markdown(
            result.per_class, result.accuracy, result.macro_f1,
            title=f"`{result.predictor}`"))
        pr = result.extras.get("precision_at_recall")
        if pr:
            lines += [
                f"- Precision@recall>={CFG.ml.guard_target_recall:.2f}: "
                f"**{pr['precision']:.3f}** at threshold "
                f"`{pr['threshold']:.4f}` (recall {pr['recall']:.3f}, "
                f"target reached: {pr['achieved']})", ""]
    return "\n".join(lines)


def write_reports(results: Sequence[EvalResult], task: str,
                  reports_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """Write the markdown report and the machine-readable metrics JSON."""
    reports_dir = Path(reports_dir or CFG.ml.reports_dir)
    reports_dir.mkdir(parents=True, exist_ok=True)

    title = {"resolver": "Product resolver — baseline vs model",
             "guard": "Listing guard — baseline vs model"}.get(task, task)
    md_path = reports_dir / f"{task}_eval.md"
    md_path.write_text(render_report(results, title), encoding="utf-8")

    json_path = reports_dir / f"{task}_metrics.json"
    json_path.write_text(
        json.dumps([r.to_dict() for r in results], indent=2, sort_keys=True),
        encoding="utf-8")
    return md_path, json_path


def check_baseline(current: Sequence[dict], baseline: Sequence[dict],
                   tolerance: float = 0.0) -> list[str]:
    """Return regression messages where a predictor's macro F1 dropped.

    A predictor present in the baseline but missing from *current* is itself a
    regression — silently dropping a model from the report must not pass CI.
    """
    by_name = {r["predictor"]: r for r in baseline}
    current_by_name = {r["predictor"]: r for r in current}
    regressions: list[str] = []
    for name, base in by_name.items():
        now = current_by_name.get(name)
        if now is None:
            regressions.append(f"{name}: missing from this run")
            continue
        if now["macro_f1"] < base["macro_f1"] - tolerance:
            regressions.append(
                f"{name}: macro F1 {now['macro_f1']:.3f} "
                f"< baseline {base['macro_f1']:.3f}")
    return regressions


# ── Experiments ─────────────────────────────────────────────────────────────
# The model is imported lazily so this module (and its tests) still work when
# scikit-learn is not installed: the baselines are always scored.


def _texts(examples: Sequence[ds.LabeledExample]) -> list[str]:
    return [e.text for e in examples]


def _labels(examples: Sequence[ds.LabeledExample]) -> list[str]:
    return [e.label for e in examples]


def _split(examples, seed):
    splits = ds.split_dataset(examples, seed)
    return splits["train"], splits["val"] + splits["test"]


def _build_resolver_model(train_examples):
    """Train the TF-IDF resolver, or return None if sklearn is absent."""
    try:
        from app.ml.tfidf_model import TfidfPredictor
    except ImportError:
        return None
    return TfidfPredictor.build(seed=CFG.ml.seed).fit(
        _texts(train_examples), _labels(train_examples))


def _build_guard_model(train_examples):
    try:
        from app.ml.guard_model import GuardModel
    except ImportError:
        return None
    return GuardModel.build(seed=CFG.ml.seed).fit(
        _texts(train_examples), _labels(train_examples))


def run_resolver_experiment(examples, seed: Optional[int] = None) -> list[EvalResult]:
    """Score the regex baseline, the TF-IDF model and their hybrid."""
    seed = CFG.ml.seed if seed is None else seed
    train, test = _split(examples, seed)

    # Rare classes are folded into OTHER using training support only; the map is
    # then applied to true labels and predictions alike, so the comparison is
    # fair and nothing leaks from val/test.
    mapping = ds.rare_label_map(train, CFG.ml.min_class_support, CFG.ml.other_label)
    test_texts, test_labels = _texts(test), _labels(test)

    results = [evaluate(ResolverBaseline(), test_texts, test_labels,
                        "resolver", label_map=mapping)]

    model = _build_resolver_model(ds.apply_label_map(train, mapping))
    if model is not None:
        results.append(evaluate(model, test_texts, test_labels,
                                "resolver", label_map=mapping))
        from app.ml.hybrid import HybridResolver
        results.append(evaluate(HybridResolver(ResolverBaseline(), model),
                                test_texts, test_labels,
                                "resolver", label_map=mapping))
    return results


def run_guard_experiment(examples, seed: Optional[int] = None) -> list[EvalResult]:
    """Score the rule guard, the learned guard and their hybrid."""
    seed = CFG.ml.seed if seed is None else seed
    train, test = _split(examples, seed)
    test_texts, test_labels = _texts(test), _labels(test)

    results = [evaluate(GuardBaseline(), test_texts, test_labels, "guard")]

    model = _build_guard_model(train)
    if model is not None:
        results.append(evaluate(model, test_texts, test_labels, "guard"))
        from app.ml.hybrid import HybridGuard
        results.append(evaluate(HybridGuard(GuardBaseline(), model),
                                test_texts, test_labels, "guard"))
    return results


LABEL_COLUMN = {"resolver": "product_label", "guard": "guard_label"}


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate resolver/guard predictors against a labeled CSV.")
    parser.add_argument("--task", choices=["resolver", "guard"], default="resolver")
    parser.add_argument("--labels", default=None,
                        help="Labeled CSV (default: data/labels/deals.csv).")
    parser.add_argument("--reports-dir", default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--check-baseline", action="store_true",
                        help="Exit 1 if macro F1 regressed vs the committed "
                             "reports/<task>_metrics.json.")
    parser.add_argument("--tolerance", type=float, default=0.0)
    args = parser.parse_args(argv)

    reports_dir = Path(args.reports_dir) if args.reports_dir else CFG.ml.reports_dir
    # One labeled file serves both tasks: it carries product_label and
    # guard_label side by side, and each task reads its own column.
    labels_path = (Path(args.labels) if args.labels
                   else CFG.ml.labels_dir / "deals.csv")

    if not labels_path.exists():
        print(f"No labeled data at {labels_path}.")
        print("Export a labeling sheet with "
              "`python -m app.ml.dataset --export <path>` and fill it in.")
        return 2

    examples = ds.load_labeled_csv(labels_path, LABEL_COLUMN[args.task])
    if not examples:
        print(f"{labels_path} has no rows with a "
              f"{LABEL_COLUMN[args.task]} value.")
        return 2

    results = (run_resolver_experiment(examples, args.seed)
               if args.task == "resolver"
               else run_guard_experiment(examples, args.seed))

    # Capture the committed baseline BEFORE overwriting the metrics file.
    metrics_path = reports_dir / f"{args.task}_metrics.json"
    baseline = None
    if args.check_baseline and metrics_path.exists():
        baseline = json.loads(metrics_path.read_text(encoding="utf-8"))

    md_path, json_path = write_reports(results, args.task, reports_dir)

    print(f"{args.task}: {len(examples)} labeled example(s)")
    for result in results:
        print(f"  {result.predictor:<20} acc={result.accuracy:.3f}  "
              f"macroF1={result.macro_f1:.3f}  (n={result.n})")
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
