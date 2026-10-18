"""r/CanadianHardwareSwap, read from the subreddit's public Atom feed.

Why this source: it is a *shipping-friendly, buyer-protected* used market. Posts
are mostly sold by mail with PayPal Goods & Services, which is the one payment
route that lets you claw money back — exactly what Kijiji and Craigslist lack.

How it is read, and why it is gentle: one request per page of results, with a
descriptive User-Agent, to the same Atom feed any feed reader uses. Reddit hands
unauthenticated clients a tiny request budget, so a 429 is answered by waiting
out the reset window it reports (never by retrying faster or by disguising the
client). If Reddit still refuses, the source returns nothing for this run and the
other sources carry on.

Post titles follow the subreddit's ``[CA-ON] [H] <have> [W] <want>`` convention:

* a sale has the item under ``[H]`` and payment under ``[W]``;
* a wanted ad has payment under ``[H]``; a trade has hardware on both sides.

Only sales of GPUs and laptops with a readable price are kept.
"""

from __future__ import annotations

import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional

import requests

from app.adapters.sources import SourceAdapter, register_source
from app.engines.laptops import LAPTOP_WORDS
from app.models.schema import Platform, RawListing

logger = logging.getLogger(__name__)

FEED_URL = "https://www.reddit.com/r/CanadianHardwareSwap/new/.rss"
USER_AGENT = "deal-sniper/0.1 (personal use; hardware deal alerts)"
_ATOM = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}

_PAYMENT = re.compile(
    r"\b(paypal|pp|e-?transfer|interac|cash|local|venmo|zelle|crypto|btc|"
    r"trade|g\s*&\s*s|goods)\b", re.IGNORECASE)
_HARDWARE = re.compile(
    r"\b(rtx|gtx|geforce|radeon|rx\s?\d{3,4}|arc\s?[ab]\d{3}|quadro|"
    r"graphics|gpu|video ?card)\b", re.IGNORECASE)
_SOLD = re.compile(r"\b(sold|closed|pending|expired)\b", re.IGNORECASE)
_PAYPAL_GS = re.compile(
    r"paypal[^.\n]{0,25}(g\s*&\s*s|g/s|goods)|(g\s*&\s*s|goods\s*(and|&)\s*services)"
    r"[^.\n]{0,25}paypal", re.IGNORECASE)
_SHIPS = re.compile(r"\bshipp?(ed|ing)\b", re.IGNORECASE)
_SHIPPED_PRICE = re.compile(
    r"\$\s?[\d,]+(?:\.\d{2})?\s*(?:cad\s*)?(?:shipped|delivered|(?:incl\w*|with)\s+shipping)",
    re.IGNORECASE)
_MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{2,5})(?:\.\d{2})?")


def _sections(title: str) -> tuple[str, str, str]:
    """``(region, have, want)`` from a ``[CA-ON] [H] ... [W] ...`` title."""
    have = re.search(r"\[\s*H\s*\]\s*(.*?)\s*(?=\[\s*W\s*\]|$)", title, re.I | re.S)
    want = re.search(r"\[\s*W\s*\]\s*(.*)$", title, re.I | re.S)
    region = ""
    for tag in re.findall(r"\[([^\]]{2,8})\]", title):
        if tag.strip().upper() not in {"H", "W"}:
            region = tag.strip().upper()
            break
    return region, (have.group(1) if have else ""), (want.group(1) if want else "")


def _price(have: str, body: str) -> Optional[float]:
    """The asking price: first dollar amount that isn't a shipping charge."""
    for text in (have, body):
        for m in _MONEY.finditer(text):
            before = text[max(0, m.start() - 14): m.start()].lower()
            after = text[m.end(): m.end() + 14].lower()
            # "shipping $40" / "$40 shipping" is a charge; "$650 shipped" is the
            # price *including* delivery, which is exactly what we want.
            if re.search(r"ship|postage|tax|fee", before) or re.match(
                    r"\s*(\+\s*)?(extra|shipping|ship\b|postage|tax|fee|for ship)", after):
                continue
            value = float(m.group(1).replace(",", ""))
            if 20 <= value <= 20000:
                return value
    return None


