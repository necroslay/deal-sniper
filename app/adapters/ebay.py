"""eBay.ca through eBay's official Browse API.

eBay's website serves bot challenges, and its terms forbid scraping, so this uses
the sanctioned route: the free Browse API. It needs a developer keyset
(developer.ebay.com -> Application Keys -> Production). Put them in ``.env``::

    EBAY_CLIENT_ID=...
    EBAY_CLIENT_SECRET=...
    EBAY_POSTAL_CODE=M5V2T6      # optional: makes shipping costs to YOU accurate

With no keys the source is simply switched off and the run carries on.

Why it is worth the setup: every eBay listing ships, the API reports the actual
shipping charge and the seller's feedback score, and eBay's Money Back Guarantee
is real buyer protection. That makes its landed-cost and risk numbers far firmer
than anything scraped from a classifieds site.

This adapter was written against eBay's documented Browse API response shape and
is covered by fixture tests; it has not been exercised against the live service.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from typing import Optional

import requests

from app.adapters.sources import SourceAdapter, register_source
from app.models.schema import Platform, RawListing

logger = logging.getLogger(__name__)

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
SCOPE = "https://api.ebay.com/oauth/api_scope"
GPU_CATEGORY = "27386"        # Graphics / Video Cards
LAPTOP_CATEGORY = "177"       # PC Laptops & Netbooks

# (query, category) pairs swept each run. Specific models beat one broad query.
QUERIES: list[tuple[str, str]] = (
    [(f"rtx {m}", GPU_CATEGORY) for m in (
        "5090", "5080", "5070", "4090", "4080", "4070", "4060", "3090", "3080",
        "3070", "3060")]
    + [("rx 7900", GPU_CATEGORY), ("rx 6800", GPU_CATEGORY),
       ("gtx 1080", GPU_CATEGORY)]
    + [("gaming laptop rtx", LAPTOP_CATEGORY), ("macbook pro m", LAPTOP_CATEGORY),
       ("macbook air m", LAPTOP_CATEGORY), ("thinkpad", LAPTOP_CATEGORY)]
)


def credentials() -> Optional[tuple[str, str]]:
    """``(client_id, client_secret)`` if configured, else None."""
    from app.alerts import load_env_file
    load_env_file()
    cid, secret = os.environ.get("EBAY_CLIENT_ID"), os.environ.get("EBAY_CLIENT_SECRET")
    return (cid, secret) if cid and secret else None


def _float(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_item(item: dict) -> Optional[RawListing]:
    """One ``itemSummaries`` entry -> ``RawListing`` (None if not a fixed-price CAD sale)."""
    price = item.get("price") or {}
    if price.get("currency") != "CAD":
        return None
    amount = _float(price.get("value"))
    item_id = str(item.get("itemId") or "")
    title = (item.get("title") or "").strip()
    if not amount or amount <= 0 or not item_id or not title:
        return None
    if "FIXED_PRICE" not in (item.get("buyingOptions") or []):
        return None                                  # auctions have no firm price

    condition = (item.get("condition") or "").lower()
    if "parts" in condition or "not working" in condition:
        return None

    # Cheapest delivery option the API reports; free shipping is a real $0.
    shipping = None
    options = item.get("shippingOptions") or []
    for opt in options:
        cost = _float((opt.get("shippingCost") or {}).get("value"))
        if cost is not None and (shipping is None or cost < shipping):
            shipping = cost
    if options and shipping is None:
        shipping = None                              # present but unpriced: estimate

    seller = item.get("seller") or {}
    image = (item.get("image") or {}).get("imageUrl")
    extra = [i.get("imageUrl") for i in (item.get("additionalImages") or [])
             if i.get("imageUrl")]
    location = item.get("itemLocation") or {}

    return RawListing(
        source=Platform.ebay, listing_id=item_id, title=title, price_cad=amount,
        url=item.get("itemWebUrl") or "",
        location=", ".join(p for p in (location.get("city"),
                                       location.get("stateOrProvince"),
                                       location.get("country")) if p),
        description=(item.get("shortDescription") or "")[:2000],
        date_posted=item.get("itemCreationDate") or item.get("itemOriginDate"),
        image_urls=([image] if image else []) + extra,
        fulfillment="shipping", condition=condition.replace(" ", ""),
        payment="ebay_protection", seller_id=str(seller.get("username") or ""),
        seller_type="ebay", shipping_cad=shipping,
        seller_rating=_float(seller.get("feedbackPercentage")),
        seller_reviews=(int(seller["feedbackScore"])
                        if str(seller.get("feedbackScore", "")).isdigit() else None))


def parse_response(payload: dict) -> list[RawListing]:
    out = []
    for item in payload.get("itemSummaries") or []:
        try:
            listing = parse_item(item)
        except Exception as exc:                     # one odd item must not sink the page
            logger.warning("Skipping unparseable eBay item: %s", exc)
            continue
        if listing is not None:
            out.append(listing)
    return out


class EbayClient:
    """Minimal Browse API client: client-credentials token + search."""

    def __init__(self, client_id: str, client_secret: str, session=None,
                 postal_code: Optional[str] = None):
        self._auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._http = session or requests
        self._token: Optional[str] = None
        self._expires = 0.0
        self._postal = postal_code

    def _bearer(self) -> Optional[str]:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = self._http.post(
            TOKEN_URL, timeout=20,
            headers={"Authorization": f"Basic {self._auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": SCOPE})
        if resp.status_code != 200:
            logger.warning("eBay token request failed (HTTP %s)", resp.status_code)
            return None
        body = resp.json()
        self._token = body.get("access_token")
        self._expires = time.time() + float(body.get("expires_in") or 0)
        return self._token

    def search(self, query: str, category: str, limit: int = 100) -> list[RawListing]:
        token = self._bearer()
        if not token:
            return []
        context = "contextualLocation=country=CA"
        if self._postal:
            context += f",zip={self._postal}"
        resp = self._http.get(
            SEARCH_URL, timeout=30,
            headers={"Authorization": f"Bearer {token}",
                     "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA",
                     "X-EBAY-C-ENDUSERCTX": context},
            params={"q": query, "category_ids": category, "limit": limit,
                    "sort": "newlyListed",
                    "filter": "buyingOptions:{FIXED_PRICE},priceCurrency:CAD"})
        if resp.status_code != 200:
            logger.warning("eBay search %r failed (HTTP %s)", query, resp.status_code)
            return []
        return parse_response(resp.json())


@register_source(Platform.ebay)
class EbayAdapter(SourceAdapter):
    @property
    def platform(self):
        return Platform.ebay

    def search(self, query="", max_pages=1, **kwargs):
        keys = credentials()
        if keys is None:
            logger.info("eBay skipped: no EBAY_CLIENT_ID / EBAY_CLIENT_SECRET")
            return []
        client = EbayClient(*keys, postal_code=os.environ.get("EBAY_POSTAL_CODE"))
        seen: dict[str, RawListing] = {}
        for q, category in QUERIES:
            try:
                for listing in client.search(q, category):
                    seen.setdefault(listing.source_listing_id, listing)
            except requests.RequestException as exc:
                logger.warning("eBay search %r failed: %s", q, type(exc).__name__)
            time.sleep(0.3)
        return list(seen.values())

    def fetch_detail(self, url):
        return None

    def check_health(self):
        return {"status": "ok" if credentials() else "disabled", "source": "ebay"}
