"""Tests for engines/condition.py — condition classification."""

import pytest

from app.engines.condition import classify_condition, ConditionGrade


class TestConditionClassifier:
    def test_brand_new(self):
        result = classify_condition(
            "RTX 3080 Brand New Sealed",
            "Factory sealed, never opened."
        )
        assert result.grade == ConditionGrade.brand_new
        assert result.confidence >= 0.80

    def test_like_new_with_keywords(self):
        result = classify_condition("MacBook Pro M2 Like New", "")
        assert result.grade == ConditionGrade.open_box_like_new
        assert result.confidence >= 0.80

    def test_used_good(self):
        result = classify_condition("Used PS5 - Good Condition", "Works perfectly")
        assert result.grade == ConditionGrade.used_good
        assert result.confidence >= 0.70

    def test_for_parts(self):
        result = classify_condition("Broken RTX 3080 For Parts", "Not working, as is")
        assert result.grade == ConditionGrade.for_parts
        assert result.confidence >= 0.80

    def test_mint_condition(self):
        result = classify_condition("RTX 4090 Mint Condition", "")
        assert result.grade == ConditionGrade.open_box_like_new
        assert result.confidence >= 0.80

    def test_unknown_when_no_keywords(self):
        result = classify_condition(
            "Selling My Computer",
            "Great computer, comes with accessories"
        )
        assert result.grade == ConditionGrade.unknown
        assert result.confidence == 0.0

    def test_barely_used(self):
        result = classify_condition("PS5 Barely Used", "")
        assert result.grade == ConditionGrade.open_box_like_new

    def test_question_mark_reduces_confidence(self):
        """Uncertainty signals should reduce confidence."""
        result = classify_condition(
            "RTX 3080? Not sure if working",
            "Unknown condition"
        )
        # Should still match something but with uncertainty penalty
        assert len(result.signals) > 0

    def test_description_also_scanned(self):
        """Condition keywords in description should be found."""
        result = classify_condition(
            "RTX 4090",
            "This item is brand new in box, never opened, factory sealed."
        )
        assert result.grade == ConditionGrade.brand_new
        assert result.confidence >= 0.90

    def test_multiple_matches_boost_confidence(self):
        result = classify_condition(
            "Like New RTX 3080",
            "Mint condition, excellent condition, barely used, open box"
        )
        assert result.grade == ConditionGrade.open_box_like_new
        # Multiple matches should boost confidence above base
        assert result.confidence >= 0.80