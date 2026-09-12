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


def refresh_comps(standard_name: Optional[str] = None,
                  condition: Optional[str] = None):
    """Refresh comps from retired (presumed-sold) listings.

    There is no reachable *free* source of real sold comps (see
    ``PROJECT_STATUS.md`` §5), so the refresh available today is the lifecycle
    inference: listings that disappeared are converted into inferred comps and
    stored. Inferred comps remain excluded from valuation unless
    ``lifecycle.use_inferred_comps`` is enabled.

    Args:
        standard_name: Retained for a future real-comp source. Currently advisory
            only — inference operates over whatever has been retired.
        condition: Optional condition grade filter (also currently advisory).

    Returns:
        ``{"inferred_built": n, "inferred_added": n}``.
    """
    from app.db.store import Store
    from app.pipeline.lifecycle_cycle import refresh_inferred_comps

    logger.info(f"Task: refresh_comps(standard_name='{standard_name}', condition={condition})")
    store = Store()
    store.connect()
    try:
        summary = refresh_inferred_comps(store)
        logger.info(f"Comp refresh complete: {summary}")
        return summary
    except Exception as exc:  # pragma: no cover - defensive
        logger.error(f"Comp refresh failed: {exc}")
        return {"inferred_built": 0, "inferred_added": 0}
    finally:
        store.close()


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