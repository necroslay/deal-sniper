"""Phase 1b stub — embedding kNN predictor (registered, not implemented).

This exists so the interface and the registry are already in place when the
embedding model is built; adding it later touches only this file.

Intended approach:

1. Encode every title with a small sentence-transformer
   (e.g. ``all-MiniLM-L6-v2``) — cheap, CPU-friendly, no fine-tuning needed.
2. Keep the train-title embeddings with their labels.
3. At inference, kNN in embedding space with a distance-weighted vote, or a
   logistic probe trained on the embeddings if the label set is large enough.
4. Keep the same ``predict`` / ``predict_proba`` / ``classes`` surface, so the
   harness and ``app.engines.enrich`` need no changes at all.

Why it is deferred: it adds a heavyweight dependency and buys accuracy mainly on
paraphrase-heavy titles, which the char n-gram model already handles reasonably.
It is worth building once the labeled set is large enough to justify it — the
active-learning loop in ``app.ml.active_learning`` is what grows that set.
"""

from __future__ import annotations

from typing import Optional, Sequence

from app.eval.predictors import register_predictor

_DEFERRED = (
    "EmbeddingKNNPredictor is a Phase 1b stub. It is registered so the registry "
    "and interface are testable, but not implemented yet. See app/ml/embeddings.py "
    "for the intended design (sentence-transformers + kNN)."
)


@register_predictor("embedding-knn")
class EmbeddingKNNPredictor:
    """Registered placeholder for the Phase 1b embedding model."""

    name = "embedding-knn"
    implemented = False

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "EmbeddingKNNPredictor":
        raise NotImplementedError(_DEFERRED)

    def predict(self, texts: Sequence[str]) -> list[str]:
        raise NotImplementedError(_DEFERRED)

    def predict_proba(self, texts: Sequence[str]) -> Optional[list[list[float]]]:
        raise NotImplementedError(_DEFERRED)