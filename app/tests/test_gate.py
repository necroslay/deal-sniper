"""Tests for valuation/gate.py — the single alert-gate definition.

Regression cover for the duplicated-gate bug: the pipeline and the store each had
their own copy of this rule, and the store's copy omitted the comp-count check.
"""

from __future__ import annotations

from app.db.store import Store
from app.models.schema import (
    CanonicalProduct,
    CompBand,
    ConfidenceLevel,
    DealScore,
    FairValueEstimate,
    Platform,
    RawListing,
    RealizedValue,
    ValuationOutput,
)
from app.valuation.gate import passes_alert_gate


def _output(profit=100.0, margin=25.0, level=ConfidenceLevel.alert_caution, comps=10):
    """Build a ValuationOutput with just enough populated to exercise the gate."""
    raw = RawListing(
        source=Platform.kijiji, listing_id="gate_1", title="RTX 3080",
        price_cad=500.0, url="https://example.test/x",
    )
    band = CompBand(
        standard_name="RTX 3080", condition="used_good", count=comps,
        median_price_cad=800.0, mean_price_cad=800.0,
        p25_price_cad=700.0, p75_price_cad=900.0,
        min_price_cad=600.0, max_price_cad=1000.0,
    )
    return ValuationOutput(
        raw_listing=raw,
        canonical_product=CanonicalProduct(standard_name="RTX 3080"),
        fair_value=FairValueEstimate(
            standard_name="RTX 3080", fmv_cad=800.0,
            fmv_confidence_interval=(700.0, 900.0), condition="used_good",
            condition_confidence=1.0, comps_used=comps, comp_band=band,
        ),
        realized_value=RealizedValue(
            fmv_cad=800.0, platform_fees_cad=0.0, shipping_cost_cad=0.0,
            return_reserve_cad=24.0, erv_cad=776.0,
        ),
        deal_score=DealScore(
            score=60.0, margin_score=25.0, liquidity_score=50.0, urgency_score=50.0,
            confidence_score=80.0, penalty_total=0.0, expected_profit_cad=profit,
            margin_pct=margin, confidence_level=level, asking_price_cad=500.0,
            fmv_cad=800.0, erv_cad=776.0,
        ),
    )


class TestGatePasses:
    def test_qualifying_deal_passes(self):
        assert passes_alert_gate(_output()) is True

    def test_alert_immediate_passes(self):
        assert passes_alert_gate(_output(level=ConfidenceLevel.alert_immediate)) is True


class TestGateRejects:
    def test_low_profit_rejected(self):
        assert passes_alert_gate(_output(profit=49.99)) is False

    def test_low_margin_rejected(self):
        assert passes_alert_gate(_output(margin=14.9)) is False

    def test_review_tier_rejected(self):
        assert passes_alert_gate(_output(level=ConfidenceLevel.review)) is False

    def test_suppress_tier_rejected(self):
        assert passes_alert_gate(_output(level=ConfidenceLevel.suppress)) is False

    def test_thin_comps_rejected(self):
        """A big discount from 4 comps is noise, not a deal."""
        assert passes_alert_gate(_output(comps=4)) is False

    def test_exactly_five_comps_passes(self):
        assert passes_alert_gate(_output(comps=5)) is True


class TestStoreUsesTheSharedGate:
    """The store's alert_sent flag must agree with the pipeline gate by construction."""

    def test_alert_sent_matches_gate(self, tmp_path):
        s = Store(db=str(tmp_path / "gate.db"))
        s.connect()
        try:
            for output, expected in [
                (_output(), True),
                (_output(profit=10.0), False),
                (_output(comps=2), False),
                (_output(level=ConfidenceLevel.review), False),
            ]:
                s.insert_decision(output)
                row = s._cur.execute(
                    "SELECT alert_sent FROM valuation_decisions "
                    "ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                assert bool(row["alert_sent"]) is expected
                assert bool(row["alert_sent"]) == passes_alert_gate(output)
        finally:
            s.close()
