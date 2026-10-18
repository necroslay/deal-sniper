"""Tests for eval/predictors.py — the Predictor interface and rule baselines."""

from __future__ import annotations

from app.config import CFG
from app.eval.predictors import (KEEP, REJECT, GuardBaseline, Predictor,
                                 ResolverBaseline, available_predictors)


class TestResolverBaseline:
    def test_known_product_resolves(self):
        assert ResolverBaseline().predict(
            ["EVGA GeForce RTX 3080 FTW3"]) == ["RTX 3080"]

    def test_unknown_title_maps_to_unknown_label(self):
        assert ResolverBaseline().predict(
            ["Wooden desk"]) == [CFG.ml.unknown_label]

    def test_unknown_label_is_configurable(self):
        assert ResolverBaseline(unknown_label="???").predict(
            ["Wooden desk"]) == ["???"]

    def test_exposes_no_probabilities(self):
        assert ResolverBaseline().predict_proba(["RTX 3080"]) is None


class TestGuardBaseline:
    def test_real_listing_is_kept(self):
        assert GuardBaseline().predict(
            ["EVGA GeForce RTX 3080 FTW3"]) == [KEEP]

    def test_accessory_is_rejected(self):
        assert GuardBaseline().predict(
            ["RTX 4090 water block cooler"]) == [REJECT]

    def test_multi_item_lot_is_rejected(self):
        assert GuardBaseline().predict(
            ["RTX 3060 Ti, RTX 3070, RTX3080 for sale"]) == [REJECT]


class TestProtocolAndRegistry:
    def test_baselines_satisfy_the_protocol(self):
        assert isinstance(ResolverBaseline(), Predictor)
        assert isinstance(GuardBaseline(), Predictor)

    def test_registry_includes_baselines_and_phase1b_stub(self):
        import app.ml.embeddings  # noqa: F401  (import registers the stub)

        names = set(available_predictors())
        assert {"resolver-baseline", "guard-baseline", "embedding-knn"} <= names

    def test_registry_hands_out_a_copy(self):
        assert available_predictors() is not available_predictors()
