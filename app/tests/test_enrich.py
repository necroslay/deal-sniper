"""Tests for engines/enrich.py — rules first, models only for the gaps."""

from __future__ import annotations

import pytest

from app.engines import enrich
from app.engines.enrich import resolve_and_assess
from app.engines.listing_guard import assess_listing
from app.engines.resolver import resolve_product

TITLES = [
    "EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB",
    "RTX 4090 water block cooler",
    "RTX 3060 Ti, RTX 3070, RTX3080 for sale",
    "Wooden desk",
    "PS5 Disc Version - Like New",
]


class _FakeResolver:
    def __init__(self, label="RTX 4090", confidence=0.95):
        self.label = label
        self.confidence = confidence
        self.classes = ["RTX 4090"]
        self.calls = 0

    def predict(self, texts):
        self.calls += 1
        return [self.label for _ in texts]

    def predict_proba(self, texts):
        return [[self.confidence] for _ in texts]


class _FakeGuard:
    classes = ["keep", "reject"]

    def __init__(self, confidence=0.9):
        self.confidence = confidence

    def predict(self, texts):
        return ["reject" for _ in texts]

    def predict_proba(self, texts):
        return [[1 - self.confidence, self.confidence] for _ in texts]


class TestRulesOnly:
    @pytest.mark.parametrize("title", TITLES)
    def test_matches_the_rule_engine_when_ml_is_off(self, title):
        """ML disabled (the default) must reproduce the pre-ML behaviour."""
        product, flag = resolve_and_assess(title)
        expected = resolve_product(title)
        assert product.standard_name == expected.standard_name
        assert product.category == expected.category
        assert flag == assess_listing(title, expected)

    def test_gaming_laptop_is_a_laptop_not_a_stray_gpu(self):
        """A GPU named inside a laptop title prices against laptops, unflagged."""
        title = "Alienware Laptop Ryzen 7 5800H 16GB RTX 3060"
        product, flag = resolve_and_assess(title)
        assert (product.standard_name, product.category) == ("Laptop RTX 3060", "laptop")
        assert flag is None

    def test_accessory_is_flagged(self):
        _, flag = resolve_and_assess("RTX 4090 water block cooler")
        assert flag == "accessory_or_part"


class TestModelGapFilling:
    def test_model_resolves_an_unknown_title(self):
        enrich._LOADED = True
        enrich._RESOLVER_MODEL = _FakeResolver()
        product, _ = resolve_and_assess("Mystery 3D accelerator card")
        assert product.standard_name == "RTX 4090"
        assert product.category == "gpu"

    def test_known_title_never_consults_the_model(self):
        model = _FakeResolver()
        enrich._LOADED = True
        enrich._RESOLVER_MODEL = model
        product, _ = resolve_and_assess("EVGA RTX 3080 FTW3")
        assert product.standard_name == "RTX 3080"
        assert model.calls == 0

    def test_low_confidence_model_leaves_the_title_unknown(self):
        enrich._LOADED = True
        enrich._RESOLVER_MODEL = _FakeResolver(confidence=0.1)
        product, _ = resolve_and_assess("Mystery 3D accelerator card")
        assert product.category == "unknown"

    def test_model_guard_flags_a_listing_the_rules_kept(self):
        enrich._LOADED = True
        enrich._GUARD_MODEL = _FakeGuard(confidence=0.9)
        _, flag = resolve_and_assess("EVGA RTX 3080 FTW3")
        assert flag == "ml_guard"

    def test_rule_rejection_wins_over_the_model(self):
        enrich._LOADED = True
        enrich._GUARD_MODEL = _FakeGuard(confidence=0.1)
        _, flag = resolve_and_assess("RTX 4090 water block cooler")
        assert flag == "accessory_or_part"


class TestCache:
    def test_reset_clears_cached_models(self):
        enrich._LOADED = True
        enrich._RESOLVER_MODEL = object()
        enrich._GUARD_MODEL = object()
        enrich.reset_model_cache()
        assert enrich._LOADED is False
        assert enrich._RESOLVER_MODEL is None
        assert enrich._GUARD_MODEL is None
