"""The ``Predictor`` interface and the deterministic baselines.

Everything the harness can score — the regex resolver, the listing guard, a
TF-IDF classifier, a future embedding model — is wrapped behind the same tiny
interface. That is what lets the harness and the ingestion seam stay unchanged
as models are added.

Two prediction tasks are defined:

* **resolver** — map a title to a canonical product name, or ``__none__`` when
  the title names no known product. Multiclass.
* **guard** — decide whether a title is a real single-item listing (``keep``) or
  junk the guard should reject (``reject``). Binary.

Both baselines delegate to the existing pure engine functions, so the baseline
numbers are exactly the current production behaviour.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from app.config import CFG
from app.engines.listing_guard import assess_listing
from app.engines.resolver import resolve_product

KEEP = "keep"
REJECT = "reject"


@runtime_checkable
class Predictor(Protocol):
    """Maps a batch of texts to one label per text.

    ``predict_proba`` may return ``None`` when the underlying model exposes no
    probabilities (the rule baselines do not), which the harness treats as
    "no scores available" rather than a zero-probability prediction.
    """

    name: str

    def predict(self, texts: list[str]) -> list[str]: ...

    def predict_proba(self, texts: list[str]) -> Optional[list[list[float]]]: ...


# ── Registry ────────────────────────────────────────────────────────────────
# A name -> class map so the CLI (and later the model loader) can look models up
# without importing every module eagerly.

_PREDICTORS: dict[str, type] = {}


def register_predictor(name: str):
    """Class decorator: register a predictor under *name*."""

    def wrapper(cls: type) -> type:
        _PREDICTORS[name] = cls
        return cls

    return wrapper


def available_predictors() -> dict[str, type]:
    """Return a copy of the registered predictor classes, by name."""
    return dict(_PREDICTORS)


# ── Baselines ───────────────────────────────────────────────────────────────


@register_predictor("resolver-baseline")
class ResolverBaseline:
    """The regex waterfall in ``engines/resolver.py``, adapted to ``Predictor``.

    Tier-3 fallbacks (``category == "unknown"``) collapse to ``unknown_label``
    rather than leaking the raw title as a "prediction".
    """

    name = "resolver-baseline"

    def __init__(self, unknown_label: Optional[str] = None):
        self.unknown_label = unknown_label or CFG.ml.unknown_label

    def predict(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for text in texts:
            product = resolve_product(text or "")
            if product.category == "unknown":
                out.append(self.unknown_label)
            else:
                out.append(product.standard_name)
        return out

    def predict_proba(self, texts: list[str]) -> Optional[list[list[float]]]:
        return None


@register_predictor("guard-baseline")
class GuardBaseline:
    """The rule-based listing guard, adapted to a binary ``keep``/``reject``."""

    name = "guard-baseline"

    def predict(self, texts: list[str]) -> list[str]:
        out: list[str] = []
        for text in texts:
            product = resolve_product(text or "")
            reason = assess_listing(text or "", product)
            out.append(REJECT if reason else KEEP)
        return out

    def predict_proba(self, texts: list[str]) -> Optional[list[list[float]]]:
        return None
