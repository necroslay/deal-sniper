"""Tests for eval/metrics.py — the local, numpy-only classification metrics."""

from __future__ import annotations

import pytest

from app.eval.metrics import (accuracy, classification_report_markdown,
                              confusion_matrix, interval_coverage, macro_f1,
                              mae, per_class_prf, pinball_loss,
                              precision_at_recall, precision_recall_curve)

# a is confused once as b; b and c are perfect on recall.
Y_TRUE = ["a", "a", "b", "b", "c", "c"]
Y_PRED = ["a", "b", "b", "b", "c", "c"]


class TestAccuracyAndConfusion:
    def test_accuracy(self):
        assert accuracy(Y_TRUE, Y_PRED) == pytest.approx(5 / 6)

    def test_empty_input_is_zero(self):
        assert accuracy([], []) == 0.0

    def test_confusion_matrix_counts(self):
        labels, matrix = confusion_matrix(Y_TRUE, Y_PRED)
        assert labels == ["a", "b", "c"]
        assert matrix.tolist() == [[1, 1, 0], [0, 2, 0], [0, 0, 2]]

    def test_label_outside_declared_set_raises(self):
        with pytest.raises(ValueError):
            confusion_matrix(["a"], ["z"], labels=["a"])


class TestPerClassAndMacroF1:
    def test_per_class_values(self):
        prf = per_class_prf(Y_TRUE, Y_PRED)
        assert prf["a"]["precision"] == pytest.approx(1.0)
        assert prf["a"]["recall"] == pytest.approx(0.5)
        assert prf["b"]["precision"] == pytest.approx(2 / 3)
        assert prf["b"]["recall"] == pytest.approx(1.0)
        assert prf["c"]["f1"] == pytest.approx(1.0)
        assert prf["b"]["support"] == 2

    def test_macro_f1_is_unweighted_mean(self):
        assert macro_f1(Y_TRUE, Y_PRED) == pytest.approx(
            (2 / 3 + 0.8 + 1.0) / 3)

    def test_macro_f1_empty_is_zero(self):
        assert macro_f1([], []) == 0.0

    def test_zero_support_class_does_not_swing_macro_f1(self):
        """A class only ever *predicted* must not be averaged in as a hard zero.

        Without this, one phantom label (e.g. the bare-alias "iPhone 15"
        matching any title containing "15") could tank an otherwise good score.
        Here "zzz" is a false positive for an 'a', so 'a' loses recall (F1 2/3)
        while 'b' stays perfect -- and "zzz" itself is not its own class.
        """
        y_true = ["a", "a", "b", "b"]
        polluted = macro_f1(y_true, ["a", "zzz", "b", "b"])
        assert polluted == pytest.approx((2 / 3 + 1.0) / 2)


class TestPrecisionRecall:
    # Descending scores: reject(.9), keep(.8), reject(.4), keep(.1)
    Y = ["reject", "reject", "keep", "keep"]
    SCORES = [0.9, 0.4, 0.8, 0.1]

    def test_curve_prefixes(self):
        _, precision, recall = precision_recall_curve(self.Y, self.SCORES)
        assert precision[0] == pytest.approx(1.0)
        assert recall[0] == pytest.approx(0.5)
        assert recall[-1] == pytest.approx(1.0)

    def test_threshold_for_loose_target_keeps_precision(self):
        chosen = precision_at_recall(self.Y, self.SCORES, target_recall=0.5)
        assert chosen["achieved"] is True
        assert chosen["precision"] == pytest.approx(1.0)
        assert chosen["recall"] == pytest.approx(0.5)
        assert chosen["threshold"] == pytest.approx(0.9)

    def test_threshold_for_strict_target(self):
        chosen = precision_at_recall(self.Y, self.SCORES, target_recall=0.95)
        assert chosen["achieved"] is True
        assert chosen["recall"] == pytest.approx(1.0)
        assert chosen["threshold"] == pytest.approx(0.4)

    def test_unreachable_target_is_reported_not_hidden(self):
        # No positives at all: recall can never reach the target.
        chosen = precision_at_recall(["keep", "keep"], [0.9, 0.1],
                                     target_recall=0.95)
        assert chosen["achieved"] is False
        assert chosen["recall"] == pytest.approx(0.0)


class TestRegressionMetrics:
    """Metrics for the quantile price model."""

    def test_mae(self):
        assert mae([100, 200], [110, 180]) == pytest.approx(15.0)

    def test_mae_empty_is_zero(self):
        assert mae([], []) == 0.0

    def test_pinball_at_the_median_is_half_the_absolute_error(self):
        assert pinball_loss([100], [110], 0.5) == pytest.approx(5.0)

    def test_pinball_is_asymmetric(self):
        """A P75 forecast that is too *low* costs more than one too high."""
        assert pinball_loss([100], [90], 0.75) == pytest.approx(7.5)
        assert pinball_loss([100], [110], 0.75) == pytest.approx(2.5)

    def test_interval_coverage(self):
        covered = interval_coverage([100, 200, 300], [90, 210, 290],
                                    [110, 220, 310])
        assert covered == pytest.approx(2 / 3)

    def test_interval_coverage_empty_is_zero(self):
        assert interval_coverage([], [], []) == 0.0


class TestReport:
    def test_markdown_has_summary_and_rows(self):
        text = classification_report_markdown(
            per_class_prf(Y_TRUE, Y_PRED), accuracy(Y_TRUE, Y_PRED),
            macro_f1(Y_TRUE, Y_PRED))
        assert "Macro F1" in text
        assert "| `a` |" in text
        assert "Support" in text
