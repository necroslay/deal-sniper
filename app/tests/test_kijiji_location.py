"""Tests for the Kijiji scraper's config-driven search scope.

The scraper used to hardcode the GTA path in ``_fetch_page_graphql``, so the
``url_template`` in ``config.yaml`` was silently ignored and changing the scope
did nothing. The URL is now built from ``region`` + ``location_id``; these tests
pin the two scopes that matter.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CONFIG_PY = (Path(__file__).resolve().parents[2]
              / "kijiji-scraper" / "config.py")


def _load_kijiji_config():
    """Import ``kijiji-scraper/config.py`` by path, under a private name.

    Loaded via ``importlib`` rather than a normal import so it cannot collide
    with this package's own ``config`` module -- every scraper project defines a
    top-level ``config``, which is exactly the shadowing hazard the adapter
    works around.

    The module **must** be registered in ``sys.modules`` before it is executed:
    ``dataclasses`` resolves each field's annotation string through
    ``sys.modules[cls.__module__]``, and an unregistered module makes that lookup
    return ``None`` ("'NoneType' object has no attribute '__dict__'"). The
    scraper's own path avoids this by importing ``config`` normally.
    """
    spec = importlib.util.spec_from_file_location(
        "kijiji_config_under_test", _CONFIG_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestSearchScope:
    def test_canada_scope_uses_l0_for_every_location(self):
        cfg = _load_kijiji_config().SearchConfig(region="canada",
                                                 location_id="0")
        assert cfg.search_path("graphics-card") == "/b-canada/graphics-card/k0l0"
        assert cfg.search_path("graphics-card", page=2) == \
            "/b-canada/graphics-card/page-2/k0l0"

    def test_location_segment_is_never_dropped(self):
        """A blank location must not produce the "k0"-only form.

        That form is not a scoped search: Kijiji answers a GPU query with cars
        and jewellery while still reporting thousands of hits.
        """
        cfg = _load_kijiji_config().SearchConfig(region="canada",
                                                 location_id="")
        path = cfg.search_path("graphics-card")
        assert path == "/b-canada/graphics-card/k0l0"
        assert not path.endswith("/k0")

    def test_location_scope_adds_the_l_suffix(self):
        cfg = _load_kijiji_config().SearchConfig(
            region="gta-greater-toronto-area", location_id="1700272")
        assert cfg.search_path("gpu") == \
            "/b-gta-greater-toronto-area/gpu/k0l1700272"
        assert cfg.search_path("gpu", page=3) == \
            "/b-gta-greater-toronto-area/gpu/page-3/k0l1700272"

    def test_search_url_includes_the_host(self):
        cfg = _load_kijiji_config().SearchConfig()
        assert cfg.search_url("rtx-4090").startswith("https://www.kijiji.ca/b-")

    def test_the_shipped_config_is_canada_wide(self):
        cfg = _load_kijiji_config().load_config()
        assert cfg.search.region == "canada"
        assert cfg.search.location_id == "0"
        assert cfg.search.search_url("gpu") == \
            "https://www.kijiji.ca/b-canada/gpu/k0l0"
