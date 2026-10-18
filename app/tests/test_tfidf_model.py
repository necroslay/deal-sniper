"""Tests for ml/tfidf_model.py.

Skipped automatically when scikit-learn is not installed, since the harness and
the rest of the suite must still run without the ML stack.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.ml.tfidf_model import TfidfPredictor  # noqa: E402

TRAIN = [
    ("EVGA GeForce RTX 3080 FTW3 Ultra", "RTX 3080"),
    ("ASUS RTX 3080 TUF Gaming", "RTX 3080"),
    ("NVIDIA GeForce RTX 4090 Founders Edition", "RTX 4090"),
    ("Zotac RTX 4090 Trinity OC", "RTX 4090"),
    ("PS5 Disc Version Like New", "PlayStation 5"),
    ("Sony PlayStation 5 Digital Edition", "PlayStation 5"),
    ("Wooden desk pickup only", "__none__"),
    ("Fridge for sale works great", "__none__"),
]


def _fit() -> TfidfPredictor:
    texts = [text for text, _ in TRAIN]
    labels = [label for _, label in TRAIN]
    return TfidfPredictor.build(seed=1).fit(texts, labels)


class TestTfidfPredictor:
    def test_learns_a_training_title(self):
        assert _fit().predict(["EVGA GeForce RTX 3080 FTW3 Ultra"]) == ["RTX 3080"]

    def test_classes_are_exposed(self):
        assert set(_fit().classes) == {
            "RTX 3080", "RTX 4090", "PlayStation 5", "__none__"}

    def test_probabilities_sum_to_one(self):
        proba = _fit().predict_proba(["ASUS RTX 3080 TUF Gaming"])
        assert proba is not None
        assert sum(proba[0]) == pytest.approx(1.0, abs=1e-6)

    def test_save_and_load_roundtrip(self, tmp_path):
        model = _fit()
        path = model.save(tmp_path / "resolver.pkl")
        reloaded = TfidfPredictor.load(path)
        titles = ["NVIDIA GeForce RTX 4090 Founders Edition"]
        assert reloaded.predict(titles) == model.predict(titles)
        assert reloaded.classes == model.classes
