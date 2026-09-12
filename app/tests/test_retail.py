"""Tests for the retail-anchored valuation basis.

Covers the honesty guarantees that make retail usable at all: it is labelled
``basis="retail"``, it never claims comps it does not have, it can never reach
``ALERT_IMMEDIATE``, and it is inert when disabled.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import CFG
from app.db.store import Store
from app.models.schema import (
    CompBand,
    ConfidenceLevel,
    Platform,
    RawListing,
    RetailPrice,
)
from app.pipeline.ingest import IngestionPipeline
from app.valuation.fair_value import compute_fmv_from_retail
from app.valuation.gate import passes_alert_gate
from app.valuation.realize import compute_erv
from app.valuation.score import compute_deal_score

UTC = timezone.utc


@pytest.fixture
def store(tmp_path):
    s = Store(db=str(tmp_path / "retail.db"))
    s.connect()
    yield s
    s.close()


def _empty_band(name="RTX 3080", condition="used_good") -> CompBand:
    return CompBand(standard_name=name, condition=condition, count=0,
                    median_price_cad=0.0, mean_price_cad=0.0, p25_price_cad=0.0,
                    p75_price_cad=0.0, min_price_cad=0.0, max_price_cad=0.0)


# ── FMV from retail ─────────────────────────────────────────────────────────

class TestComputeFmvFromRetail:
    def test_applies_condition_multiplier_to_retail(self):
        # used_good = 0.82, so $1000 new -> $820 used.
        fv = compute_fmv_from_retail(1000.0, "used_good")
        assert fv.fmv_cad == pytest.approx(1000.0 * CFG.condition_multipliers.used_good)
        assert fv.basis == "retail"
        assert fv.retail_reference_cad == 1000.0

    def test_never_claims_comps(self):
        fv = compute_fmv_from_retail(1000.0, "used_good")
        assert fv.comps_used == 0
        assert fv.comp_band is None

    def test_interval_is_below_the_retail_anchor(self):
        fv = compute_fmv_from_retail(1000.0, "used_good")
        low, high = fv.fmv_confidence_interval
        assert high == pytest.approx(fv.fmv_cad)
        assert low < high

    def test_no_retail_price_yields_no_basis(self):
        fv = compute_fmv_from_retail(0.0, "used_good")
        assert fv.basis == "none"
        assert fv.fmv_cad == 0.0

    def test_applies_item_adjustments(self):
        fv = compute_fmv_from_retail(1000.0, "used_good", adjustments=[-15.0])
        assert fv.fmv_cad == pytest.approx(1000.0 * 0.82 - 15.0)


# ── Store ───────────────────────────────────────────────────────────────────

class TestRetailPriceStore:
    def test_upsert_refreshes_rather_than_duplicates(self, store):
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=1000.0,
                                              retailer="canada_computers"))
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=950.0,
                                              retailer="canada_computers"))
        assert len(store.get_retail_prices(product_key="RTX 3080")) == 1
        assert store.get_retail_price("RTX 3080").price_cad == 950.0

    def test_cheapest_quote_wins(self, store):
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=1100.0, retailer="a"))
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=980.0, retailer="b"))
        assert store.get_retail_price("RTX 3080").retailer == "b"

    def test_stale_quotes_are_excluded(self, store):
        old = (datetime.now(UTC) - timedelta(days=200)).isoformat()
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=900.0, retailer="old",
                                              observed_at=old))
        assert store.get_retail_price("RTX 3080", max_age_days=45) is None
        assert store.get_retail_price("RTX 3080") is not None


# ── Scoring / gating ────────────────────────────────────────────────────────

def _retail_decision(asking=300.0, retail=1000.0):
    """Build the full valuation stack for a retail-anchored deal."""
    from app.models.schema import CanonicalProduct, ValuationOutput, RawListing

    fv = compute_fmv_from_retail(retail, "used_good")
    erv = compute_erv(fmv_cad=fv.fmv_cad, platform=Platform.kijiji,
                      shipping_cost_cad=0.0, category="gpu")
    band = _empty_band()
    ds = compute_deal_score(
        asking_price_cad=asking, fair_value=fv, realized_value=erv,
        comp_band=band, condition_confidence=fv.condition_confidence,
        days_since_posted=1, platform=Platform.kijiji,
    )
    listing = RawListing(source=Platform.kijiji, listing_id="r1",
                         title="RTX 3080", price_cad=asking,
                         url="https://kijiji.ca/r1")
    output = ValuationOutput(
        raw_listing=listing,
        canonical_product=CanonicalProduct(standard_name="RTX 3080", category="gpu"),
        fair_value=fv, realized_value=erv, deal_score=ds)
    return output


class TestRetailScoring:
    def test_basis_flag_is_recorded_on_the_decision(self):
        out = _retail_decision()
        assert out.fair_value.basis == "retail"
        assert "retail_basis" in out.deal_score.risk_flags

    def test_confidence_cannot_reach_immediate(self):
        # A huge margin still must not produce ALERT_IMMEDIATE without comps.
        out = _retail_decision(asking=100.0, retail=5000.0)
        assert out.deal_score.confidence_level == ConfidenceLevel.alert_caution
        assert out.deal_score.confidence_level != ConfidenceLevel.alert_immediate

    def test_confidence_cap_is_configurable(self, monkeypatch):
        monkeypatch.setattr(CFG.retail, "confidence_cap", "REVIEW")
        out = _retail_decision()
        assert out.deal_score.confidence_level == ConfidenceLevel.review


class TestRetailGate:
    def test_retail_deal_alerts_when_allowed(self):
        out = _retail_decision()
        assert out.fair_value.comps_used == 0
        assert passes_alert_gate(out) is True

    def test_retail_deal_blocks_when_alerts_disabled(self, monkeypatch):
        monkeypatch.setattr(CFG.retail, "allow_alerts", False)
        out = _retail_decision()
        assert passes_alert_gate(out) is False

    def test_retail_deal_blocks_when_feature_disabled(self, monkeypatch):
        monkeypatch.setattr(CFG.retail, "enabled", False)
        out = _retail_decision()
        assert passes_alert_gate(out) is False

    def test_thin_profit_still_fails_the_gate(self):
        # Retail basis does not excuse a bad deal.
        out = _retail_decision(asking=790.0, retail=1000.0)
        assert passes_alert_gate(out) is False


# ── Pipeline integration ────────────────────────────────────────────────────

class TestPipelineRetailFallback:
    def _listing(self):
        return RawListing(
            source=Platform.kijiji, listing_id="k1",
            title="EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB",
            price_cad=400.0, url="https://kijiji.ca/k1",
            description="Used for gaming, excellent condition.",
        )

    def test_no_comps_uses_retail_when_available(self, store):
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=1000.0,
                                              retailer="canada_computers"))
        pipeline = IngestionPipeline(store=store)
        out = pipeline.process(raw_listing=self._listing(), persist=True)
        assert out.fair_value.basis == "retail"
        assert out.fair_value.fmv_cad > 0
        assert out.fair_value.comps_used == 0

    def test_no_comps_and_no_retail_stays_suppressed(self, store):
        pipeline = IngestionPipeline(store=store)
        out = pipeline.process(raw_listing=self._listing(), persist=True)
        assert out.fair_value.fmv_cad == 0.0
        assert out.fair_value.basis == "none"

    def test_sold_comps_take_precedence_over_retail(self, store, rtx_3080_sold_comps):
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=1000.0, retailer="cc"))
        pipeline = IngestionPipeline(store=store)
        out = pipeline.process(raw_listing=self._listing(),
                               sold_comps=rtx_3080_sold_comps, persist=True)
        assert out.fair_value.basis == "sold_comps"
        assert out.fair_value.comps_used > 0

    def test_retail_disabled_falls_back_to_comp_path(self, store, monkeypatch):
        monkeypatch.setattr(CFG.retail, "enabled", False)
        store.insert_retail_price(RetailPrice(product_key="RTX 3080",
                                              price_cad=1000.0, retailer="cc"))
        pipeline = IngestionPipeline(store=store)
        out = pipeline.process(raw_listing=self._listing(), persist=True)
        assert out.fair_value.basis == "none"
        assert out.fair_value.fmv_cad == 0.0


# ── Import tool ─────────────────────────────────────────────────────────────

class TestRetailImportTool:
    def test_import_rows_upserts_and_flags_unknown(self, store):
        from app.tools.import_retail_prices import import_rows

        summary = import_rows(store, [
            {"product_key": "RTX 3080", "price_cad": "999.99",
             "retailer": "cc", "url": "https://x"},
            {"product_key": "Not A Real GPU", "price_cad": "123.45",
             "retailer": "cc"},
            {"product_key": "RTX 4090", "price_cad": "0"},  # non-positive
        ])
        assert summary["imported"] == 2
        assert summary["skipped"] == 1
        assert summary["unknown_products"] == ["Not A Real GPU"]
        assert store.get_retail_price("RTX 3080").price_cad == 999.99

    def test_load_rows_reads_csv(self, tmp_path):
        from app.tools.import_retail_prices import load_rows

        path = tmp_path / "r.csv"
        path.write_text("product_key,price_cad,retailer\nRTX 3080,999.99,cc\n",
                        encoding="utf-8")
        assert load_rows(path) == [
            {"product_key": "RTX 3080", "price_cad": "999.99", "retailer": "cc"}]

    def test_load_rows_reads_json(self, tmp_path):
        from app.tools.import_retail_prices import load_rows

        path = tmp_path / "r.json"
        path.write_text('[{"product_key": "RTX 3080", "price_cad": 999.99}]',
                        encoding="utf-8")
        assert load_rows(path)[0]["product_key"] == "RTX 3080"

    def test_load_rows_rejects_unknown_extension(self, tmp_path):
        from app.tools.import_retail_prices import load_rows

        path = tmp_path / "r.txt"
        path.write_text("nope", encoding="utf-8")
        with pytest.raises(ValueError):
            load_rows(path)
