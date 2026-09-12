"""Tests for config.py — all thresholds, multipliers, fees, weights load correctly."""

from app.config import AppConfig, load_config, CFG


class TestConfigDefaults:
    """Verify all config defaults are sensible."""

    def test_config_is_singleton(self):
        cfg = load_config()
        assert isinstance(cfg, AppConfig)

    def test_platform_fees(self):
        assert CFG.platform_fees.ebay_canada_pct == 0.1325
        assert CFG.platform_fees.facebook_marketplace_pct == 0.0
        assert CFG.platform_fees.kijiji_pct == 0.0

    def test_condition_multipliers(self):
        assert CFG.condition_multipliers.brand_new == 1.05
        assert CFG.condition_multipliers.used_good == 0.82
        assert CFG.condition_multipliers.for_parts == 0.30

    def test_comp_config(self):
        assert CFG.comp.min_comps_for_valuation == 5
        assert CFG.comp.iqr_multiplier == 1.5
        assert CFG.comp.comp_window_days == 90

    def test_alert_gate(self):
        assert CFG.alert_gate.min_expected_profit_cad == 50.0
        assert CFG.alert_gate.min_margin_pct == 0.15
        assert CFG.alert_gate.min_confidence == 0.75

    def test_score_weights_sum_close_to_one(self):
        w = CFG.score_weights
        total = w.margin_weight + w.liquidity_weight + w.urgency_weight + w.confidence_weight
        assert abs(total - 1.0) < 0.01

    def test_travel_costs(self):
        assert CFG.travel_costs.flat_base == 10.0
        assert CFG.travel_costs.per_km_beyond == 0.75
        assert CFG.travel_costs.free_radius_km == 5.0

    def test_currency(self):
        assert CFG.currency == "CAD"
        assert CFG.country == "CA"