"""Guards against listings the resolver mis-identifies as a product.

``resolve_product`` matches canonical aliases as bare substrings, so any listing
that merely *mentions* a product resolves to that product and is then valued as if
it were the product itself. That produced alerts like a $20 GPU power **cable**
"worth" $1,893 because its title listed the cards it fits.

Three distinct failure modes are rejected here, before any valuation happens:

* ``accessory_or_part`` — the item is an accessory/part (a cable, dock, cooler).
* ``platform_accessory`` — "X for PS5"; something made *for* the platform.
* ``component_in_host_item`` — a GPU inside a laptop, not a GPU for sale.
* ``multi_item_lot`` — several distinct products in one listing, i.e. a lot.

Checks run against the **title** only. Descriptions routinely mention accessories
that are *included* ("comes with 2 controllers"), which is a positive signal, not a
reason to reject the listing.
"""

from __future__ import annotations

import re
from typing import Optional

from app.engines.resolver import find_products
from app.models.schema import CanonicalProduct

# Nouns that indicate the listing is a part or accessory rather than a product,
# even when it names the product it fits. Word-boundaried, so "stand" does not
# fire on "standard".
ACCESSORY_PATTERNS = re.compile(
    r"\b("
    r"cable|adapter|dock|mount|bracket|stand|backplate|riser|"
    r"water ?block|cooler|heatsink|shroud|faceplate|capture card|"
    r"screen protector|thermal pad|keycaps|empty box|box only"
    r")\b",
    re.IGNORECASE,
)

# "X for PS5" / "for Switch" — a game or accessory made FOR the platform.
_COMPAT_PATTERNS = re.compile(
    r"\bfor\s+(ps[45]|playstation|xbox|switch|nintendo|rtx|gtx|radeon)\b",
    re.IGNORECASE,
)

# Larger items that *contain* a component. A GPU inside a laptop is not a GPU.
_HOST_PATTERNS = re.compile(
    r"\b("
    r"laptop|notebook|desktop|prebuilt|pre-built|all-in-one|workstation|"
    r"gaming pc|pc build|tower"
    r")\b",
    re.IGNORECASE,
)

# Only these categories are components that can live inside a host item.
_COMPONENT_CATEGORIES = frozenset({"gpu", "cpu", "memory", "storage"})


def assess_listing(title: str, product: Optional[CanonicalProduct] = None) -> Optional[str]:
    """Return a rejection reason for *title*, or ``None`` if it looks like a real listing."""
    if not title:
        return None

    # 1. A part or accessory that happens to name the product it fits.
    if ACCESSORY_PATTERNS.search(title):
        return "accessory_or_part"

    # 2. Something made *for* a platform (usually a game or peripheral).
    if _COMPAT_PATTERNS.search(title):
        return "platform_accessory"

    # 3. The product is a component of a bigger item (e.g. a laptop's GPU).
    if product is not None and product.category in _COMPONENT_CATEGORIES:
        if _HOST_PATTERNS.search(title):
            return "component_in_host_item"

    # 4. More than one distinct product means a lot, not a single item.
    if product is not None and len(find_products(title)) >= 2:
        return "multi_item_lot"

    return None
