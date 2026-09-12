"""Synthetic baseline comps for tests and ``--seed-demo`` runs.

**These are NOT real completed sales.** They are a hand-written price ladder per
product, used so the pipeline has *something* to valuate against when no real
sold comps have been ingested yet. They must never be treated as ground truth:

* the runtime pipeline does **not** import this module — only ``--seed-demo`` and
  the test suite do;
* every seeded comp carries the ``baseline_`` listing-id prefix so a real comp
  can be told apart from a synthetic one in the database;
* the prices are roughly realistic for the Canadian used market, nothing more.

The honest replacement for this table is real eBay sold comps (see WORKSTREAM 1
in the project plan).
"""

from __future__ import annotations

BASELINE_COMPS: dict[str, list[float]] = {
    "RTX 4090": [2200, 2350, 2400, 2450, 2500, 2550, 2600, 2650, 2700, 2750, 2800, 2850, 2900, 3000, 3200],
    "RTX 4080 Super": [1500, 1550, 1600, 1650, 1700, 1750, 1800, 1850, 1900, 1950],
    "RTX 4080": [1200, 1250, 1300, 1350, 1400, 1450, 1500, 1550, 1600, 1650],
    "RTX 4070 Ti Super": [900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350],
    "RTX 4070": [700, 750, 800, 850, 900, 950, 1000, 1050, 1100, 1150],
    "RTX 3080": [450, 480, 500, 520, 540, 560, 580, 600, 620, 650],
    "RTX 3080 Ti": [550, 580, 600, 620, 640, 660, 680, 700, 720, 750],
    "RTX 3070": [350, 380, 400, 420, 440, 460, 480, 500, 520, 540],
    "RTX 3070 Ti": [400, 430, 450, 470, 490, 510, 530, 550, 570, 590],
    "RTX 3060 Ti": [300, 320, 340, 360, 380, 400, 420, 440, 460, 480],
    "RTX 3060": [200, 220, 240, 260, 280, 300, 320, 340, 360, 380],
    "RTX 2070": [180, 200, 220, 240, 260, 280, 300, 320, 340, 360],
    "GTX 1660 Super": [120, 130, 140, 150, 160, 170, 180, 190, 200, 210],
    "GTX 1660": [100, 110, 120, 130, 140, 150, 160, 170, 180, 190],
    "GTX 1070": [100, 110, 120, 130, 140, 150, 160, 170, 180, 190],
    "GTX 1080": [130, 140, 150, 160, 170, 180, 190, 200, 210, 220],
    "GTX 1080 Ti": [180, 200, 220, 240, 260, 280, 300, 320, 340, 360],
    "RX 7900 XTX": [900, 950, 1000, 1050, 1100, 1150, 1200, 1250, 1300, 1350],
    "RX 7900 XT": [750, 800, 850, 900, 950, 1000, 1050, 1100, 1150, 1200],
    "RX 7800 XT": [550, 580, 600, 620, 640, 660, 680, 700, 720, 750],
    "RX 6800 XT": [400, 430, 450, 470, 490, 510, 530, 550, 570, 590],
    "RX 6800": [350, 370, 390, 410, 430, 450, 470, 490, 510, 530],
    "RX 580": [60, 70, 80, 90, 100, 110, 120, 130, 140, 150],
    "PlayStation 5": [350, 370, 380, 400, 420, 430, 440, 450, 460, 480],
    "PS5 Pro": [600, 650, 700, 750, 800, 850, 900, 950, 1000, 1050],
    "Nintendo Switch OLED": [250, 260, 270, 280, 290, 300, 310, 320, 330, 340],
}

# Every synthetic comp's source_listing_id starts with this, so real rows can be
# distinguished from seeded ones with a single query.
SEED_ID_PREFIX = "baseline_"
