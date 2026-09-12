"""Condition Classifier — grades listing condition from title + description."""
from __future__ import annotations
import re, logging
from typing import Optional
from app.models.schema import ConditionGrade
logger = logging.getLogger(__name__)

CONDITION_KEYWORDS = {
    "brand_new": [
        {"keywords": ["brand new", "brand-new"], "weight": 1.0},
        {"keywords": ["bnib", "new in box", "never used", "factory sealed", "sealed"], "weight": 0.95},
        {"keywords": ["new", "unopened", "still sealed", "nib"], "weight": 0.80},
        {"keywords": ["never opened", "still in box"], "weight": 0.90},
    ],
    "open_box_like_new": [
        {"keywords": ["open box", "open-box"], "weight": 1.0},
        {"keywords": ["like new", "like-new", "mint", "mint condition"], "weight": 0.95},
        {"keywords": ["excellent condition"], "weight": 0.85},
        {"keywords": ["barely used", "lightly used"], "weight": 0.80},
        {"keywords": ["still has warranty", "under warranty"], "weight": 0.75},
        {"keywords": ["used for", "only used"], "weight": 0.70},
    ],
    "used_good": [
        {"keywords": ["good condition", "well maintained"], "weight": 0.90},
        {"keywords": ["used", "pre-owned", "pre owned", "gently used"], "weight": 0.80},
        {"keywords": ["working", "functional", "no issues", "works great"], "weight": 0.75},
        {"keywords": ["never mined", "no mining", "tested", "fully tested"], "weight": 0.85},
    ],
    "acceptable": [
        {"keywords": ["acceptable", "fair condition", "shows wear"], "weight": 0.70},
        {"keywords": ["scratch", "scratched", "scratches", "cosmetic"], "weight": 0.50},
        {"keywords": ["worn", "used condition", "needs"], "weight": 0.60},
    ],
    "for_parts": [
        {"keywords": ["for parts", "parts only", "repair"], "weight": 1.0},
        {"keywords": ["not working", "broken", "defective", "faulty", "dead", "fried"], "weight": 0.95},
        {"keywords": ["as is", "as-is", "untested", "damaged"], "weight": 0.85},
        {"keywords": ["no power", "won't turn on", "doesn't work"], "weight": 0.95},
    ],
}

UNCERTAINTY_SIGNALS = [r"\?", r"not sure", r"don't know", r"unknown condition", r"no description"]

DESC_BOOST = [(0, 0.0), (20, 0.1), (100, 0.15), (500, 0.2)]

class ConditionResult:
    grade: ConditionGrade
    confidence: float
    matched_keyword: str = ""
    signals: list[str]
    def __init__(self, grade, confidence, matched_keyword="", signals=None):
        self.grade = grade; self.confidence = confidence
        self.matched_keyword = matched_keyword; self.signals = signals or []

def classify_condition(title: str, description: str = "") -> ConditionResult:
    combined = (title + " " + description).lower()
    signals = []
    highest_weight = 0.0
    best_grade = ConditionGrade.unknown
    best_keyword = ""

    for grade_str, rule_list in CONDITION_KEYWORDS.items():
        for rule in rule_list:
            for kw in rule["keywords"]:
                if kw in combined:
                    if rule["weight"] > highest_weight:
                        highest_weight = rule["weight"]
                        best_grade = ConditionGrade(grade_str)
                        best_keyword = kw
                    break

    if best_grade == ConditionGrade.unknown:
        return ConditionResult(ConditionGrade.unknown, 0.0, signals=["no condition keywords found"])

    confidence = highest_weight
    match_count = sum(1 for _, rl in CONDITION_KEYWORDS.items() for r in rl for kw in r["keywords"] if kw in combined)
    if match_count >= 2: confidence = min(1.0, confidence + 0.1)
    if match_count >= 3: confidence = min(1.0, confidence + 0.05)

    desc_len = len(description.strip())
    for threshold, boost in DESC_BOOST:
        if desc_len >= threshold: confidence = min(1.0, confidence + boost)

    if "never mined" in combined or "no mining" in combined:
        confidence = min(1.0, confidence + 0.05)

    for signal in UNCERTAINTY_SIGNALS:
        if re.search(signal, combined, re.IGNORECASE):
            signals.append(signal); confidence = max(0.0, confidence - 0.2)

    if desc_len < 10:
        signals.append("no_description")
        if best_grade in (ConditionGrade.used_good, ConditionGrade.brand_new):
            confidence = max(0.0, confidence - 0.15)

    logger.debug(f"Condition: '{title[:40]}' -> {best_grade.value} (conf={confidence:.2f}, desc_len={desc_len})")
    return ConditionResult(best_grade, round(confidence, 2), best_keyword, signals)