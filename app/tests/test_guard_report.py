"""Tests for eval/guard_report.py — the threshold comes from error cost."""

from __future__ import annotations

import pytest

from app.eval.guard_report import choose_threshold, threshold_report

# Descending scores: reject(.9), keep(.8), reject(.4), keep(.1)
Y = ["reject", "reject", "keep", "keep"]
SCORES = [0.9, 0.4, 0.8, 0.1]


class TestChooseThreshold:
    def test_biased_toward_catching_junk(self):
        chosen = choose_threshold(Y, SCORES, target_recall=0.95)
        assert chosen["achieved"] is True
        assert chosen["recall"] == pytest.approx(1.0)
        assert chosen["threshold"] == pytest.approx(0.4)

    def test_loose_target_keeps_more_precision(self):
        chosen = choose_threshold(Y, SCORES, target_recall=0.5)
        assert chosen["precision"] == pytest.approx(1.0)


class TestThresholdReport:
    def test_report_states_the_target(self):
        text = threshold_report(Y, SCORES, target_recall=0.95)
        assert "Guard operating threshold" in text
        assert "0.95" in text
        assert "Precision" in text

    def test_unreachable_target_is_called_out(self):
        text = threshold_report(["keep", "keep"], [0.9, 0.1],
                                target_recall=0.95)
        assert "No threshold reached" in text
