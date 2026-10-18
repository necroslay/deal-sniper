"""Tests for ml/active_learning.py — where labeling effort should be spent."""

from __future__ import annotations

from app.ml import active_learning as al


class TestUncertainty:
    def test_unknown_title_is_top_priority(self):
        assert al.resolver_uncertainty("Wooden desk") == (1.0, "resolver_unknown")

    def test_known_title_has_no_labeling_value(self):
        assert al.resolver_uncertainty("EVGA RTX 3080 FTW3") == (0.0, "")

    def test_multiple_products_is_ambiguous(self):
        score, reason = al.resolver_uncertainty("RTX 3060 Ti, RTX 3070, RTX3080")
        assert score == 0.5
        assert reason == "multiple_products"

    def test_guard_rejection_needs_no_review(self):
        assert al.guard_uncertainty("RTX 4090 water block cooler") == (0.0, "")

    def test_guard_near_miss_is_flagged(self):
        # A host keyword on a non-component product: the rules stay silent, but
        # this is exactly the kind of title a reviewer should adjudicate.
        score, reason = al.guard_uncertainty("Nintendo Switch with laptop")
        assert score == 0.5
        assert reason == "single_weak_signal"


class TestQueue:
    ROWS = [
        {"id": "1", "source": "kijiji", "title": "Wooden desk",
         "product_key": "", "flag_reason": ""},
        {"id": "2", "source": "kijiji", "title": "EVGA RTX 3080 FTW3",
         "product_key": "RTX 3080", "flag_reason": ""},
        {"id": "3", "source": "kijiji", "title": "Old router no box",
         "product_key": "", "flag_reason": ""},
    ]

    def test_only_valuable_rows_are_queued(self):
        queue = al.labeling_queue(self.ROWS)
        assert {row["id"] for row in queue} == {"1", "3"}

    def test_highest_priority_first(self):
        queue = al.labeling_queue(self.ROWS)
        assert queue[0]["priority"] >= queue[-1]["priority"]

    def test_limit_is_applied(self):
        assert len(al.labeling_queue(self.ROWS, limit=1)) == 1

    def test_export_queue_csv(self, tmp_path):
        path = tmp_path / "queue.csv"
        written = al.export_queue_csv(self.ROWS, path)
        assert written == 2
        assert "priority" in path.read_text(encoding="utf-8")
