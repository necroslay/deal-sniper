"""Tests for engines/listing_guard.py — rejecting false-positive listings.

Regression cover for the resolver false positives: listings that merely *mention*
a product were being valued as that product, producing alerts such as a $20 GPU
power cable "worth" $1,893.
"""

from __future__ import annotations

import pytest

from app.engines.listing_guard import assess_listing
from app.engines.resolver import find_products, resolve_product
from app.models.schema import Platform, RawListing


def _product_for(title):
    return resolve_product(title)


class TestFindProducts:
    """find_products must report distinct products, not overlapping aliases."""

    def test_single_product(self):
        assert find_products("EVGA GeForce RTX 3080 FTW3") == ["RTX 3080"]

    def test_more_specific_match_suppresses_base(self):
        # "RTX 3060" is a substring of "RTX 3060 Ti" -- only the Ti is real.
        assert find_products("RTX 3060 Ti") == ["RTX 3060 Ti"]

    def test_super_variant_suppresses_base(self):
        assert find_products("RTX 4080 Super") == ["RTX 4080 Super"]

    def test_switch_oled_suppresses_base(self):
        assert find_products("Nintendo Switch OLED White") == ["Nintendo Switch OLED"]

    def test_multi_item_lot_reports_each(self):
        found = find_products("3060 Ti, 3070 and RTX3080 for sale")
        assert len(found) >= 2


class TestAccessoryRejection:
    """A part or accessory that names the product it fits is not the product."""

    def test_gpu_cable_listing(self):
        title = "GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090 6+2 Male"
        assert assess_listing(title, _product_for(title)) == "accessory_or_part"

    def test_switch_dock_listing(self):
        title = "NEW 2-in-1 Switch 2 Dock & Capture Card USB-C Video Streaming"
        assert assess_listing(title, _product_for(title)) == "accessory_or_part"

    def test_cooler_listing(self):
        assert assess_listing("RTX 4090 water block cooler", None) == "accessory_or_part"

    def test_stand_word_boundary_does_not_fire_on_standard(self):
        title = "RTX 3080 standard edition"
        assert assess_listing(title, _product_for(title)) is None


class TestPlatformAccessoryRejection:
    def test_game_for_ps5(self):
        title = "Soul Hackers 2 for PS5 (brand new sealed)"
        assert assess_listing(title, _product_for(title)) == "platform_accessory"

    def test_accessory_for_switch(self):
        title = "Screen protector for Switch"
        assert assess_listing(title, _product_for(title)) == "accessory_or_part"


class TestHostItemRejection:
    """A GPU inside a laptop is not a GPU for sale."""

    def test_gpu_in_laptop(self):
        title = "Alienware Laptop Ryzen 7 5800H 16GB RTX 3060"
        assert assess_listing(title, _product_for(title)) == "component_in_host_item"

    def test_laptop_product_is_not_rejected(self):
        # The product IS a laptop, so the host rule must not apply.
        title = "MacBook Air M2 2022 8GB 256GB"
        assert assess_listing(title, _product_for(title)) is None

    def test_console_not_subject_to_host_rule(self):
        """A console is not a component of a laptop, so the host rule must not fire."""
        title = "Nintendo Switch with laptop"
        assert assess_listing(title, _product_for(title)) is None


class TestLotRejection:
    def test_multi_gpu_lot(self):
        title = "Great condition RTX 3060 Ti, RTX 3070, RTX3080 for sale"
        assert assess_listing(title, _product_for(title)) == "multi_item_lot"

    def test_mixed_lot(self):
        title = "NVIDA GeForce RTX 2080Ti 11GB 3060Ti 3070 8GB, older ATI/AMD"
        assert assess_listing(title, _product_for(title)) == "multi_item_lot"


class TestLegitimateListingsPass:
    """The guard must not reject genuine single-item listings."""

    @pytest.mark.parametrize("title", [
        "EVGA GeForce RTX 3080 FTW3 Ultra Gaming 10GB",
        "ASUS RTX 4090 TUF Gaming OC Edition",
        "PS5 Disc Version - Like New",
        "RTX 3060 Gigabyte - used",
        "Nintendo Switch OLED White",
        "ROG Strix Gaming RTX 4090",
        "GeForce 4090 RTX GPU",
        "Nvidia RTX3080 New",
        "Sapphire Radeon RX 7900 XTX Nitro+",
        "AMD Ryzen 7 7800X3D Processor",
        "iPhone 15 Pro Max 256GB Natural Titanium",
    ])
    def test_passes(self, title):
        assert assess_listing(title, _product_for(title)) is None


class TestPipelineIntegration:
    """A rejected listing must produce a zero valuation."""

    def test_cable_rejected_end_to_end(self, rtx_4090_sold_comps):
        from app.pipeline.ingest import IngestionPipeline

        listing = RawListing(
            source=Platform.kijiji,
            listing_id="cable_1",
            title="GPU power cable, Fasgear. RTX 3090 Ti, 4080 and 4090 6+2 Male",
            price_cad=20.0,
            url="https://kijiji.ca/cable",
        )
        out = IngestionPipeline().process(raw_listing=listing, sold_comps=rtx_4090_sold_comps)

        assert out.fair_value.fmv_cad == 0.0
        assert out.deal_score.expected_profit_cad == 0.0
        assert out.deal_score.score == 0.0
        assert out.deal_score.risk_flags == ["accessory_or_part"]

    def test_legitimate_listing_still_valuated(self, rtx_4090_listing, rtx_4090_sold_comps):
        from app.pipeline.ingest import IngestionPipeline

        out = IngestionPipeline().process(
            raw_listing=rtx_4090_listing, sold_comps=rtx_4090_sold_comps
        )
        assert out.fair_value.fmv_cad > 0
        assert out.deal_score.risk_flags != ["accessory_or_part"]
