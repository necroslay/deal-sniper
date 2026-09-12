"""
Craigslist Canada Scraper.

Craigslist search pages are JavaScript-rendered, so a plain HTTP fetch yields an
empty shell. This scraper drives a headless browser (Playwright, using the
locally-installed Chrome when available) to render search results, then parses
the resulting DOM with BeautifulSoup.

The current Craigslist result markup is::

    <div class="cl-search-result" data-pid="7951348061" title="<category>">
      <a class="... posting-title" href="https://www.craigslist.org/view/d/<slug>/<token>">
        <span class="label">Title</span>
      </a>
      <div class="meta-line">
        <span class="result-posted-date">5h ago</span>
        <span class="result-location">city of toronto</span>
      </div>
      <span class="priceinfo">$650</span>
    </div>

A legacy ``li.cl-static-search-result`` layout is still parsed as a fallback.
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from browser import BrowserRenderer, BrowserRenderError
from config import load_config
from models import CraigslistListing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CFG = load_config()

# Selectors for the current Craigslist layout, with the legacy layout as fallback.
RESULT_SELECTORS = ("div.cl-search-result", "li.cl-static-search-result", "li.result-row")



# Craigslist emits both full and abbreviated units: "5h ago", "2d ago",
# "3w ago", "1 month ago". Map every accepted spelling to a canonical unit.
_DATE_UNITS: dict[str, str] = {}
for _canonical, _spellings in (
    ("minute", ("minute", "minutes", "min", "mins")),
    ("hour", ("hour", "hours", "hr", "hrs", "h")),
    ("day", ("day", "days", "d")),
    ("week", ("week", "weeks", "wk", "wks", "w")),
    ("month", ("month", "months", "mo", "mos")),
    ("year", ("year", "years", "yr", "yrs", "y")),
):
    for _spelling in _spellings:
        _DATE_UNITS[_spelling] = _canonical


class CraigslistScraper:
    """Scrapes Craigslist Canada search results."""

    def __init__(self, delay: Optional[float] = None, use_browser: Optional[bool] = None):
        self.session = requests.Session()
        self.delay = delay if delay is not None else CFG.request.page_delay
        self.session.headers.update(CFG.request.headers)

        self.use_browser = CFG.browser.enabled if use_browser is None else use_browser
        self._renderer: Optional[BrowserRenderer] = None

    # ── browser lifecycle ────────────────────────────────────────────────────

    def _get_renderer(self) -> BrowserRenderer:
        """Lazily create (and reuse) the shared headless-browser renderer."""
        if self._renderer is None:
            self._renderer = BrowserRenderer(
                headless=CFG.browser.headless,
                channel=CFG.browser.channel or None,
                timeout_ms=CFG.browser.timeout_ms,
                settle_ms=CFG.browser.settle_ms,
                wait_for=CFG.browser.wait_for_selector,
                block_images=CFG.browser.block_images,
            )
        return self._renderer

    def close(self) -> None:
        """Release the browser process, if one was started."""
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None

    def __enter__(self) -> "CraigslistScraper":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ── fetching ─────────────────────────────────────────────────────────────

    def _fetch_http(self, url: str) -> Optional[str]:
        """Plain HTTP GET. Returns the JS shell for Craigslist search pages."""
        try:
            resp = self.session.get(url, timeout=CFG.request.timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a fully-rendered page, falling back to plain HTTP on failure."""
        if self.use_browser:
            try:
                html = self._get_renderer().render(url)
                if html:
                    return html
                logger.warning(f"Browser render returned no HTML for {url}")
            except BrowserRenderError as e:
                logger.error(f"Headless browser unavailable: {e}")
            except Exception as e:
                logger.error(f"Browser render failed for {url}: {e}")

            if not CFG.browser.fallback_to_requests:
                return None
            logger.warning("Falling back to plain HTTP (results likely empty).")

        return self._fetch_http(url)

    def fetch_listing_detail(self, url: str) -> Optional[dict]:
        """Fetch a single Craigslist listing page for full details."""
        html = self.fetch_page(url)
        if not html:
            return None

        soup = BeautifulSoup(html, "html.parser")
        details = {}

        # Description
        desc_el = soup.select_one("#postingbody")
        if desc_el:
            # Remove the "QR Code Link to This Post" text
            for el in desc_el.select(".print-information"):
                el.decompose()
            details["description"] = desc_el.get_text(strip=True)
        else:
            details["description"] = ""

        # Images
        img_els = soup.select("img[src*='images.craigslist.org']")
        details["image_urls"] = [img["src"] for img in img_els if img.get("src")]

        # Date posted (from meta or HTML)
        date_el = soup.select_one("time[datetime]")
        if date_el and date_el.get("datetime"):
            details["date_posted"] = date_el["datetime"]
        else:
            # Try meta tag
            meta_el = soup.select_one("meta[property='og:updated_time']")
            if meta_el and meta_el.get("content"):
                details["date_posted"] = meta_el["content"]

        return details

    def parse_search_results(self, html: str) -> list[CraigslistListing]:
        """
        Parse Craigslist search results page HTML into CraigslistListing objects.

        Tries the current ``div.cl-search-result`` layout first, then the legacy
        ``li.cl-static-search-result`` / ``li.result-row`` layouts.
        """
        soup = BeautifulSoup(html, "html.parser")
        items = []
        for selector in RESULT_SELECTORS:
            items = soup.select(selector)
            if items:
                logger.debug(f"Matched {len(items)} rows with '{selector}'")
                break

        listings: list[CraigslistListing] = []
        seen_ids: set[str] = set()
        for item in items:
            try:
                listing = self._parse_result_item(item)
                if not listing:
                    continue
                key = listing.listing_id or listing.url
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                listings.append(listing)
            except Exception as e:
                logger.debug(f"Failed to parse listing item: {e}")
                continue

        return listings

    @staticmethod
    def _parse_relative_date(text: str) -> Optional[str]:
        """Convert Craigslist's relative timestamps ("5h ago", "2d ago") to ISO.

        Craigslist has no absolute date in search results, so we anchor to UTC
        'now'. Returns ``None`` for unparseable input.
        """
        if not text:
            return None
        m = re.search(r"(\d+)\s*([a-z]+)\s*ago", text.strip().lower())
        if not m:
            return None

        unit = _DATE_UNITS.get(m.group(2))
        if unit is None:
            return None
        value = int(m.group(1))

        delta = {
            "minute": timedelta(minutes=value),
            "hour": timedelta(hours=value),
            "day": timedelta(days=value),
            "week": timedelta(weeks=value),
            "month": timedelta(days=value * 30),
            "year": timedelta(days=value * 365),
        }[unit]
        return (datetime.now(timezone.utc) - delta).isoformat()

    @staticmethod
    def _parse_short_date(text: str) -> Optional[str]:
        """Parse Craigslist's short posted-dates into ISO-8601.

        Craigslist is inconsistent about this label — the same page renders as
        ``9/10`` for some sessions and ``09-10`` for others, and older posts can
        carry a month name (``Sep 10`` / ``Sep 10, 2024``). Recent posts use a
        relative label ("5h ago") instead. When no year is present we assume the
        current year, rolling back one year if that would land in the future.
        """
        if not text:
            return None
        text = text.strip()

        month = day = None
        year_raw: Optional[str] = None

        # Numeric: 9/10, 09-10, 9.10, 1/7/2024, 12-31-24
        m = re.fullmatch(r"(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?", text)
        if m:
            month, day, year_raw = int(m.group(1)), int(m.group(2)), m.group(3)
        else:
            # Month name: "Sep 10", "Sept 10 2024", "Sep 10, 2024"
            m = re.fullmatch(
                r"([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:\s*,?\s*(\d{2,4}))?", text
            )
            if m:
                token = m.group(1)[:3].lower()
                months = {
                    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
                }
                if token not in months:
                    return None
                month, day, year_raw = months[token], int(m.group(2)), m.group(3)

        if month is None or day is None:
            return None

        now = datetime.now(timezone.utc)
        if year_raw is None:
            year = now.year
        else:
            year = int(year_raw)
            if year < 100:
                year += 2000

        try:
            dt = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            return None

        if year_raw is None and dt > now + timedelta(days=1):
            try:
                dt = dt.replace(year=year - 1)
            except ValueError:  # Feb 29 in a non-leap year
                return None
        return dt.isoformat()

    @staticmethod
    def _parse_posted_date(text: str) -> Optional[str]:
        """Parse any Craigslist posted-date label (relative or ``M/D``) to ISO."""
        return (
            CraigslistScraper._parse_relative_date(text)
            or CraigslistScraper._parse_short_date(text)
        )

    def _parse_result_item(self, item) -> Optional[CraigslistListing]:
        """Parse a single Craigslist search result item (any layout)."""
        # Title element: current layout uses a.posting-title (whose text lives in
        # a nested <span class="label">); legacy layouts use .result-title/.title.
        # Note the legacy layout wraps the *whole* card in a bare <a>, so falling
        # back to anchor text there would swallow price and location.
        title_anchor = item.select_one("a.posting-title") or item.select_one("a.result-title")
        title_el = (
            title_anchor
            or item.select_one(".result-title")
            or item.select_one(".title")
        )

        # URL: prefer the title anchor, else the first link on the card.
        url = ""
        if title_anchor is not None and title_anchor.get("href"):
            url = title_anchor["href"]
        if not url:
            any_link = item.select_one("a[href]")
            if any_link is not None:
                url = any_link.get("href", "")

        title = title_el.get_text(" ", strip=True) if title_el is not None else ""
        if not title:
            # Last resort: the card's title attribute, or the anchor's own text.
            title = (item.get("title") or "").strip()
        if not title:
            any_link = item.select_one("a[href]")
            if any_link is not None:
                title = any_link.get_text(" ", strip=True)

        if not title or not url:
            return None

        # Price — .priceinfo is current, .price is legacy
        price_el = item.select_one("span.priceinfo") or item.select_one(".price")
        price_cad = 0.0
        if price_el:
            price_text = price_el.get_text(strip=True)
            price_match = re.search(r'\$?([\d,]+(?:\.\d{2})?)', price_text)
            if price_match:
                price_cad = float(price_match.group(1).replace(",", ""))

        # Location
        loc_el = item.select_one(".result-location") or item.select_one(".location")
        location = loc_el.get_text(strip=True) if loc_el else ""

        # Date — prefer an absolute <time datetime>, else parse the label
        # (which is either "5h ago" or a year-less "M/D").
        date_el = item.select_one("time[datetime]")
        if date_el and date_el.get("datetime"):
            date_posted = date_el["datetime"]
        else:
            rel_el = item.select_one(".result-posted-date")
            date_posted = self._parse_posted_date(
                rel_el.get_text(strip=True) if rel_el else ""
            )

        # Listing ID — the current layout carries it on the card's data-pid
        # attribute. Craigslist URLs now use an opaque token
        # (".../view/d/<slug>/<token>") rather than the id, so that token is the
        # only usable fallback. (The old "/<id>.html" pattern is retained as a
        # last resort but Craigslist no longer emits it — measured 0/55 live.)
        listing_id = (item.get("data-pid") or "").strip()
        if not listing_id and url:
            token_match = re.search(r"/view/d/[^/]+/([A-Za-z0-9_-]+)", url)
            if token_match:
                listing_id = token_match.group(1)
            else:
                legacy_match = re.search(r"/(\d+)\.html", url)
                if legacy_match:
                    listing_id = legacy_match.group(1)

        # Image
        image_urls = [
            img["src"]
            for img in item.select("img[src*='images.craigslist.org']")
            if img.get("src")
        ]
        if not image_urls:
            img_el = item.select_one("img")
            if img_el and img_el.get("src"):
                image_urls.append(img_el["src"])

        return CraigslistListing(
            title=title, price_cad=price_cad, url=url,
            location=location, description="",
            date_posted=date_posted, listing_id=listing_id,
            image_urls=image_urls,
        )

    def _scrape_category(
        self,
        search_term: str,
        category: str,
        location: str,
        max_pages: int,
        seen_ids: set[str],
        fetch_details: bool,
    ) -> list[CraigslistListing]:
        """Scrape one (search_term, category) pair across `max_pages` pages."""
        base = CFG.search.url_template.format(
            location=location,
            category=category,
            search_term=search_term.replace(" ", "+"),
        )

        found: list[CraigslistListing] = []
        for page in range(0, max_pages):
            if page == 0:
                url = base
            else:
                sep = "&" if "?" in base else "?"
                url = f"{base}{sep}s={page * CFG.search.listings_per_page}"

            logger.info(f"[{category}] Fetching page {page + 1}: {url}")
            html = self.fetch_page(url)
            if not html:
                break

            listings = self.parse_search_results(html)
            if not listings:
                logger.info(f"[{category}] No listings on page {page + 1}, stopping.")
                break

            new_count = 0
            for listing in listings:
                key = listing.listing_id or listing.url
                if not key or key in seen_ids:
                    continue
                seen_ids.add(key)

                if fetch_details and listing.url:
                    details = self.fetch_listing_detail(listing.url)
                    if details:
                        listing.description = details.get("description", "")
                        if details.get("image_urls"):
                            listing.image_urls = details["image_urls"]
                        if details.get("date_posted"):
                            listing.date_posted = details["date_posted"]

                found.append(listing)
                new_count += 1

            logger.info(f"[{category}] Page {page + 1}: {new_count} new listings")

            if page < max_pages - 1:
                time.sleep(self.delay)

        return found

    def scrape(
        self,
        search_term: Optional[str] = None,
        category: Optional[str] = None,
        location: Optional[str] = None,
        max_pages: Optional[int] = None,
        fetch_details: bool = False,
        categories: Optional[list[str]] = None,
    ) -> list[CraigslistListing]:
        """Scrape Craigslist search results across categories and pages.

        Args:
            search_term: Query string (defaults to config).
            category: Single category code. When omitted, every code in
                ``CFG.search.categories`` is swept.
            location: Craigslist subdomain (e.g. ``toronto``).
            max_pages: Pages per category.
            fetch_details: Also open each listing page (slow; uses the browser).
            categories: Override the category list to sweep.
        """
        search_term = search_term or CFG.search.default_search_term
        location = location or CFG.search.default_location
        max_pages = max_pages or CFG.search.default_pages

        if category:
            cats = [category]
        elif categories:
            cats = list(categories)
        else:
            cats = list(CFG.search.categories)

        seen_ids: set[str] = set()
        all_listings: list[CraigslistListing] = []
        for cat in cats:
            try:
                all_listings.extend(
                    self._scrape_category(
                        search_term=search_term,
                        category=cat,
                        location=location,
                        max_pages=max_pages,
                        seen_ids=seen_ids,
                        fetch_details=fetch_details,
                    )
                )
            except Exception as e:
                logger.error(f"Category '{cat}' failed: {e}")
                continue

        logger.info(
            f"Scraped {len(all_listings)} unique listings across "
            f"{len(cats)} categories for '{search_term}'"
        )
        return all_listings


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Craigslist Canada listings"
    )
    parser.add_argument(
        "--search", type=str, default=None,
        help="Search term (default: from config)"
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Category code (default: 'elec')"
    )
    parser.add_argument(
        "--location", type=str, default=None,
        help="Location (default: 'toronto')"
    )
    parser.add_argument(
        "--pages", type=int, default=None,
        help="Number of pages to scrape (default: from config)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON file path"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--categories", action="store_true",
        help="Show available categories and exit"
    )
    parser.add_argument(
        "--fetch-details", action="store_true",
        help="Open each listing page for the full description (slow, browser-driven)"
    )
    parser.add_argument(
        "--no-browser", action="store_true",
        help="Disable the headless browser (returns empty results on Craigslist)"
    )
    args = parser.parse_args()

    if args.categories:
        print("Available Craigslist Categories:")
        for code, name in CFG.search.category_map.items():
            print(f"  {code:<6s}  {name}")
        return

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper = CraigslistScraper(use_browser=not args.no_browser)
    try:
        listings = scraper.scrape(
            search_term=args.search,
            category=args.category,
            location=args.location,
            max_pages=args.pages,
            fetch_details=args.fetch_details,
        )
    finally:
        scraper.close()

    result = {
        "scraped_at": datetime.now().isoformat(),
        "search_term": args.search or CFG.search.default_search_term,
        "total_listings": len(listings),
        "listings": [l.to_dict() for l in listings],
    }

    print(f"\n{'='*60}")
    print(f"Scraped {len(listings)} listings from Craigslist {args.location or 'toronto'}")
    print(f"{'='*60}\n")

    for l in listings[:15]:
        print(f"  ${l.price_cad:,.0f}  |  {l.title[:70]}")
        print(f"     {l.location or 'N/A'}  |  {l.url}")
        print()

    if len(listings) > 15:
        print(f"  ... and {len(listings) - 15} more listings\n")

    output_json = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"Full results saved to: {args.output}")
    else:
        print("Full JSON output:\n")
        print(output_json)


if __name__ == "__main__":
    main()