"""Tests for engines/adjustments.py — item-level FMV deductions.

Regression cover for the dead-config bug: `CFG.item_adjustments` was never read
because the pipeline hardcoded `adjustments=[]`.
"""

from __future__ import annotations

from app.config import CFG
from app.engines.adjustments import detect_adjustment_reasons, detect_adjustments


class TestDetection:
    def test_real_missing_box_phrasing_is_detected(self):
        """The wording from the listing that exposed the bug."""
        desc = (
            "Just upgraded Can deliver depending on the area I keep it in my current "
            "gpus box with anti static bag but this gpu will not come with a box, "
            "only the anti static bag"
        )
        assert detect_adjustment_reasons("RTX 3060 Gigabyte - used", desc) == [
            "missing_original_box"
        ]

    def test_missing_charger(self):
        assert "missing_charger" in detect_adjustment_reasons(
            "iPhone 15 Pro - no charger included"
        )

    def test_major_damage(self):
        assert "cosmetic_damage_major" in detect_adjustment_reasons(
            "MacBook Pro cracked screen"
        )

    def test_battery_degradation(self):
        assert "battery_health_below_80" in detect_adjustment_reasons(
            "iPhone 14 Pro", "battery health 79%, otherwise great"
        )

    def test_multiple_families_stack(self):
        reasons = detect_adjustment_reasons(
            "PS5 with scratched screen", "no box, no cables"
        )
        assert set(reasons) == {
            "scratched_screen", "missing_original_box", "no_cables"
        }


class TestNegationGuard:
    """A negated flaw describes a *good* item and must not be penalised."""

    def test_no_scratches_is_not_penalised(self):
        assert detect_adjustments("RTX 3080 no scratches, works perfectly") == []

    def test_not_broken_is_not_penalised(self):
        assert detect_adjustments("PS5, not broken, fully working") == []

    def test_never_cracked_is_not_penalised(self):
        assert detect_adjustments("Switch never cracked, mint") == []

    def test_positive_mention_still_detected(self):
        """Sanity check the guard doesn't swallow genuine reports."""
        assert detect_adjustments("Switch with a cracked shell") != []

    def test_includes_box_is_not_penalised(self):
        assert detect_adjustments(
            "RTX 4090", "comes with original box and all accessories"
        ) == []


class TestNoDoubleCounting:
    """One flaw must not stack multiple deductions."""

    def test_scratched_screen_beats_generic_minor_damage(self):
        reasons = detect_adjustment_reasons("Monitor with scratched screen")
        assert reasons == ["scratched_screen"]
        # -50 (scratched_screen), not -50 + -25
        assert detect_adjustments("Monitor with scratched screen") == [
            CFG.item_adjustments.scratched_screen
        ]

    def test_cracked_beats_minor_damage(self):
        reasons = detect_adjustment_reasons("Phone with cracked and scratched glass")
        assert reasons == ["cosmetic_damage_major"]


class TestValuesComeFromConfig:
    def test_values_match_config(self):
        got = detect_adjustments("iPhone with no charger")
        assert got == [CFG.item_adjustments.missing_charger]

    def test_clean_listing_yields_no_adjustments(self):
        assert detect_adjustments(
            "ASUS RTX 4090 TUF Gaming OC Edition",
            "Like new, used for 2 months, still has warranty.",
        ) == []
