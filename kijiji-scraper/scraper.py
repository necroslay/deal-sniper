"""
Kijiji GPU Scraper — free method using cloudscraper + __NEXT_DATA__ extraction.

Kijiji is a Next.js app. All listing data is embedded server-side in a
<script id="__NEXT_DATA__"> tag as Apollo GraphQL state. We extract it
from there — no HTML parsing needed, more reliable than CSS selectors.

Usage:
    python scraper.py [--pages N] [--output FILE] [--search SEARCH_TERM]

Configuration:
    Edit ``config.yaml`` in this directory to change search terms, GPU
    model filters, request delays, HTTP headers, etc. without touching code.
    See ``config.py`` for the dataclass schema and loader.
"""

import argparse
import json
import logging
import re
import time
from datetime import datetime
from typing import Optional

import cloudscraper

from config import load_config
from models import KijijiListing

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# Load once at module level; also available in main() via config.xxx
CFG = load_config()


class KijijiScraper:
    """Scrapes Kijiji search results by extracting __NEXT_DATA__."""

    def __init__(self, delay: Optional[float] = None):
        self.scraper = cloudscraper.create_scraper(
            browser={
                "browser": CFG.request.browser,
                "platform": CFG.request.platform,
                "mobile": CFG.request.mobile,
            }
        )
        self.delay = delay if delay is not None else CFG.request.page_delay

    def fetch_page(self, url: str) -> Optional[str]:
        """Fetch a page, returning HTML text or None on failure."""
        try:
            resp = self.scraper.get(
                url,
                timeout=CFG.request.timeout,
                headers=CFG.request.headers,
            )
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            logger.error(f"Failed to fetch {url}: {e}")
            return None

    def extract_apollo_state(self, html: str) -> Optional[dict]:
        """Extract the Apollo state from __NEXT_DATA__ script tag."""
        match = re.search(
            r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
            html,
            re.DOTALL,
        )
        if not match:
            logger.warning("No __NEXT_DATA__ found in page")
            return None

        try:
            data = json.loads(match.group(1))
            return data["props"]["pageProps"]["__APOLLO_STATE__"]
        except (KeyError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to parse Apollo state: {e}")
            return None

    def extract_listings_from_apollo(self, apollo: dict) -> list[KijijiListing]:
        """
        Extract StandardListing entries that are part of the actual search results.

        Only listings referenced in the ROOT_QUERY's searchResultsPageByUrl
        mainListings array are returned — this excludes promoted/related listings
        that appear elsewhere on the page.
        """
        root = apollo.get("ROOT_QUERY", {})

        # Find the search results page key (e.g., searchResultsPageByUrl:/b-...)
        srp_key = None
        for key in root:
            if "searchResultsPageByUrl" in key:
                srp_key = key
                break

        if not srp_key:
            logger.warning("No searchResultsPageByUrl found in ROOT_QUERY")
            return []

        srp = root[srp_key]
        if not isinstance(srp, dict):
            logger.warning("searchResultsPageByUrl is not a dict")
            return []

        results = srp.get("results", {})
        if not isinstance(results, dict):
            logger.warning("searchResultsPage.results is not a dict")
            return []

        # Find the mainListings array — key varies by pagination args
        main_listings_key = None
        for key in results:
            if "mainListings" in key:
                main_listings_key = key
                break

        if not main_listings_key:
            logger.warning("No mainListings found in search results")
            return []

        listing_refs = results[main_listings_key]
        if not isinstance(listing_refs, list):
            logger.warning("mainListings is not a list")
            return []

        listings = []
        for ref in listing_refs:
            # Each ref is like {"__ref": "StandardListing:1742224874"}
            if not isinstance(ref, dict):
                continue
            ref_key = ref.get("__ref", "")
            if not ref_key:
                continue

            listing_data = apollo.get(ref_key)
            if not isinstance(listing_data, dict):
                continue

            try:
                listing = self._parse_standard_listing(listing_data)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.warning(f"Failed to parse listing {ref_key}: {e}")

        return listings

    def _parse_standard_listing(self, data: dict) -> Optional[KijijiListing]:
        """Convert a StandardListing Apollo object to our KijijiListing model."""
        title = data.get("title", "")
        if not title:
            return None

        # Price is in cents (e.g., 150000 = $1500)
        price_obj = data.get("price", {})
        if isinstance(price_obj, dict):
            price_cents = price_obj.get("amount", 0) or 0
            price_cad = price_cents / 100.0
        else:
            price_cad = 0.0

        url = data.get("url", "")
        if url and not url.startswith("http"):
            url = CFG.search.base_url + url

        # Location
        location = ""
        loc_obj = data.get("location", {})
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name", "")

        # Description
        description = data.get("description", "")

        # Date posted
        date_posted = data.get("activationDate", "")

        # Listing ID
        listing_id = str(data.get("id", ""))

        # Image URLs
        image_urls = data.get("imageUrls", [])
        if not isinstance(image_urls, list):
            image_urls = []

        return KijijiListing(
            title=title,
            price_cad=price_cad,
            url=url,
            location=location,
            description=description,
            date_posted=date_posted,
            listing_id=listing_id,
            image_urls=image_urls,
        )

    def is_high_end_gpu(self, listing: KijijiListing) -> bool:
        """Check if a listing title matches high-end GPU keywords."""
        title_lower = listing.title.lower()
        desc_lower = listing.description.lower()
        combined = title_lower + " " + desc_lower
        return any(gpu in combined for gpu in CFG.high_end_gpus)

    def scrape_search_page(self, search_term: str, page: int = 1) -> list[KijijiListing]:
        """Scrape a single search results page for a given search term."""
        url = CFG.search.search_url(search_term, page)
        logger.info(f"Fetching page {page}: {url}")

        html = self.fetch_page(url)
        if not html:
            return []

        apollo = self.extract_apollo_state(html)
        if not apollo:
            return []

        listings = self.extract_listings_from_apollo(apollo)
        logger.info(f"Found {len(listings)} listings on page {page}")

        return listings

    def scrape(
        self,
        search_term: str = "graphics-card",
        max_pages: int = 3,
        high_end_only: bool = True,
    ) -> list[KijijiListing]:
        """Scrape multiple pages of search results using the anvil GraphQL API.

        Uses the anvil/api endpoint for all pages (supports higher listing counts
        than the __NEXT_DATA__ approach, which is capped at 40).

        Args:
            search_term: Kijiji search term (e.g., "graphics-card", "rtx-4090")
            max_pages: Number of pages to scrape
            high_end_only: If True, only return listings matching high-end GPU models
        """
        all_listings = []
        seen_urls = set()

        for page in range(1, max_pages + 1):
            # Use the anvil GraphQL API for all pages (supports up to 50 per page)
            listings = self._fetch_page_graphql(search_term, page)

            if not listings:
                # Fallback to __NEXT_DATA__ for page 1 if GraphQL fails
                if page == 1:
                    logger.info("GraphQL API failed, falling back to __NEXT_DATA__")
                    listings = self.scrape_search_page(search_term, page)
                if not listings:
                    logger.info(f"No listings found on page {page}, stopping.")
                    break

            # Dedup by URL
            for listing in listings:
                if listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    all_listings.append(listing)

            logger.info(
                f"Page {page}: {len(listings)} listings "
                f"({len(all_listings)} unique total)"
            )

            if page < max_pages:
                time.sleep(self.delay)

        # Filter to high-end GPUs if requested
        if high_end_only:
            high_end = [l for l in all_listings if self.is_high_end_gpu(l)]
            logger.info(
                f"Filtered to {len(high_end)} high-end GPU listings "
                f"(out of {len(all_listings)} total)"
            )
            return high_end

        return all_listings

    def scrape_terms(
        self,
        search_terms: Optional[list[str]] = None,
        max_pages: Optional[int] = None,
        high_end_only: bool = False,
    ) -> list[KijijiListing]:
        """Scrape several search terms and return their de-duplicated union.

        Kijiji search is keyword-based: a broad term ("graphics-card") catches
        plenty but also drags in unrelated matches, while narrow model terms
        ("rtx-4090") surface listings the broad term ranks off the first pages.
        Sweeping a list and unioning the results gives a much larger corpus.
        """
        terms = list(search_terms) if search_terms else list(CFG.search.search_terms)
        pages = max_pages if max_pages is not None else CFG.search.default_pages

        all_listings: list[KijijiListing] = []
        seen_urls: set[str] = set()
        for term in terms:
            try:
                found = self.scrape(term, max_pages=pages, high_end_only=high_end_only)
            except Exception as e:
                logger.error(f"Search term '{term}' failed: {e}")
                continue

            new = 0
            for listing in found:
                if listing.url and listing.url not in seen_urls:
                    seen_urls.add(listing.url)
                    all_listings.append(listing)
                    new += 1
            logger.info(
                f"Term '{term}': {new} new listings "
                f"({len(all_listings)} unique total)"
            )

        return all_listings

    def _get_kijiji_headers(self) -> dict:
        """Get headers required for Kijiji GraphQL API calls."""
        return {
            "accept": "*/*",
            "accept-language": "en",
            "apollo-require-preflight": "true",
            "content-type": "application/json",
            "origin": "https://www.kijiji.ca",
            "referer": "https://www.kijiji.ca/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        }

    def _fetch_page_graphql(self, search_term: str, page: int) -> list[KijijiListing]:
        """Fetch a page of search results using Kijiji's anvil/api GraphQL endpoint.

        Uses URL-based pagination: page 1 uses the base URL, page 2+ uses
        /page-N/ in the URL path. The PaginationInputV2 offset parameter
        is ignored by the API — pagination is controlled entirely by the URL.
        """
        # Region and location come from config (SearchConfig.search_url), so the
        # geographic scope is a config edit. This used to be hardcoded to the
        # GTA, which silently ignored the configured scope entirely.
        full_url = CFG.search.search_url(search_term, page)

        query = {
            "operationName": "SearchResultsPage",
            "variables": {
                "by": {"url": full_url},
                "pagination": {"offset": 0, "limit": CFG.search.listings_per_page},
            },
            "query": (
                "query SearchResultsPage($by: SearchResultsPageInputBy!, $pagination: PaginationInputV2!) {"
                "  searchResultsPage(by: $by, pagination: $pagination) {"
                "    pagination { totalCount limit offset }"
                "    results {"
                "      mainListings {"
                "        id"
                "        title"
                "        price {"
                "          ... on StandardAmountPrice { amount }"
                "          ... on AmountPrice { amount }"
                "          ... on AutosDealerAmountPrice { amount }"
                "        }"
                "        url"
                "        location { name }"
                "        description"
                "        activationDate"
                "        imageUrls"
                "        attributes { all { canonicalName canonicalValues } }"
                "        flags { priceDrop }"
                "        posterInfo { posterId sellerType }"
                "      }"
                "    }"
                "  }"
                "}"
            ),
        }

        try:
            resp = self.scraper.post(
                f"{CFG.search.base_url}/anvil/api",
                json=query,
                timeout=30,
                headers=self._get_kijiji_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return self._parse_graphql_response(data)
        except Exception as e:
            logger.warning(f"GraphQL API call failed for page {page}: {e}")
            return []

    def _parse_graphql_response(self, data: dict) -> list[KijijiListing]:
        """Parse a GraphQL searchResultsPage response into KijijiListing objects."""
        if "errors" in data:
            logger.warning(f"GraphQL errors: {data['errors']}")
            return []

        page_data = data.get("data", {}).get("searchResultsPage", {})
        if not page_data:
            return []

        results = page_data.get("results", {})
        main_listings = results.get("mainListings", [])
        if not isinstance(main_listings, list):
            return []

        listings = []
        for item in main_listings:
            try:
                listing = self._parse_graphql_listing(item)
                if listing:
                    listings.append(listing)
            except Exception as e:
                logger.warning(f"Failed to parse GraphQL listing: {e}")

        return listings

    def _parse_graphql_listing(self, data: dict) -> Optional[KijijiListing]:
        """Convert a GraphQL search result item to our KijijiListing model."""
        title = data.get("title", "")
        if not title:
            return None

        # Price is in cents
        price_obj = data.get("price", {})
        if isinstance(price_obj, dict):
            price_cents = price_obj.get("amount", 0) or 0
            price_cad = price_cents / 100.0
        else:
            price_cad = 0.0

        url = data.get("url", "")
        if url and not url.startswith("http"):
            url = CFG.search.base_url + url

        # Location
        location = ""
        loc_obj = data.get("location", {})
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name", "")

        description = data.get("description", "")
        date_posted = data.get("activationDate", "")
        listing_id = str(data.get("id", ""))

        image_urls = data.get("imageUrls", [])
        if not isinstance(image_urls, list):
            image_urls = []

        attrs = self._attribute_map(data)
        flags = data.get("flags") if isinstance(data.get("flags"), dict) else {}
        poster = data.get("posterInfo") if isinstance(data.get("posterInfo"), dict) else {}

        return KijijiListing(
            title=title,
            price_cad=price_cad,
            url=url,
            location=location,
            description=description,
            date_posted=date_posted,
            listing_id=listing_id,
            image_urls=image_urls,
            fulfillment=",".join(attrs.get("fulfillment", [])),
            condition=",".join(attrs.get("condition", [])),
            payment=",".join(attrs.get("payment", [])),
            seller_id=str(poster.get("posterId") or ""),
            seller_type=str(poster.get("sellerType") or ""),
            price_drop=bool(flags.get("priceDrop")),
        )

    @staticmethod
    def _attribute_map(data: dict) -> dict[str, list[str]]:
        """``{canonicalName: [values]}`` from a listing's ``attributes.all``."""
        block = data.get("attributes")
        items = block.get("all") if isinstance(block, dict) else None
        out: dict[str, list[str]] = {}
        for item in items or []:
            if isinstance(item, dict) and item.get("canonicalName"):
                values = item.get("canonicalValues") or []
                out[item["canonicalName"]] = [str(v) for v in values]
        return out

    def scrape_search_page_graphql(self, search_term: str, page: int) -> list[KijijiListing]:
        """Alias for _fetch_page_graphql called by scrape()."""
        return self._fetch_page_graphql(search_term, page)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape GPU listings from Kijiji GTA"
    )
    parser.add_argument(
        "--pages", type=int, default=3,
        help="Number of pages to scrape (default: 3)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON file path"
    )
    parser.add_argument(
        "--search", type=str, default="graphics-card",
        help="Search term (default: 'graphics-card')"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Include all listings, not just high-end GPUs"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    scraper = KijijiScraper()
    listings = scraper.scrape(
        search_term=args.search,
        max_pages=args.pages,
        high_end_only=not args.all,
    )

    # Convert to dicts
    result = {
        "scraped_at": datetime.now().isoformat(),
        "search_term": args.search,
        "total_listings": len(listings),
        "listings": [l.to_dict() for l in listings],
    }

    # Print summary
    print(f"\n{'='*60}")
    print(f"Scraped {len(listings)} GPU listings from Kijiji GTA")
    print(f"{'='*60}\n")

    for l in listings[:15]:
        print(f"  💰 ${l.price_cad:,.0f}  |  {l.title[:70]}")
        print(f"     📍 {l.location}  |  {l.url}")
        print()

    if len(listings) > 15:
        print(f"  ... and {len(listings) - 15} more listings\n")

    # Output JSON
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