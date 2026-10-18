"""The single enrichment seam: resolve a product and assess a listing.

``store_listings`` calls this once per listing instead of calling the resolver
and the guard separately. That matters because the ML layer attaches *here*:

  1. **Rules first.** The regex resolver's Tier-1/2 match and the guard's hard
     rejects are authoritative — deterministic, auditable, and free.
  2. **The model only fills the gaps.** A trained resolver is consulted only for
     titles the rules left at Tier-3 (unknown), and a trained guard only for
     listings the rules did not flag.

With ``CFG.ml.enabled`` False (the default) this returns exactly the pre-ML
behaviour, so the scraper and viewer are unaffected when no model is present.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import CFG
from app.engines.listing_guard import assess_listing
from app.engines.resolver import resolve_product
from app.models.schema import CanonicalProduct

logger = logging.getLogger(__name__)

# The guard's binary label contract, shared with app/ml/guard_model.py.
KEEP = "keep"
REJECT = "reject"

_RESOLVER_MODEL = None
_GUARD_MODEL = None
_LOADED = False


def _load_models() -> None:
    """Lazily load trained models once, if the ML layer is enabled.

    Import errors (no scikit-learn) and missing/corrupt model files are treated
    as "no model", never as a hard failure: enrichment must keep working.
    """
    global _RESOLVER_MODEL, _GUARD_MODEL, _LOADED
    if _LOADED:
        return
    _LOADED = True
    if not CFG.ml.enabled:
        return
    try:
        from app.ml.guard_model import GuardModel
        from app.ml.tfidf_model import TfidfPredictor
    except ImportError:
        logger.info("ML layer enabled but scikit-learn is not installed; "
                    "falling back to the rule engine.")
        return

    resolver_path = CFG.ml.models_dir / "resolver_model.pkl"
    guard_path = CFG.ml.models_dir / "guard_model.pkl"
    if resolver_path.exists():
        try:
            _RESOLVER_MODEL = TfidfPredictor.load(resolver_path)
        except Exception as exc:  # a stale pickle must not break ingestion
            logger.warning("Could not load resolver model: %s", exc)
    if guard_path.exists():
        try:
            _GUARD_MODEL = GuardModel.load(guard_path)
        except Exception as exc:
            logger.warning("Could not load guard model: %s", exc)


def reset_model_cache() -> None:
    """Forget cached models — used by tests to re-read config and disk."""
    global _RESOLVER_MODEL, _GUARD_MODEL, _LOADED
    _RESOLVER_MODEL = _GUARD_MODEL = None
    _LOADED = False


def _model_product(title: str) -> Optional[str]:
    """A confident model product for *title*, or None to keep the rule result."""
    model = _RESOLVER_MODEL
    if model is None or not title:
        return None
    predictions = model.predict([title])
    if not predictions:
        return None
    label = predictions[0]
    if label in (CFG.ml.unknown_label, CFG.ml.other_label):
        return None

    proba = model.predict_proba([title])
    classes = getattr(model, "classes", None)
    if proba and classes and label in classes:
        if proba[0][classes.index(label)] < CFG.ml.resolver_min_confidence:
            return None
    return label


def _model_rejects(title: str) -> bool:
    """Whether the trained guard wants *title* rejected at the chosen threshold."""
    model = _GUARD_MODEL
    if model is None or not title:
        return False
    proba = model.predict_proba([title])
    classes = getattr(model, "classes", None)
    if proba and classes and REJECT in classes:
        return proba[0][classes.index(REJECT)] >= CFG.ml.guard_reject_threshold

    predictions = model.predict([title])
    return bool(predictions) and predictions[0] == REJECT


def resolve_and_assess(title: str, product: Optional[CanonicalProduct] = None
                       ) -> tuple[CanonicalProduct, Optional[str]]:
    """Resolve *title* to a product and return any guard rejection reason.

    Returns:
        ``(product, flag_reason)`` where ``flag_reason`` is None for a listing
        that looks like a real single item.
    """
    _load_models()

    if product is None:
        product = resolve_product(title)

    # A model is only worth asking when the rules gave up.
    if product.category == "unknown":
        predicted = _model_product(title)
        if predicted:
            product = CanonicalProduct(
                standard_name=predicted,
                category=resolve_product(predicted).category,
                resolved_from=title,
            )

    reason = assess_listing(title, product)
    if reason is None and _model_rejects(title):
        reason = "ml_guard"
    return product, reason
