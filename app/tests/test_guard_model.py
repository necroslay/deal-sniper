"""Tests for ml/guard_model.py — the rule-informed binary guard.

Skipped automatically when scikit-learn is not installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("sklearn")

from app.engines.listing_guard import RULE_SIGNAL_NAMES  # noqa: E402
from app.ml.guard_model import GuardModel, RuleFeatures  # noqa: E402

TRAIN = [
    ("EVGA GeForce RTX 3080 FTW3 Ultra Gaming", "keep"),
    ("ASUS RTX 4090 TUF Gaming OC Edition", "keep"),
    ("PS5 Disc Version - Like New", "keep"),
    ("Nintendo Switch OLED White", "keep"),
    ("RTX 4090 water block cooler", "reject"),
    ("GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090", "reject"),
    ("RTX 3060 Ti, RTX 3070, RTX3080 for sale", "reject"),
    ("Soul Hackers 2 for PS5 (brand new sealed)", "reject"),
    ("Alienware Laptop Ryzen 7 5800H RTX 3060", "reject"),
]


class TestRuleFeatures:
    def test_output_shape_matches_the_signal_contract(self):
        features = RuleFeatures().transform(["a", "b c d"])
        assert features.shape == (2, len(RULE_SIGNAL_NAMES))

    def test_accessory_signal_fires(self):
        row = RuleFeatures().transform(["RTX 4090 water block cooler"])[0]
        assert row[RULE_SIGNAL_NAMES.index("accessory")] == 1.0

    def test_product_count_counts_distinct_products(self):
        row = RuleFeatures().transform(["RTX 3060 Ti, RTX 3070, RTX3080"])[0]
        assert row[RULE_SIGNAL_NAMES.index("product_count")] >= 2

    def test_feature_names_are_exposed(self):
        """Needed so the enclosing FeatureUnion can report feature names.

        Names are returned unprefixed because FeatureUnion adds the transformer
        name, yielding ``rules__host`` rather than a doubled prefix.
        """
        assert list(RuleFeatures().get_feature_names_out()) == list(
            RULE_SIGNAL_NAMES)


class TestGuardModel:
    def _fit(self) -> GuardModel:
        texts = [text for text, _ in TRAIN]
        labels = [label for _, label in TRAIN]
        return GuardModel.build(seed=1).fit(texts, labels)

    def test_classes_are_keep_and_reject(self):
        assert set(self._fit().classes) == {"keep", "reject"}

    def test_rejects_an_accessory(self):
        assert self._fit().predict(["RTX 4090 water block cooler"]) == ["reject"]

    def test_keeps_a_real_listing(self):
        assert self._fit().predict(
            ["EVGA GeForce RTX 3080 FTW3 Ultra Gaming"]) == ["keep"]

    def test_probabilities_are_available(self):
        proba = self._fit().predict_proba(["RTX 4090 water block cooler"])
        assert proba is not None
        assert sum(proba[0]) == pytest.approx(1.0, abs=1e-6)

    def test_save_and_load_roundtrip(self, tmp_path):
        model = self._fit()
        path = model.save(tmp_path / "guard.pkl")
        reloaded = GuardModel.load(path)
        titles = ["RTX 4090 water block cooler"]
        assert reloaded.predict(titles) == model.predict(titles)
