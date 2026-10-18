"""Source Adapters for all marketplaces."""
from __future__ import annotations
import importlib.util
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
from app.models.schema import Platform, RawListing
logger = logging.getLogger(__name__)
_PARENT = Path(__file__).resolve().parent.parent.parent

_PROJECT_DIRS = {
    "kijiji": _PARENT / "kijiji-scraper",
    "craigslist": _PARENT / "craigslist-scraper",
}

# Every scraper project ships its own copies of these top-level modules. Because
# they are imported by *absolute* name (``from config import load_config``),
# whichever project loads first would otherwise win and the second project would
# silently import the first project's modules.
_SHARED_MODULE_NAMES = ("config", "models", "scraper", "browser")


def _import_scraper(name):
    """Import a scraper module from its project directory by full path.

    Isolates each scraper project's top-level modules so that loading Kijiji and
    Craigslist in the same process cannot cross-contaminate.
    """
    import sys

    proj = _PROJECT_DIRS[name]
    proj_str = str(proj)

    # Evict any cached module that actually came from one of the scraper
    # projects, so this project's own `config`/`models`/`scraper` are imported.
    for mod_name in _SHARED_MODULE_NAMES:
        cached = sys.modules.get(mod_name)
        cached_file = getattr(cached, "__file__", None) if cached is not None else None
        if cached_file and any(
            str(d) in cached_file for d in _PROJECT_DIRS.values()
        ):
            del sys.modules[mod_name]

    # Put this project first so its modules shadow the others' on sys.path.
    if proj_str in sys.path:
        sys.path.remove(proj_str)
    sys.path.insert(0, proj_str)

    p = proj / "scraper.py"
    spec = importlib.util.spec_from_file_location(f"{name}_scraper", str(p))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

class SourceAdapter(ABC):
    @property
    @abstractmethod
    def platform(self) -> Platform: ...
    @abstractmethod
    def search(self, query, max_pages=3, **kwargs) -> list: ...
    @abstractmethod
    def fetch_detail(self, url) -> Optional[RawListing]: ...
    @abstractmethod
    def check_health(self) -> dict: ...

_REGISTRY = {}
def register_source(platform):
    def wrapper(cls): _REGISTRY[platform] = cls; return cls
    return wrapper
def get_adapter(platform):
    cls = _REGISTRY.get(platform)
    if not cls: raise ValueError(f"No adapter for {platform}")
    return cls()

@register_source(Platform.kijiji)
class KijijiAdapter(SourceAdapter):
    @property
    def platform(self): return Platform.kijiji
    def search(self, query, max_pages=3, **kwargs):
        """Scrape Kijiji.

        Pass ``search_terms=[...]`` (or ``query="ALL"``) to sweep the configured
        GPU term list and union the results, which yields a far larger corpus
        than a single broad query.
        """
        try:
            mod = _import_scraper("kijiji")
            scraper = mod.KijijiScraper()
            high_end_only = kwargs.get("high_end_only", True)
            search_terms = kwargs.get("search_terms")

            if search_terms or (isinstance(query, str) and query.upper() == "ALL"):
                listings = scraper.scrape_terms(
                    search_terms=search_terms, max_pages=max_pages,
                    high_end_only=high_end_only,
                )
            else:
                listings = scraper.scrape(
                    search_term=query, max_pages=max_pages,
                    high_end_only=high_end_only,
                )
            return [RawListing(source=Platform.kijiji, listing_id=l.listing_id or "",
                title=l.title, price_cad=l.price_cad, url=l.url,
                location=l.location, description=l.description,
                date_posted=l.date_posted, image_urls=l.image_urls,
                fulfillment=getattr(l, "fulfillment", ""),
                condition=getattr(l, "condition", ""),
                payment=getattr(l, "payment", ""),
                seller_id=getattr(l, "seller_id", ""),
                seller_type=getattr(l, "seller_type", ""),
                price_drop=getattr(l, "price_drop", False),
                scraped_at=l.scraped_at) for l in listings]
        except Exception as e:
            logger.error(f"Kijiji error: {e}"); return []
    def fetch_detail(self, url): return None
    def check_health(self): return {"status":"ok","source":"kijiji"}

@register_source(Platform.craigslist)
class CraigslistAdapter(SourceAdapter):
    @property
    def platform(self): return Platform.craigslist
    def search(self, query, max_pages=3, **kwargs):
        """Scrape Craigslist via a headless browser (Playwright).

        Craigslist search pages are JavaScript-rendered — a plain HTTP GET
        returns an ~8 KB shell with zero rows — so the scraper drives the
        locally-installed Chrome. Pass ``category`` to target one section, or
        omit it to sweep the sections configured in ``config.yaml``.
        """
        try:
            mod = _import_scraper("craigslist")
            scraper = mod.CraigslistScraper()
            try:
                listings = scraper.scrape(
                    search_term=query,
                    category=kwargs.get("category"),
                    location=kwargs.get("location", "toronto"),
                    max_pages=max_pages,
                    fetch_details=kwargs.get("fetch_details", False),
                )
            finally:
                scraper.close()
            return [RawListing(source=Platform.craigslist, listing_id=l.listing_id or "",
                title=l.title, price_cad=l.price_cad, url=l.url,
                location=l.location, description=l.description,
                date_posted=l.date_posted, image_urls=l.image_urls,
                fulfillment="pickup",       # Craigslist is local, in-person only
                scraped_at=l.scraped_at) for l in listings]
        except Exception as e:
            logger.error(f"CL error: {e}"); return []
    def fetch_detail(self, url): return None
    def check_health(self): return {"status":"ok","source":"craigslist","engine":"playwright"}


# Importing the newer adapters registers them; they import this module's
# SourceAdapter/register_source, so this must stay at the bottom.
from app.adapters import ebay as _ebay  # noqa: E402,F401
from app.adapters import reddit_chs as _reddit  # noqa: E402,F401
