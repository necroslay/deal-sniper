"""User tuning without editing code: ``watchlist.yaml`` at the repository root.

Every key is optional; anything you leave out keeps its default from
``app/config.py``. Unknown keys are reported rather than silently ignored, so a
typo can't quietly leave a limit unset.

    alerts:
      include: ["RTX 4090", "RTX 5080", "Laptop RTX"]   # substring of product key
      exclude: ["GTX"]
      max_landed_cad: 2500        # price + shipping you're willing to pay
      min_discount: 0.25
      max_risk: 50
      require_shippable: true
    profit:
      resale_fee_rate: 0.135
    logistics:
      shipping_cad: {gpu: 25, laptop: 45}
"""

from __future__ import annotations

import logging
from dataclasses import fields, is_dataclass
from pathlib import Path

import yaml

from app.config import CFG

logger = logging.getLogger("watchlist")

WATCHLIST = Path(__file__).resolve().parent.parent / "watchlist.yaml"


def _apply(section, values: dict, path: str, problems: list) -> None:
    known = {f.name for f in fields(section)}
    for key, value in values.items():
        if key not in known:
            problems.append(f"{path}.{key}: unknown setting")
            continue
        current = getattr(section, key)
        if isinstance(current, dict) and isinstance(value, dict):
            current.update(value)
        elif isinstance(current, bool) != isinstance(value, bool) and not (
                current is None):
            problems.append(f"{path}.{key}: expected {type(current).__name__}")
        elif isinstance(current, (int, float)) and not isinstance(current, bool):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                problems.append(f"{path}.{key}: expected a number")
            else:
                setattr(section, key, type(current)(value))
        elif isinstance(current, list):
            setattr(section, key, [str(v) for v in value]
                    if isinstance(value, list) else [str(value)])
        else:
            setattr(section, key, value)


def apply_watchlist(path: Path = WATCHLIST) -> list[str]:
    """Overlay *path* onto ``CFG``. Returns a list of problems (empty = clean)."""
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return [f"could not read {path.name}: {exc}"]
    if not isinstance(data, dict):
        return [f"{path.name}: expected a mapping at the top level"]

    problems: list[str] = []
    for name, values in data.items():
        section = getattr(CFG, name, None)
        if section is None or not is_dataclass(section) or not isinstance(values, dict):
            problems.append(f"{name}: not a settings section")
            continue
        _apply(section, values, name, problems)
    for problem in problems:
        logger.warning("watchlist.yaml: %s", problem)
    return problems
