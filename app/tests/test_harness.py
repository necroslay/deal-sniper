"""Tests for eval/harness.py — scoring, reporting and the regression gate."""

from __future__ import annotations

import importlib.util

import pytest

from app.config import CFG
from app.eval import harness
from app.ml import dataset as ds


def _sklearn_available() -> bool:
    return importlib.util.find_spec("sklearn") is not None


class _FixedPredictor:
    """Predicts the same label list for any input."""

    name = "fixed"

    def __init__(self, labels):
        self._labels = list(labels)

    def predict(self, texts):
        return list(self._labels)

    def predict_proba(self, texts):
        return None


class _ScoredPredictor:
    """Returns fixed predictions and probabilities (for the guard PR extras)."""

    name = "scored"
    classes = ["keep", "reject"]

    def __init__(self, predictions, proba):
        self._predictions = list(predictions)
        self._proba = list(proba)

    def predict(self, texts):
        return list(self._predictions)

    def predict_proba(self, texts):
        return list(self._proba)


class TestEvaluate:
    def test_perfect_predictions(self):
        result = harness.evaluate(_FixedPredictor(["x", "y"]), ["a", "b"],
                                  ["x", "y"], "resolver")
        assert result.accuracy == pytest.approx(1.0)
        assert result.macro_f1 == pytest.approx(1.0)
        assert result.n == 2
        assert result.predictor == "fixed"

    def test_label_map_applies_to_true_and_predicted(self):
        result = harness.evaluate(
            _FixedPredictor(["RTX 3060"]), ["t"], ["RTX 3060"], "resolver",
            label_map={"RTX 3060": "OTHER"})
        assert result.accuracy == pytest.approx(1.0)
        assert result.labels == ["OTHER"]

    def test_guard_records_precision_at_recall_when_scored(self):
        predictor = _ScoredPredictor(["reject", "keep"],
                                     [[0.1, 0.9], [0.9, 0.1]])
        result = harness.evaluate(predictor, ["a", "b"], ["reject", "keep"],
                                  "guard")
        assert "precision_at_recall" in result.extras


class TestReporting:
    def _results(self):
        return [harness.evaluate(_FixedPredictor(["x"]), ["a"], ["x"], "resolver")]

    def test_render_has_a_comparison_table(self):
        text = harness.render_report(self._results(), "Some title")
        assert "# Some title" in text
        assert "## Comparison" in text
        assert "`fixed`" in text

    def test_write_reports_writes_markdown_and_json(self, tmp_path):
        md_path, json_path = harness.write_reports(
            self._results(), "resolver", tmp_path)
        assert md_path.exists() and json_path.exists()
        assert "macro_f1" in json_path.read_text(encoding="utf-8")


class TestBaselineGate:
    def test_detects_a_regression(self):
        regressions = harness.check_baseline(
            [{"predictor": "m", "macro_f1": 0.5}],
            [{"predictor": "m", "macro_f1": 0.8}])
        assert len(regressions) == 1

    def test_a_missing_predictor_is_a_regression(self):
        regressions = harness.check_baseline(
            [], [{"predictor": "m", "macro_f1": 0.8}])
        assert "missing" in regressions[0]

    def test_no_regression_when_equal(self):
        assert harness.check_baseline(
            [{"predictor": "m", "macro_f1": 0.8}],
            [{"predictor": "m", "macro_f1": 0.8}]) == []


class TestExperiments:
    def test_resolver_experiment_scores_the_baseline_first(self, tiny_examples):
        CFG.ml.min_class_support = 1   # don't fold the tiny fixture's classes
        results = harness.run_resolver_experiment(tiny_examples)

        names = [r.predictor for r in results]
        assert names[0] == "resolver-baseline"
        if _sklearn_available():
            assert "tfidf-linear" in names
            assert "hybrid-resolver" in names
        assert all(0.0 <= r.macro_f1 <= 1.0 for r in results)
        assert all(r.n > 0 for r in results)

    def test_guard_experiment(self, labels_csv):
        examples = ds.load_labeled_csv(labels_csv, "guard_label")
        results = harness.run_guard_experiment(examples)

        names = [r.predictor for r in results]
        assert names[0] == "guard-baseline"
        if _sklearn_available():
            assert "guard-model" in names
            assert "hybrid-guard" in names


class TestCli:
    def test_missing_labels_returns_2(self, tmp_path):
        code = harness.main(["--task", "resolver",
                             "--labels", str(tmp_path / "absent.csv"),
                             "--reports-dir", str(tmp_path)])
        assert code == 2

    def test_run_then_a_gated_rerun_passes(self, labels_csv, tmp_path):
        CFG.ml.min_class_support = 1
        args = ["--task", "resolver", "--labels", labels_csv,
                "--reports-dir", str(tmp_path)]

        assert harness.main(args) == 0
        assert (tmp_path / "resolver_eval.md").exists()

        # Deterministic training + fixed splits: a rerun cannot regress vs itself.
        assert harness.main(args + ["--check-baseline"]) == 0
