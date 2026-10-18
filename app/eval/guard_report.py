"""Choose the guard's operating threshold from the cost of a false positive.

A classifier defaults to a 0.5 cut, which is a decision about nothing. For this
guard the two errors have very different prices: a false positive (junk passed
through) is how a GPU power *cable* once got valued at $1,893, while a false
negative (a real listing sent for review) merely costs a glance.

So the threshold is chosen as the **highest-precision point that still reaches a
target recall** of junk — deliberately biased toward catching junk, and the
precision it costs is reported explicitly rather than hidden.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.config import CFG
from app.eval.metrics import precision_at_recall, precision_recall_curve
from app.eval.predictors import REJECT


def choose_threshold(y_true: Sequence[str], scores: Sequence[float],
                     positive_label: str = REJECT,
                     target_recall: Optional[float] = None) -> dict:
    """Return the operating threshold plus the precision/recall it buys."""
    return precision_at_recall(
        y_true, scores, positive_label,
        target_recall if target_recall is not None else CFG.ml.guard_target_recall)


def threshold_report(y_true: Sequence[str], scores: Sequence[float],
                     positive_label: str = REJECT,
                     target_recall: Optional[float] = None) -> str:
    """Markdown explaining the chosen threshold and the tradeoff accepted."""
    target_recall = (target_recall if target_recall is not None
                     else CFG.ml.guard_target_recall)
    chosen = choose_threshold(y_true, scores, positive_label, target_recall)
    _, precision, recall = precision_recall_curve(y_true, scores, positive_label)

    lines = [
        "### Guard operating threshold", "",
        f"Chosen to reach recall >= {target_recall:.2f} with maximum precision:",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Threshold | `{chosen['threshold']:.4f}` |",
        f"| Precision | {chosen['precision']:.3f} |",
        f"| Recall | {chosen['recall']:.3f} |",
        f"| Target reached | {chosen['achieved']} |",
        "",
    ]
    if not chosen["achieved"]:
        lines += [
            f"> No threshold reached recall {target_recall:.2f}; the best "
            f"available recall was {chosen['recall']:.3f}. More labeled junk "
            "is needed before this threshold can be trusted.", ""]
    if len(recall):
        lines += [
            f"Precision range across thresholds: {precision.min():.3f} – "
            f"{precision.max():.3f}.", ""]
    return "\n".join(lines)
