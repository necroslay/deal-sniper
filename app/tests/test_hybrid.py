"""Tests for ml/hybrid.py — rules stay authoritative, the model fills the gaps."""

from __future__ import annotations

from app.config import CFG
from app.eval.predictors import KEEP, REJECT, GuardBaseline, ResolverBaseline
from app.ml.hybrid import HybridGuard, HybridResolver


class _FakeResolverModel:
    """Predicts one label with *confidence* as the top probability.

    Ten classes so that a low ``confidence`` really is the row maximum (with two
    classes, 0.1 for the label would make the other class 0.9).
    """

    def __init__(self, label="RTX 4090", confidence=0.9):
        self.label = label
        self.confidence = confidence
        self.classes = [label] + [f"other_{i}" for i in range(9)]
        self.calls = 0

    def predict(self, texts):
        self.calls += 1
        return [self.label for _ in texts]

    def predict_proba(self, texts):
        other = (1 - self.confidence) / (len(self.classes) - 1)
        return [[self.confidence if c == self.label else other
                 for c in self.classes] for _ in texts]


class _FakeGuardModel:
    classes = ["keep", "reject"]

    def __init__(self, prediction="reject", confidence=0.9):
        self.prediction = prediction
        self.confidence = confidence
        self.calls = 0

    def predict(self, texts):
        self.calls += 1
        return [self.prediction for _ in texts]

    def predict_proba(self, texts):
        reject = self.confidence if self.prediction == REJECT else 1 - self.confidence
        return [[1 - reject, reject] for _ in texts]


class TestHybridResolver:
    def test_known_title_never_consults_the_model(self):
        model = _FakeResolverModel()
        hybrid = HybridResolver(ResolverBaseline(), model)
        assert hybrid.predict(["EVGA RTX 3080 FTW3"]) == ["RTX 3080"]
        assert model.calls == 0

    def test_unknown_title_falls_through_to_the_model(self):
        model = _FakeResolverModel(label="RTX 4090")
        hybrid = HybridResolver(ResolverBaseline(), model)
        assert hybrid.predict(["Mystery 3D accelerator"]) == ["RTX 4090"]
        assert model.calls == 1

    def test_low_confidence_keeps_the_rule_abstention(self):
        model = _FakeResolverModel(label="RTX 4090", confidence=0.1)
        hybrid = HybridResolver(ResolverBaseline(), model)
        assert hybrid.predict(["Mystery 3D accelerator"]) == [CFG.ml.unknown_label]


class TestHybridGuard:
    def test_rule_rejection_stands_without_asking_the_model(self):
        model = _FakeGuardModel(prediction=KEEP)
        hybrid = HybridGuard(GuardBaseline(), model)
        assert hybrid.predict(["RTX 4090 water block cooler"]) == [REJECT]
        assert model.calls == 0

    def test_model_rejects_a_title_the_rules_kept(self):
        model = _FakeGuardModel(prediction=REJECT, confidence=0.9)
        hybrid = HybridGuard(GuardBaseline(), model)
        assert hybrid.predict(["EVGA RTX 3080 FTW3"]) == [REJECT]

    def test_low_confidence_model_does_not_override_the_rules(self):
        model = _FakeGuardModel(prediction=REJECT, confidence=0.2)
        hybrid = HybridGuard(GuardBaseline(), model)
        assert hybrid.predict(["EVGA RTX 3080 FTW3"]) == [KEEP]