def parse_feed(xml_text: str) -> list[RawListing]:
    """Turn an Atom feed body into ``RawListing``s (sales of GPUs/laptops only)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Reddit feed was not valid XML: %s", exc)
        return []

    out: list[RawListing] = []
    for entry in root.findall("a:entry", _ATOM):
        title = html.unescape(entry.findtext("a:title", "", _ATOM)).strip()
        if _SOLD.search(title):
            continue
        region, have, want = _sections(title)
        if not have:
            continue
        # Wanted ads put payment under [H]; trades put hardware under [W].
        if _PAYMENT.search(have) and not (_HARDWARE.search(have)
                                          or LAPTOP_WORDS.search(have)):
            continue
        if (_HARDWARE.search(want) or LAPTOP_WORDS.search(want)) and not _PAYMENT.search(want):
            continue
        if not (_HARDWARE.search(have) or LAPTOP_WORDS.search(have)):
            continue

        raw = html.unescape(entry.findtext("a:content", "", _ATOM))
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()
        price = _price(have, body)
        if price is None:
            continue

        link_el = entry.find("a:link", _ATOM)
        link = link_el.get("href") if link_el is not None else ""
        post_id = (entry.findtext("a:id", "", _ATOM) or link).rsplit("/", 1)[-1]
        if not post_id:
            continue
        thumb = entry.find("m:thumbnail", _ATOM)
        author = (entry.findtext("a:author/a:name", "", _ATOM) or "").replace("/u/", "")
        text = f"{title}\n{body}"

        payment = []
        if _PAYPAL_GS.search(text):
            payment.append("paypal_gs")
        if re.search(r"e-?transfer|interac", text, re.I):
            payment.append("etransfer")

        out.append(RawListing(
            source=Platform.reddit, listing_id=post_id, title=have[:200],
            price_cad=price, url=link, location=region, description=body[:2000],
            date_posted=entry.findtext("a:published", None, _ATOM)
            or entry.findtext("a:updated", None, _ATOM),
            image_urls=[thumb.get("url")] if thumb is not None and thumb.get("url") else [],
            fulfillment="shipping" if _SHIPS.search(text) else "",
            # "$650 shipped" already includes delivery: don't add an estimate.
            shipping_cad=0.0 if _SHIPPED_PRICE.search(text) else None,
            payment=",".join(payment), seller_id=author, seller_type="reddit"))
    return out


def fetch_feed(url: str, max_attempts: int = 3,
               sleep: Callable[[float], None] = time.sleep,
               get=requests.get) -> Optional[str]:
    """GET *url*, waiting out Reddit's reported rate-limit window on a 429."""
    for attempt in range(1, max_attempts + 1):
        try:
            resp = get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
        except requests.RequestException as exc:
            logger.warning("Reddit request failed: %s", type(exc).__name__)
            return None
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 429 and attempt < max_attempts:
            wait = float(resp.headers.get("x-ratelimit-reset")
                         or resp.headers.get("retry-after") or 30)
            sleep(min(wait + 2, 90))
            continue
        logger.warning("Reddit returned HTTP %s", resp.status_code)
        return None
    return None


@register_source(Platform.reddit)
class RedditAdapter(SourceAdapter):
    @property
    def platform(self):
        return Platform.reddit

    def search(self, query="", max_pages=2, **kwargs):
        """Read *max_pages* pages of the newest posts (100 each)."""
        listings: list[RawListing] = []
        url = f"{FEED_URL}?limit=100"
        for page in range(max_pages):
            body = fetch_feed(url)
            if body is None:
                break
            listings.extend(parse_feed(body))
            ids = re.findall(r"<id>t3_(\w+)</id>", body)
            if not ids or page + 1 >= max_pages:
                break
            url = f"{FEED_URL}?limit=100&after=t3_{ids[-1]}"
        return listings

    def fetch_detail(self, url):
        return None

    def check_health(self):
        return {"status": "ok", "source": "reddit"}
