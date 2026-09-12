"""Integration tests for pipeline/ingest.py — end-to-end valuation pipeline."""

import pytest

from app.db.store import Store
from app.pipeline.ingest import IngestionPipeline
from app.models.schema import ConfidenceLevel, Platform, RawListing


class TestPipeline:
    """End-to-end tests using real comp data and the full pipeline."""

    def test_rtx_3080_good_deal(self, rtx_3080_listing, rtx_3080_sold_comps):
        """
        RTX 3080 asking $750, comps suggest ~$835 median.
        Should identify as a reasonable deal.
        """
        pipeline = IngestionPipeline()
        output = pipeline.process(
            raw_listing=rtx_3080_listing,
            sold_comps=rtx_3080_sold_comps,
            travel_distance_km=5.0,
        )

        assert output.canonical_product.standard_name == "RTX 3080"
        assert output.fair_value.fmv_cad > 0
        assert output.raw_listing.price_cad == 750.0
        assert output.deal_score.score >= 0

    def test_rtx_4090_fb_marketplace(self, rtx_4090_listing, rtx_4090_sold_comps):
        """
        RTX 4090 asking $2000 on FB Marketplace (0% fees).
        Comps suggest ~$2600 median. Should score well.
        """
        pipeline = IngestionPipeline()
        output = pipeline.process(
            raw_listing=rtx_4090_listing,
            sold_comps=rtx_4090_sold_comps,
        )

        assert output.canonical_product.standard_name == "RTX 4090"
        assert output.fair_value.fmv_cad > 0
        assert output.deal_score.expected_profit_cad > 0

    def test_ps5_listing(self, ps5_listing, ps5_sold_comps):
        """
        PS5 asking $380, comps suggest ~$435 median.
        Should be a moderate deal.
        """
        pipeline = IngestionPipeline()
        output = pipeline.process(
            raw_listing=ps5_listing,
            sold_comps=ps5_sold_comps,
            travel_distance_km=10.0,
        )

        assert output.canonical_product.standard_name == "PlayStation 5"
        assert output.deal_score.score > 0

    def test_unknown_product_no_comps(self):
        """A product not in the registry with no comps should score 0."""
        listing = RawListing(
            source=Platform.kijiji,
            listing_id="unknown_1",
            title="Random Vintage Lamp",
            price_cad=50.0,
            url="https://kijiji.ca/unknown",
        )
        pipeline = IngestionPipeline()
        output = pipeline.process(raw_listing=listing, sold_comps=[])

        assert output.canonical_product.category == "unknown"
        assert output.fair_value.fmv_cad == 0.0
        assert output.deal_score.score == 0.0

    def test_high_confidence_deal_passes_alert_gate(self, rtx_4090_listing, rtx_4090_sold_comps):
        """
        RTX 4090 asking $2000 with 15 comps.
        FMV ~$2518 (like-new ×0.95), ERV ~$2392 (0% FB fees),
        profit ~$335, margin ~14%.
        Confidence should be ALERT_CAUTION.
        """
        pipeline = IngestionPipeline()
        output = pipeline.process(
            raw_listing=rtx_4090_listing,
            sold_comps=rtx_4090_sold_comps,
        )

        assert output.fair_value.comps_used >= 5
        assert output.deal_score.expected_profit_cad > 100
        assert output.deal_score.margin_pct >= 10.0
        assert output.deal_score.confidence_level == ConfidenceLevel.alert_caution
        # Score reflects conservative weighting: margin_score=14 + liquidity=22 + urgency=90 + confidence=80
        assert 30 <= output.deal_score.score <= 50

    def test_precomputed_comp_band(self, rtx_3080_listing, rtx_3080_sold_comps):
        """Using a pre-computed CompBand should work."""
        from app.engines.comp_engine import compute_comp_band
        band = compute_comp_band(rtx_3080_sold_comps, "RTX 3080", "used_good")

        pipeline = IngestionPipeline()
        output1 = pipeline.process(raw_listing=rtx_3080_listing, sold_comps=rtx_3080_sold_comps)
        output2 = pipeline.process(raw_listing=rtx_3080_listing, comp_band=band)

        assert output1.fair_value.fmv_cad == pytest.approx(output2.fair_value.fmv_cad, abs=1)


class TestSkipPathPersistence:
    """Rejected listings must be persisted *before* their decision row.

    Regression: the skip branch wrote a ``valuation_decisions`` row without a
    parent ``raw_listings`` row, so decisions outnumbered listings (867 vs 698).
    SQLite tolerates the dangling reference; the Postgres schema does not —
    ``listing_id UUID NOT NULL REFERENCES listings(id)`` — so every orphan would
    break the migration. Keeping rejected listings also preserves them for audit.
    """

    @pytest.fixture
    def store(self, tmp_path):
        s = Store(db=str(tmp_path / "pipeline.db"))
        s.connect()
        yield s
        s.close()

    def _run(self, pipeline, listing, comps=None):
        return pipeline.process(raw_listing=listing, sold_comps=comps, persist=True)

    def test_rejected_listings_get_a_parent_row(self, store, rtx_3080_listing,
                                                rtx_3080_sold_comps):
        pipeline = IngestionPipeline(store=store)

        # 1) a legitimate listing that passes every stage
        self._run(pipeline, rtx_3080_listing, rtx_3080_sold_comps)

        # 2) rejected by the pre-filter (price below the $10 floor)
        self._run(pipeline, RawListing(
            source=Platform.kijiji, listing_id="skip_price",
            title="RTX 3080 cheap", price_cad=1.0,
            url="https://kijiji.ca/v-cheap/1",
        ))

        # 3) rejected by the listing guard (a cable that *names* a GPU)
        self._run(pipeline, RawListing(
            source=Platform.kijiji, listing_id="skip_guard",
            title="RTX 4090 power cable", price_cad=40.0,
            url="https://kijiji.ca/v-cable/1",
        ))

        counts = {
            table: store._cur.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]
            for table in ("raw_listings", "valuation_decisions")
        }
        assert counts["raw_listings"] == 3
        assert counts["valuation_decisions"] == 3

        # No decision may reference a listing that does not exist.
        orphans = store._cur.execute(
            "SELECT COUNT(*) AS n FROM valuation_decisions d "
            "LEFT JOIN raw_listings l ON l.source_listing_id = d.listing_id "
            "WHERE l.id IS NULL"
        ).fetchone()["n"]
        assert orphans == 0

    def test_rejected_listings_never_alert(self, store):
        pipeline = IngestionPipeline(store=store)
        self._run(pipeline, RawListing(
            source=Platform.kijiji, listing_id="skip_only",
            title="RTX 4090 power cable", price_cad=40.0,
            url="https://kijiji.ca/v-cable/2",
        ))

        row = store._cur.execute(
            "SELECT alert_sent, confidence_level FROM valuation_decisions"
        ).fetchone()
        assert row["alert_sent"] == 0
        assert row["confidence_level"] == ConfidenceLevel.suppress.value