"""eBay Canada Scraper."""
import argparse, json, logging, re, time, requests
from datetime import datetime
from bs4 import BeautifulSoup
from config import load_config
from models import eBayListing
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
CFG = load_config()

class eBayScraper:
    def __init__(self, delay=None):
        self.session = requests.Session()
        self.delay = delay if delay else CFG.request.page_delay
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "Accept-Language": "en-CA,en-US;q=0.7,en;q=0.3", "Referer": "https://www.google.com/"})
        self._seeded = False

    def _seed_session(self):
        if not self._seeded:
            try: self.session.get("https://www.ebay.ca/", timeout=30); time.sleep(1.5)
            except: pass
            self._seeded = True

    def fetch_page(self, url):
        self._seed_session()
        for a in range(CFG.request.max_retries):
            try:
                r = self.session.get(url, timeout=CFG.request.timeout)
                if r.status_code == 403 and "Pardon" in r.text:
                    time.sleep(self.delay * (a + 2))
                    self._seeded = False; self._seed_session(); continue
                r.raise_for_status(); return r.text
            except Exception as e:
                logger.warning(f"Attempt {a+1} failed: {e}")
                if a < CFG.request.max_retries - 1: time.sleep(self.delay * (a+1))
        return None

    def parse_search_results(self, html, sold=False):
        soup = BeautifulSoup(html, "html.parser")
        items = soup.select("li.s-card") or soup.select("li.s-item") or soup.select("ul.srp-results li")
        result = []
        for item in items:
            try:
                te = item.select_one("div.s-item__title span") or item.select_one("h3")
                if not te: continue
                title = te.get_text(strip=True)
                if not title or "Shop on" in title: continue
                le = item.select_one("a[href*='itm']") or item.select_one("a.s-item__link")
                url = le["href"].split("?")[0] if le and le.get("href") else ""
                pe = item.select_one("span.s-item__price")
                pc = 0.0
                if pe:
                    m = re.search(r'\$?([\d,]+(?:\.\d{2})?)', pe.get_text(strip=True))
                    if m: pc = float(m.group(1).replace(",", ""))
                ce = item.select_one("span.s-item__condition")
                cond = ce.get_text(strip=True) if ce else ""
                le2 = item.select_one("span.s-item__location")
                loc = le2.get_text(strip=True) if le2 else ""
                lid = item.get("data-listingid", "")
                if not lid:
                    m = re.search(r'/itm/(\d+)', url)
                    if m: lid = m.group(1)
                result.append(eBayListing(title=title, price_cad=pc, url=url, condition=cond, location=loc, listing_id=lid, sold=sold, sold_price_cad=pc if sold else None))
            except: pass
        return result

    def scrape(self, search_term=None, sold=False, max_pages=None):
        st = search_term or CFG.search.default_search_term
        mp = max_pages or CFG.search.default_pages
        tmpl = CFG.search.sold_url_template if sold else CFG.search.url_template
        base = tmpl.format(search_term=st.replace(" ", "+"))
        logger.info(f"Scraping {'sold' if sold else 'active'} for '{st}'")
        all_l, seen = [], set()
        for p in range(1, mp+1):
            html = self.fetch_page(f"{base}&_pgn={p}")
            if not html: break
            listings = self.parse_search_results(html, sold=sold)
            if not listings: break
            for l in listings:
                if l.url and l.url not in seen: all_l.append(l); seen.add(l.url)
            logger.info(f"Page {p}: {len(all_l)} total")
            time.sleep(self.delay)
        return all_l
