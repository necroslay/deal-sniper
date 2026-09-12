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
    * at least ``CFG.alert_gate.min_comps`` comps backed the valuation

    The comp-count condition matters: a large "discount" computed from two comps
    is noise, so the gate refuses to act on thin evidence.
    """
    gate = CFG.alert_gate
    ds = output.deal_score
    return (
        ds.expected_profit_cad >= gate.min_expected_profit_cad
        and ds.margin_pct >= gate.min_margin_pct * 100
        and ds.confidence_level.value in ("ALERT_IMMEDIATE", "ALERT_CAUTION")
        and output.fair_value.comps_used >= gate.min_comps
    )
