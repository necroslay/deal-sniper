"""Hybrid predictors: rules first, model only for what the rules can't decide.

This is the deliberate design choice of the ML layer. The regex resolver's
Tier-1 match and the guard's hard rejects are kept authoritative, because they
are deterministic, auditable and free; the learned model is consulted *only*
where the rules abstain — the resolver's Tier-3 (unknown) bucket and the guard's
borderline cases. That keeps behaviour reproducible on the common path while
still recovering the long tail the rules drop.
"""

from __future__ import annotations

from typing import Optional

from app.config import CFG
from app.eval.predictors import KEEP, REJECT


def _confidences(model, texts: list[str]) -> Optional[list[float]]:
    """Max class probability per text, or None if the model exposes none."""
    proba = getattr(model, "predict_proba", lambda _t: None)(texts)
    if not proba:
        return None
    return [max(row) for row in proba]


class HybridResolver:
    """Regex resolver for known titles; model only for unresolved ones."""

    name = "hybrid-resolver"

    def __init__(self, baseline, model, unknown_label: Optional[str] = None,
                 min_confidence: Optional[float] = None):
        self.baseline = baseline
        self.model = model
        self.unknown_label = unknown_label or CFG.ml.unknown_label
        self.min_confidence = (CFG.ml.resolver_min_confidence
                               if min_confidence is None else min_confidence)

    def predict(self, texts: list[str]) -> list[str]:
        out = list(self.baseline.predict(texts))
        pending = [i for i, pred in enumerate(out) if pred == self.unknown_label]
        if not pending:
            return out

        sub_texts = [texts[i] for i in pending]
        model_preds = list(self.model.predict(sub_texts))
        confidences = _confidences(self.model, sub_texts)

        for position, (i, pred) in enumerate(zip(pending, model_preds)):
            if confidences is not None and confidences[position] < self.min_confidence:
                continue  # too unsure: leave the rule's "unknown" in place
            out[i] = pred
        return out

    def predict_proba(self, texts: list[str]):
        return None

    @property
    def classes(self):
        return getattr(self.model, "classes", None)


class HybridGuard:
    """Rule rejections stand; the model adjudicates titles the rules let pass."""

    name = "hybrid-guard"

    def __init__(self, baseline, model, reject_threshold: Optional[float] = None):
        self.baseline = baseline
        self.model = model
        self.reject_threshold = (CFG.ml.guard_reject_threshold
                                 if reject_threshold is None else reject_threshold)

    def predict(self, texts: list[str]) -> list[str]:
        out = list(self.baseline.predict(texts))
        # Only the titles the rules kept are worth a second opinion.
        pending = [i for i, pred in enumerate(out) if pred == KEEP]
        if not pending:
            return out

        sub_texts = [texts[i] for i in pending]
        model_preds = list(self.model.predict(sub_texts))
        proba = self.model.predict_proba(sub_texts)
        classes = getattr(self.model, "classes", None)

        for position, (i, pred) in enumerate(zip(pending, model_preds)):
            if pred != REJECT:
                continue
            if proba is not None and classes and REJECT in classes:
                score = proba[position][classes.index(REJECT)]
                if score < self.reject_threshold:
                    continue
            out[i] = REJECT
        return out

    def predict_proba(self, texts: list[str]):
        return self.model.predict_proba(texts)

    @property
    def classes(self):
        return getattr(self.model, "classes", None)
