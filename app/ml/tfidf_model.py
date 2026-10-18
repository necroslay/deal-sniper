"""TF-IDF + linear model, exposed as a ``Predictor``.

Word n-grams capture "gaming", "founders edition"; character n-grams capture the
thing that actually matters on marketplace titles — model numbers that sellers
write every possible way ("RTX3080", "rtx-3080", "R T X 3080"). A linear model
over both is fast, needs no GPU, and is easy to explain, which is the right
first model on a small labeled set.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional, Sequence

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline


def _vectorizers(max_features: int) -> FeatureUnion:
    """Word + character TF-IDF features, unioned."""
    word = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1,
                           sublinear_tf=True, lowercase=True,
                           max_features=max_features)
    char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=1,
                           sublinear_tf=True, lowercase=True,
                           max_features=max_features)
    return FeatureUnion([("word", word), ("char", char)])


class TfidfPredictor:
    """A text classifier behind the ``Predictor`` protocol."""

    name = "tfidf-linear"

    def __init__(self, pipeline=None, name: Optional[str] = None):
        self.pipeline = pipeline
        if name:
            self.name = name

    @classmethod
    def build(cls, seed: int = 42, name: Optional[str] = None,
              max_features: int = 20000) -> "TfidfPredictor":
        """Construct an untrained model.

        ``class_weight="balanced"`` matters here: the label distribution is a
        long tail, and without it the model would ignore every rare product.
        """
        classifier = LogisticRegression(max_iter=2000, class_weight="balanced",
                                        C=4.0, random_state=seed)
        pipeline = Pipeline([("features", _vectorizers(max_features)),
                             ("clf", classifier)])
        return cls(pipeline, name)

    def fit(self, texts: Sequence[str], labels: Sequence[str]) -> "TfidfPredictor":
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
    def load(cls, path: str | Path, name: Optional[str] = None) -> "TfidfPredictor":
        with Path(path).open("rb") as fh:
            return cls(pickle.load(fh), name)
