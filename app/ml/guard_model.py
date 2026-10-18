"""Rule-informed binary listing guard.

The rule guard is high-precision but brittle: a phrasing its keyword list misses
slips through. Rather than deleting the rules, this model *consumes them as
features* (alongside TF-IDF text) so it learns the residual — the cases the
patterns don't catch — while the rule hits keep their strong signal.

The output is a probability of ``reject``; the operating threshold is chosen in
``app/eval/guard_report.py`` from the cost of a false positive, not from 0.5.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline

from app.engines.listing_guard import RULE_SIGNAL_NAMES, rule_signals


class RuleFeatures(BaseEstimator, TransformerMixin):
    """The guard's own pattern hits, as a dense feature block."""

    def fit(self, X, y=None) -> "RuleFeatures":
        return self

    def transform(self, X) -> np.ndarray:
        return np.array(
            [[rule_signals(text)[name] for name in RULE_SIGNAL_NAMES]
             for text in X],
            dtype=float,
        )

    def get_feature_names_out(self, input_features=None) -> np.ndarray:
        """Name the six signal columns, unprefixed.

        ``FeatureUnion`` prefixes with the transformer's own name, so returning
        bare names yields ``rules__host``, ``rules__accessory``, … rather than a
        doubled ``rules__rule__host``. This is what makes the fitted guard
        introspectable — "which signals is it actually leaning on?" — instead of
        a black box.
        """
        return np.array(list(RULE_SIGNAL_NAMES), dtype=object)


class GuardModel:
    """A ``keep``/``reject`` classifier behind the ``Predictor`` protocol."""

    name = "guard-model"

    def __init__(self, pipeline=None):
        self.pipeline = pipeline

    @classmethod
    def build(cls, seed: int = 42, max_features: int = 20000) -> "GuardModel":
        features = FeatureUnion([
            ("rules", RuleFeatures()),
            ("word", TfidfVectorizer(analyzer="word", ngram_range=(1, 2),
                                     min_df=1, sublinear_tf=True,
                                     max_features=max_features)),
            ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5),
                                     min_df=1, sublinear_tf=True,
                                     max_features=max_features)),
        ])
        classifier = LogisticRegression(max_iter=2000, class_weight="balanced",
                                        C=4.0, random_state=seed)
        return cls(Pipeline([("features", features), ("clf", classifier)]))

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "GuardModel":
        self.pipeline.fit(list(texts), list(labels))
        return self

    @property
    def classes(self) -> list[str]:
        return list(self.pipeline.named_steps["clf"].classes_)

    def predict(self, texts: Sequence[str]) -> list[str]:
        return list(self.pipeline.predict(list(texts)))

    def predict_proba(self, texts: Sequence[str]) -> Optional[list[list[float]]]:
        classifier = self.pipeline.named_steps["clf"]
        if not hasattr(classifier, "predict_proba"):
            return None
        return [list(row) for row in self.pipeline.predict_proba(list(texts))]

    def save(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as fh:
            pickle.dump(self.pipeline, fh)
        return path

    @classmethod
    def load(cls, path: str | Path) -> "GuardModel":
        with Path(path).open("rb") as fh:
            return cls(pickle.load(fh))