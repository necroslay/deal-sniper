"""
FastAPI entry point for the valuation engine.

Provides:
  - Health check
  - Manual valuation trigger
  - Feedback capture
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.config import CFG
from app.models.schema import (
    CompBand,
    FeedbackEvent,
    Platform,
    RawListing,
    ValuationOutput,
)
from app.pipeline.ingest import IngestionPipeline
from app.alerting.notifier import Notifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Resale Valuation Engine",
    version="1.0.0",
    description="Comp-driven resale valuation operating system",
)

pipeline = IngestionPipeline()


# ── Request Schemas ─────────────────────────────────────────────────────────

class ValuationRequest(BaseModel):
    """Request body for the /valuate endpoint."""
    source: str = "ebay"
    title: str
    price_cad: float
    url: str = ""
    location: str = ""
    description: str = ""
    date_posted: Optional[str] = None
    listing_id: str = ""
    image_urls: list[str] = []
    travel_distance_km: float = 0.0

    # Optional: pre-supplied sold comps for valuation
    comps: list = []  # list of SoldComp-like dicts
    comp_band: Optional[dict] = None  # pre-computed CompBand


# ── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "Resale Valuation Engine", "version": "1.0.0", "status": "ok"}


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "currency": CFG.currency,
        "country": CFG.country,
        "config_sections": [
            "platform_fees", "shipping_costs", "condition_multipliers",
            "return_reserve", "travel_costs", "holding_costs",
            "item_adjustments", "score_weights", "penalties",
            "comp", "alert_gate", "scraper_sources",
        ],
    }


@app.post("/valuate", response_model=ValuationOutput)
def valuate_listing(req: ValuationRequest) -> ValuationOutput:
    """
    Run the full valuation pipeline on a single listing.

    Provide either:
      - comps: list of SoldComp-like dicts (engine aggregates them), or
      - comp_band: pre-computed CompBand dict (skips aggregation)
    """
    try:
        platform = Platform(req.source)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid source: {req.source}")

    raw = RawListing(
        source=platform,
        listing_id=req.listing_id,
        title=req.title,
        price_cad=req.price_cad,
        url=req.url,
        location=req.location,
        description=req.description,
        date_posted=req.date_posted,
        image_urls=req.image_urls,
    )

    # Parse optional comps / comp_band
    comps = None
    comp_band = None
    if req.comp_band:
        comp_band = CompBand(**req.comp_band)
    elif req.comps:
        from app.models.schema import SoldComp
        comps = [SoldComp(**c) if isinstance(c, dict) else c for c in req.comps]

    output = pipeline.process(
        raw_listing=raw,
        sold_comps=comps,
        comp_band=comp_band,
        travel_distance_km=req.travel_distance_km,
    )

    return output


@app.post("/feedback")
def submit_feedback(event: FeedbackEvent):
    """
    Record user feedback on a decision. Builds the future ML training set.
    """
    logger.info(f"Feedback: {event.outcome} for decision {event.decision_id}")
    # TODO: persist to database
    return {"status": "recorded", "decision_id": event.decision_id, "outcome": event.outcome}


@app.post("/alert/{decision_id}")
def send_alert(decision_id: str):
    """
    Manually re-send an alert for a previous decision.
    """
    logger.info(f"Re-alert request for decision {decision_id}")
    # TODO: load from DB and re-send
    return {"status": "not_implemented", "decision_id": decision_id}