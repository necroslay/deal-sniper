"""Tests for engines/resolver.py — product resolution waterfall."""

import pytest

from app.engines.resolver import resolve_product, PRODUCT_REGISTRY


class TestResolverTier1:
    """Tier 1 — exact regex match against known aliases."""

    def test_exact_model_name(self):
        result = resolve_product("EVGA GeForce RTX 3080 FTW3 Ultra Gaming")
        assert result.standard_name == "RTX 3080"
        assert result.category == "gpu"

    def test_rtx_4090(self):
        result = resolve_product("NVIDIA GeForce RTX 4090 Founders Edition 24GB")
        assert result.standard_name == "RTX 4090"

    def test_rtx_5090(self):
        result = resolve_product("RTX 5090 FE")
        assert result.standard_name == "RTX 5090"

    def test_ryzen_cpu(self):
        result = resolve_product("AMD Ryzen 7 7800X3D Processor")
        assert result.standard_name == "Ryzen 7 7800X3D"
        assert result.category == "cpu"

    def test_ps5(self):
        result = resolve_product("PS5 Disc Version - Like New")
        assert result.standard_name == "PlayStation 5"
        assert result.category == "console"

    def test_macbook_air_m2(self):
        result = resolve_product("MacBook Air M2 2022 8GB 256GB Silver")
        assert result.standard_name == "MacBook Air M2"
        assert result.category == "laptop"

    def test_iphone_15_pro(self):
        result = resolve_product("iPhone 15 Pro Max 256GB Natural Titanium")
        assert result.standard_name == "iPhone 15 Pro Max"
        assert result.category == "mobile"

    def test_nintendo_switch(self):
        result = resolve_product("Nintendo Switch OLED White")
        assert result.standard_name == "Nintendo Switch OLED"

    def test_rx_7900_xtx(self):
        result = resolve_product("Sapphire Radeon RX 7900 XTX Nitro+")
        assert result.standard_name == "RX 7900 XTX"


class TestResolverTier2:
    """Tier 2 — fuzzy substring match."""

    def test_no_exact_match_fuzzy_fallback(self):
        result = resolve_product("Some Random GPU 3080 10GB Card")
        assert result.standard_name == "RTX 3080"

    def test_fuzzy_does_not_match_garbage(self):
        result = resolve_product("Random Laptop For Sale Cheap $500")
        assert result.standard_name == "Random Laptop For Sale Cheap $500"
        assert result.category == "unknown"


class TestResolverTier3:
    """Tier 3 — fallback when nothing matches."""

    def test_completely_unrecognized(self):
        result = resolve_product("Samsung 55-Inch 4K Smart TV")
        assert result.category == "unknown"
        assert result.standard_name == "Samsung 55-Inch 4K Smart TV"

    def test_empty_string(self):
        result = resolve_product("")
        assert result.standard_name == ""
        assert result.category == "unknown"

    def test_garbled_text(self):
        result = resolve_product("fsdkljf sldkjflksdj flksdjflk")
        assert result.category == "unknown"


class TestProductRegistry:
    """Verify the product registry is well-formed."""

    def test_all_entries_have_required_fields(self):
        for entry in PRODUCT_REGISTRY:
            assert "standard_name" in entry, f"Missing standard_name: {entry}"
            assert "aliases" in entry, f"Missing aliases: {entry}"
            assert len(entry["aliases"]) > 0, f"Empty aliases: {entry['standard_name']}"

    def test_no_duplicate_standard_names(self):
        names = [e["standard_name"] for e in PRODUCT_REGISTRY]
        duplicates = [n for n in names if names.count(n) > 1]
        assert duplicates == [], f"Duplicate standard names: {set(duplicates)}"

    def test_every_standard_name_resolves_to_itself(self):
        """Registry ordering invariant.

        ``resolve_product`` returns the *first* alias that matches, so a base
        model listed before its variant silently swallows it -- "PS5" matched
        before "PS5 Pro" and every PS5 Pro resolved to a PlayStation 5. Variants
        must therefore precede their base model in the registry.
        """
        misordered = []
        for entry in PRODUCT_REGISTRY:
            name = entry["standard_name"]
            got = resolve_product(name).standard_name
            if got != name:
                misordered.append(f"{name!r} resolved to {got!r}")
        assert misordered == [], (
            "Registry ordering bug — variants must come before their base model:\n  "
            + "\n  ".join(misordered)
        )