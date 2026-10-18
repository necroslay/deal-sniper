"""Classification metrics, implemented locally on numpy.

Kept dependency-light on purpose: the harness must be runnable on a machine
without the ML stack installed, so these never import scikit-learn. The maths is
standard and small enough to own:

* macro-averaged F1 treats every class equally, which is what catches the
  long-tail products a rules engine silently drops into its fallback bucket.
* ``precision_at_recall`` picks an operating threshold from a *business* target
  rather than the arbitrary 0.5 a classifier defaults to.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np


def _resolve_labels(y_true, y_pred, labels: Optional[Sequence[str]]) -> list[str]:
    if labels is not None:
        return list(labels)
    return sorted(set(y_true) | set(y_pred))


def confusion_matrix(y_true: Sequence[str], y_pred: Sequence[str],
                     labels: Optional[Sequence[str]] = None) -> tuple[list[str], np.ndarray]:
    """Return ``(labels, matrix)`` where ``matrix[i, j]`` counts true i, pred j.

    Raises:
        ValueError: if a value is outside the declared *labels* — silently
            dropping it would inflate the accuracy of a mismatched split.
    """
    labels = _resolve_labels(y_true, y_pred, labels)
    index = {label: i for i, label in enumerate(labels)}
    matrix = np.zeros((len(labels), len(labels)), dtype=int)
    for true, pred in zip(y_true, y_pred):
        if true not in index or pred not in index:
            raise ValueError(
                f"label outside the declared set: true={true!r} pred={pred!r}")
        matrix[index[true], index[pred]] += 1
    return labels, matrix


def per_class_prf(y_true: Sequence[str], y_pred: Sequence[str],
                  labels: Optional[Sequence[str]] = None) -> dict[str, dict]:
    """Precision/recall/F1/support per class, from the confusion matrix."""
    labels, matrix = confusion_matrix(y_true, y_pred, labels)
    out: dict[str, dict] = {}
    for i, label in enumerate(labels):
        tp = int(matrix[i, i])
        fp = int(matrix[:, i].sum() - tp)
        fn = int(matrix[i, :].sum() - tp)
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = (2 * precision * recall / (precision + recall)
              if (precision + recall) else 0.0)
        out[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": int(matrix[i, :].sum()),
        }
    return out


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str],
             labels: Optional[Sequence[str]] = None) -> float:
    """Unweighted mean F1 over the classes that actually occur in *y_true*.

    A class with support 0 — one that only ever appears as a *spurious
    prediction* — is deliberately excluded. It is still penalised through the
    precision of the classes it stole predictions from, but scoring it as a
    separate hard zero makes macro F1 unstable and understates real accuracy:
    a single phantom label could otherwise swing the whole metric.
    """
    prf = per_class_prf(y_true, y_pred, labels)
    scored = [v["f1"] for v in prf.values() if v["support"] > 0]
    if not scored:
        return 0.0
    return float(np.mean(scored))


def accuracy(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    """Fraction of exact matches (0.0 for an empty input)."""
    if not len(y_true):
        return 0.0
    return float(np.mean([t == p for t, p in zip(y_true, y_pred)]))


def precision_recall_curve(y_true: Sequence[str], y_scores: Sequence[float],
                           positive_label: str = "reject"
                           ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return ``(thresholds, precision, recall)`` for a binary positive class.

    Points are in descending score order, so ``thresholds[i]`` is the score at
    which predicting everything up to ``i`` positive yields ``precision[i]`` /
    ``recall[i]``. Precision is guarded against division by zero.
    """
    scores = np.asarray(y_scores, dtype=float)
    is_positive = np.asarray([t == positive_label for t in y_true])

    order = np.argsort(-scores, kind="stable")
    scores, is_positive = scores[order], is_positive[order]

    tp = np.cumsum(is_positive)
    fp = np.cumsum(~is_positive)
    total_pos = int(is_positive.sum())

    precision = tp / np.maximum(tp + fp, 1)
    if total_pos:
        recall = tp / total_pos
    else:
        recall = np.zeros_like(tp, dtype=float)
    return scores, precision, recall


def precision_at_recall(y_true: Sequence[str], y_scores: Sequence[float],
                        positive_label: str = "reject",
                        target_recall: float = 0.95) -> dict:
    """Highest-precision threshold that still reaches *target_recall*.

    Returns ``{threshold, precision, recall, achieved}``. ``achieved`` is False
    when no threshold reaches the target, in which case the best recall found is
    reported and the threshold is set above the highest score (predict nothing).
    """
    scores, precision, recall = precision_recall_curve(
        y_true, y_scores, positive_label)
    if not len(scores):
        return {"threshold": 1.0, "precision": 0.0, "recall": 0.0,
                "achieved": False}

    reachable = np.where(recall >= target_recall)[0]
    if len(reachable):
        # Lowest index = fewest positives = highest precision at that recall.
        i = int(reachable[0])
        return {"threshold": float(scores[i]),
                "precision": float(precision[i]),
                "recall": float(recall[i]),
                "achieved": True}

    best = int(np.argmax(recall))
    return {"threshold": float(scores[0]) + 1e-9,
            "precision": float(precision[best]),
            "recall": float(recall[best]),
            "achieved": False}


# ── Regression / probabilistic forecast metrics ─────────────────────────────


def mae(y_true: Sequence[float], y_pred: Sequence[float]) -> float:
    """Mean absolute error, in the units of the target (CAD here)."""
    if not len(y_true):
        return 0.0
    truth = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(truth - pred)))


def pinball_loss(y_true: Sequence[float], y_pred: Sequence[float],
                 quantile: float) -> float:
    """Quantile ("pinball") loss for one quantile forecast.

    This is the loss a quantile model actually minimises, and unlike MAE it
    scores a P25 forecast for being too high just as hard as for being too low.
    """
    if not len(y_true):
        return 0.0
    truth = np.asarray(y_true, dtype=float)
    pred = np.asarray(y_pred, dtype=float)
    diff = truth - pred
    return float(np.mean(np.maximum(quantile * diff,
                                    (quantile - 1.0) * diff)))


def interval_coverage(y_true: Sequence[float], lower: Sequence[float],
                      upper: Sequence[float]) -> float:
    """Fraction of truths falling inside ``[lower, upper]``.

    A well-calibrated P25-P75 band should contain about 50% of outcomes; much
    less means the band is over-confident regardless of how good the median is.
    """
    if not len(y_true):
        return 0.0
    truth = np.asarray(y_true, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    return float(np.mean((truth >= lo) & (truth <= hi)))


def classification_report_markdown(per_class: dict[str, dict],
                                   accuracy_score: float,
                                   macro: float,
                                   title: str = "Classification report") -> str:
    """Render a per-class report as a markdown table."""
    lines = [f"### {title}", "",
             f"- Accuracy: **{accuracy_score:.3f}**",
             f"- Macro F1: **{macro:.3f}**", "",
             "| Label | Precision | Recall | F1 | Support |",
             "|---|---|---|---|---|"]
    for label, m in sorted(per_class.items(),
                           key=lambda kv: (-kv[1]["support"], kv[0])):
        lines.append(
            f"| `{label}` | {m['precision']:.3f} | {m['recall']:.3f} | "
            f"{m['f1']:.3f} | {m['support']} |")
    lines.append("")
    return "\n".join(lines)
