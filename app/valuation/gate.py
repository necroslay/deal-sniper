"""Alert gate — the single definition of "this deal is worth alerting on".

This rule used to be implemented twice: once as ``IngestionPipeline.should_alert``
and once inline in ``db/store.py`` to set the ``alert_sent`` column. Two copies of
the same business rule drift apart the moment a threshold changes — exactly what
the project's "config in one place" principle exists to prevent. Both callers now
delegate here.
"""

from __future__ import annotations

from app.config import CFG
from app.models.schema import ValuationOutput


def passes_alert_gate(output: ValuationOutput) -> bool:
    """Return True if *output* should raise an alert.

    All four conditions must hold:

    * expected profit ≥ ``CFG.alert_gate.min_expected_profit_cad``
    * margin ≥ ``CFG.alert_gate.min_margin_pct``
    * confidence tier is ``ALERT_IMMEDIATE`` or ``ALERT_CAUTION``
    * evidence is sufficient for the valuation's *basis*

    The last condition is where the two bases differ. A comp-backed valuation
    needs at least ``CFG.alert_gate.min_comps`` comps — a large "discount"
    computed from two sales is noise. A retail-anchored valuation has no comps by
    construction (``basis="retail"``, ``comps_used=0``), so requiring comps would
    mean it could never alert; it is gated on ``CFG.retail.allow_alerts``
    instead, and its confidence is already capped below ``ALERT_IMMEDIATE``.
    """
    gate = CFG.alert_gate
    ds = output.deal_score
    basis = output.fair_value.basis

    if basis == "retail":
        evidence_ok = CFG.retail.enabled and CFG.retail.allow_alerts
    else:
        evidence_ok = output.fair_value.comps_used >= gate.min_comps

    return (
        ds.expected_profit_cad >= gate.min_expected_profit_cad
        and ds.margin_pct >= gate.min_margin_pct * 100
        and ds.confidence_level.value in ("ALERT_IMMEDIATE", "ALERT_CAUTION")
        and evidence_ok
    )
