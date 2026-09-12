"""
Facebook Marketplace Scraper — extracts listing data from Marketplace search results
using a logged-in session cookie (no account automation).
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from config import load_config
from models import MarketplaceListing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

CFG = load_config()


class FacebookMarketplaceScraper:
    """Scrapes Facebook Marketplace search results."""

    def __init__(self, delay: Optional[float] = None):
        self.session = requests.Session()
        self.delay = delay if delay is not None else CFG.request.page_delay

        self.session.headers.update({
            "User-Agent": CFG.auth.user_agent,
            **CFG.request.headers,
        })

        self._load_cookies()

    def _load_cookies(self) -> None:
        """Load Facebook session cookies from the cookie file."""
        cookie_path = Path(CFG.auth.cookie_file)
        if not cookie_path.exists():
            logger.warning(
                f"Cookie file not found at {CFG.auth.cookie_file}. "
                f"FB Marketplace requires an authenticated session."
            )
            return

        try:
            import json as _json
            with open(cookie_path, "r", encoding="utf-8") as f:
                cookies_data = _json.load(f)
            if isinstance(cookies_data, list):
                for c in cookies_data:
                    if "name" in c and "value" in c:
                        self.session.cookies.set(c["name"], c["value"])
            elif isinstance(cookies_data, dict):
                for name, value in cookies_data.items():
                    self.session.cookies.set(name, value)
            logger.info(f"Loaded cookies from {cookie_path}")
        except Exception as e:
            logger.warning(f"Failed to load cookies: {e}")

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page, returning HTML text or None on failure."""
        for attempt in range(CFG.request.max_retries):
            try:
                resp = self.session.get(
                    url,
                    timeout=CFG.request.timeout,
                )
                resp.raise_for_status()
                return resp.text
            except Exception as e:
                logger.warning(f"Attempt {attempt + 1}/{CFG.request.max_retries} failed for {url}: {e}")
                if attempt < CFG.request.max_retries - 1:
                    time.sleep(self.delay * (attempt + 1))
        return None

    def parse_listing_card(self, card) -> Optional[MarketplaceListing]:
        """
        Extract listing data from a single Marketplace search result card.
        Uses BeautifulSoup to parse the HTML card elements.
        """
        try:
            # Title
            title_el = card.select_one("span[dir='auto']")
            if not title_el:
                title_el = card.select_one("a > span")
            title = title_el.get_text(strip=True) if title_el else ""

            if not title:
                return None

            # Price
            price_el = card.select_one("span:has(+ span[dir='auto'])")
            if not price_el:
                price_texts = card.find_all(string=re.compile(r'\$\d[\d,]*\.?\d*'))
                price_text = price_texts[0] if price_texts else ""
            else:
                price_text = price_el.get_text(strip=True)

            price_cad = 0.0
            price_match = re.search(r'\$?([\d,]+(?:\.\d{2})?)', price_text)
            if price_match:
                price_cad = float(price_match.group(1).replace(",", ""))

            # URL
            link_el = card.find("a", href=True) if card.name != "a" else card
            if not link_el or not link_el.get("href"):
                link_el = card.select_one("a[href*='/marketplace/item/']")
            url = ""
            if link_el:
                href = link_el.get("href", "")
                if href.startswith("/"):
                    url = CFG.search.base_url + href
                else:
                    url = href

            # Location
            location_el = card.select_one("span:contains('·')")
            location = ""
            if location_el:
                loc_text = location_el.get_text(strip=True)
                loc_match = re.search(r'·\s*(.+?)(?:·|$)', loc_text)
                if loc_match:
                    location = loc_match.group(1).strip()

            # Listing ID from URL
            listing_id = ""
            id_match = re.search(r'/item/(\d+)', url) if url else None
            if id_match:
                listing_id = id_match.group(1)

            return MarketplaceListing(
                title=title,
                price_cad=price_cad,
                url=url,
                location=location,
                description="",
                date_posted="",
                listing_id=listing_id,
                image_urls=[],
            )
        except Exception as e:
            logger.debug(f"Failed to parse listing card: {e}")
            return None

    def scrape_search_page(self, search_term: str, location: str) -> list[MarketplaceListing]:
        """Scrape a single page of Facebook Marketplace search results."""
        url = CFG.search.url_template.format(
            location=location or CFG.search.default_location,
            search_term=search_term.replace(" ", "+"),
        )

        logger.info(f"Fetching: {url}")
        html = self.fetch_page(url)
        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        listings: list[MarketplaceListing] = []

        for selector in [
            "div[role='article']",
            "div.x1yztbdb",
            "div[data-pagelet]",
            "a[href*='/marketplace/item/']",
        ]:
            cards = soup.select(selector)
            if cards:
                for card in cards:
                    listing = self.parse_listing_card(card)
                    if listing:
                        listings.append(listing)
                if listings:
                    logger.info(f"Found {len(listings)} listings using selector '{selector}'")
                    break

        return listings

    def scrape(
        self,
        search_term: Optional[str] = None,
        location: Optional[str] = None,
        max_pages: Optional[int] = None,
    ) -> list[MarketplaceListing]:
        """Scrape multiple pages of Marketplace search results."""
        search_term = search_term or CFG.search.default_search_term
        location = location or CFG.search.default_location
        max_pages = max_pages or CFG.search.default_pages

        all_listings: list[MarketplaceListing] = []
        for page in range(1, max_pages + 1):
            listings = self.scrape_search_page(search_term, location)

            seen_urls = {l.url for l in all_listings if l.url}
            new_listings = [l for l in listings if l.url and l.url not in seen_urls]
            all_listings.extend(new_listings)

            logger.info(f"Page {page}: found {len(new_listings)} new listings")
            time.sleep(self.delay)

        return all_listings


def main():
    parser = argparse.ArgumentParser(
        description="Scrape Facebook Marketplace listings (Canada)"
    )
    parser.add_argument(
        "--search", type=str, default=None,
        help="Search term (default: from config)"
    )
    parser.add_argument(
        "--location", type=str, default=None,
        help="Location (default: from config, e.g. 'toronto')"
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
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper = FacebookMarketplaceScraper()
    listings = scraper.scrape(
        search_term=args.search,
        location=args.location,
        max_pages=args.pages,
    )

    result = {
        "scraped_at": datetime.now().isoformat(),
        "search_term": args.search or CFG.search.default_search_term,
        "total_listings": len(listings),
        "listings": [l.to_dict() for l in listings],
    }

    print(f"\n{'='*60}")
    print(f"Scraped {len(listings)} listings from Facebook Marketplace")
    print(f"{'='*60}\n")

    for l in listings[:15]:
        print(f"  💰 ${l.price_cad:,.0f}  |  {l.title[:70]}")
        print(f"     📍 {l.location or 'N/A'}  |  {l.url}")
        print()

    if len(listings) > 15:
        print(f"  ... and {len(listings) - 15} more listings\n")

    output_json = json.dumps(result, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_json)
        print(f"📁 Full results saved to: {args.output}")
    else:
        print("📄 Full JSON output:\n")
        print(output_json)


if __name__ == "__main__":
    main()