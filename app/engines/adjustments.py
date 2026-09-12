"""Item-level value adjustments — penalise listings that are missing things.

``CFG.item_adjustments`` defines what each deficiency is worth (missing box,
missing charger, battery health, cosmetic damage), but nothing ever read it:
``pipeline/ingest.py`` passed ``adjustments=[]`` to ``compute_fmv``, so the
``± Σ adjustments`` term in the FMV formula was always zero. This module detects
the deficiencies from the listing text so the configured values actually apply.

Two guards keep detection conservative:

* **One match per family.** A "scratched screen" should not also trigger the
  generic minor-damage deduction. Within a family the most severe matching rule
  wins and the rest are skipped.
* **Negation-aware.** "no scratches" and "not broken" describe a *good* item, so a
  keyword preceded by a negator is ignored.
"""

from __future__ import annotations

from app.config import CFG

# Words that invert the meaning of a following keyword.
_NEGATORS = ("no ", "not ", "never ", "without ", "zero ", "free of ", "non-",
             "won't ", "wont ", "doesn't ", "does not ", "isn't ", "is not ")

# Keywords that already express a deficiency in themselves ("no box",
# "not come with a box"). A negator from an *earlier* phrase must not cancel them.
_NEGATED_PREFIXES = ("no ", "not ", "never ", "without ", "non-",
                     "won't ", "wont ", "doesn't ", "does not ")

# Clause boundaries. A negator only applies within its own clause, so
# "no box, no cables" reports both — the "no " before "box" must not reach across
# the comma and cancel "no cables".
_CLAUSE_BREAKS = (",", ".", ";", "!", "?", " but ", " and ", " or ", " with ",
                  " plus ", " - ", " / ", "\n")

# Deficiency families. Within a family, members are listed most-severe-first and
# only the first match applies, so a single flaw is never double-counted.
FAMILIES: dict[str, list[tuple[str, tuple[str, ...]]]] = {
    "damage": [
        ("cosmetic_damage_major", (
            "cracked", "cracked screen", "broken screen", "crack in",
            "dented", "dent ", "major damage", "heavy damage",
            "heavy scratches", "deep scratch", "bent",
        )),
        ("scratched_screen", (
            "scratched screen", "screen scratch", "scratched display",
            "scratches on the screen",
        )),
        ("cosmetic_damage_minor", (
            "scratch", "scratch ", "scratches", "scuffed", "scuffs",
            "cosmetic wear", "minor wear", "signs of wear",
        )),
    ],
    "box": [
        ("missing_original_box", (
            "no box", "without box", "without the box", "no original box",
            "no retail box", "box not included", "missing box", "box is missing",
            "no packaging", "no original packaging", "not come with a box",
            "won't come with", "wont come with", "doesn't come with",
            "does not come with", "will not come with",
        )),
    ],
    "charger": [
        ("missing_charger", (
            "no charger", "without charger", "missing charger",
            "charger not included", "no power adapter", "no power supply",
        )),
    ],
    "battery": [
        ("battery_health_below_80", (
            "battery health 7", "battery health 6", "battery health 5",
            "battery health 4", "battery health 3", "bad battery",
            "battery needs", "battery is dead", "dead battery",
            "battery below 80", "poor battery life",
        )),
    ],
    "cables": [
        ("no_cables", ("no cables", "no cable", "cables not included")),
    ],
    "accessories": [
        ("no_accessories", (
            "no accessories", "accessories not included", "no extras",
            "no controller", "no controllers",
        )),
    ],
}


def _clause_prefix(text: str, idx: int) -> str:
    """Return the text between the last clause boundary and *idx*."""
    start = 0
    for br in _CLAUSE_BREAKS:
        j = text.rfind(br, 0, idx)
        if j != -1:
            start = max(start, j + len(br))
    return text[start:idx]


def _keyword_present(text: str, keyword: str) -> bool:
    """True if *keyword* occurs without being negated within its own clause.

    ``"no scratches"`` and ``"not broken"`` describe a good item, so a negated
    occurrence must not trigger a deduction. Keywords that are themselves negated
    phrases (``"no box"``) are exempt from the look-back.
    """
    if any(keyword.startswith(p) for p in _NEGATED_PREFIXES):
        return keyword in text

    start = 0
    while True:
        idx = text.find(keyword, start)
        if idx == -1:
            return False
        if not any(neg in _clause_prefix(text, idx) for neg in _NEGATORS):
            return True
        start = idx + 1


def detect_adjustment_reasons(title: str, description: str = "") -> list[str]:
    """Return the ``ItemAdjustments`` field names detected in the listing text."""
    text = f"{title} {description}".lower()
    reasons: list[str] = []
    for members in FAMILIES.values():
        for attr, keywords in members:
            if any(_keyword_present(text, kw) for kw in keywords):
                reasons.append(attr)
                break  # one per family — never double-count a single flaw
    return reasons


def detect_adjustments(title: str, description: str = "") -> list[float]:
    """Return the configured CAD adjustments (negative) for detected deficiencies.

    Ready to pass straight to :func:`app.valuation.fair_value.compute_fmv`.
    """
    return [getattr(CFG.item_adjustments, name)
            for name in detect_adjustment_reasons(title, description)]
