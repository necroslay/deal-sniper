"""
Celery task definitions for scheduled scraping + ingestion.

For MVP: simple function-based tasks. Celery beat schedule defined here.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ── Placeholder Task Wrapper ────────────────────────────────────────────────
# When Celery is wired up, these become @celery.task decorated functions.

def scrape_and_ingest(source: str, query: str, **kwargs):
    """
    Celery task: scrape a source, run pipeline, alert on good deals.

    Args:
        source: Marketplace source name (ebay, kijiji, facebook, craigslist)
        query: Search term
    """
    logger.info(f"Task: scrape_and_ingest(source='{source}', query='{query}', kwargs={kwargs})")
    # TODO: Wire up adapters → pipeline → notify
    logger.warning("Celery tasks not yet wired — noop")


def refresh_comps(standard_name: str, condition: Optional[str] = None):
    """
    Celery task: refresh sold comps for a canonical product.

    Args:
        standard_name: Canonical product name (e.g. "RTX 4090")
        condition: Optional condition grade filter
    """
    logger.info(f"Task: refresh_comps(standard_name='{standard_name}', condition={condition})")
    # TODO: eBay sold scraper → comp_engine → store in DB
    logger.warning("Comp refresh task not yet wired — noop")


def cleanup_stale_data(max_age_days: int = 90):
    """
    Celery task: remove listings/comps older than max_age_days.
    """
    logger.info(f"Task: cleanup_stale_data(max_age_days={max_age_days})")
    # TODO: DB cleanup
    logger.warning("Cleanup task not yet wired — noop")


# ── Beat Schedule (celery -A app.scheduler.tasks beat) ──────────────────────

BEAT_SCHEDULE = {
    "scrape-kijiji-graphics": {
        "task": "app.scheduler.tasks.scrape_and_ingest",
        "schedule": 3600.0,  # every hour
        "args": ("kijiji", "graphics-card"),
    },
    "scrape-ebay-active": {
        "task": "app.scheduler.tasks.scrape_and_ingest",
        "schedule": 7200.0,  # every 2 hours
        "args": ("ebay", "rtx 4090"),
        "kwargs": {"sold": False},
    },
    "scrape-ebay-sold-comps": {
        "task": "app.scheduler.tasks.refresh_comps",
        "schedule": 86400.0,  # daily
        "args": ("RTX 4090", None),
    },
    "scrape-facebook-marketplace": {
        "task": "app.scheduler.tasks.scrape_and_ingest",
        "schedule": 3600.0,
        "args": ("facebook", "graphics card"),
    },
    "cleanup-old-data": {
        "task": "app.scheduler.tasks.cleanup_stale_data",
        "schedule": 604800.0,  # weekly
        "args": (90,),
    },
}