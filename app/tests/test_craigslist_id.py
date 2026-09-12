"""Regression tests for Craigslist listing-id parsing.

Craigslist's id lives on the card's ``data-pid`` attribute. The old fallback
looked for ``/<id>.html`` in the URL — a format Craigslist no longer emits (its
URLs are now ``/view/d/<slug>/<token>``), so that branch could never fire. A
missing id then flowed through as an empty string, and
``UNIQUE(source, source_listing_id)`` would file every such listing under the
blank key ``(source, '')``, each silently overwriting the last.

The scraper is a separate project, so it is loaded through the same
``_import_scraper`` helper the app uses and exercised via the public
``parse_search_results`` entry point.
"""

from __future__ import annotations

import pytest

from app.adapters.sources import _import_scraper

# "/view/d/<slug>/<token>" is the URL shape Craigslist emits today.
TOKEN = "gpc7UV63Ug3eDHUpwSQNFj"
MODERN_URL = f"https://www.craigslist.org/view/d/north-york-hp-elitebook-830/{TOKEN}"
OTHER_URL = ("https://www.craigslist.org/view/d/west-toronto-xfx-radeon/"
             "aLG9BhxMPd3FKRb5MrNLF2")
LEGACY_URL = "https://toronto.craigslist.org/cpd/7951348061.html"


def _card(url: str, pid: str | None = None,
          title: str = "HP EliteBook 830 G6") -> str:
    attr = f' data-pid="{pid}"' if pid is not None else ""
    return f'''
    <div class="cl-search-result"{attr} title="computer parts">
      <a class="posting-title" href="{url}"><span class="label">{title}</span></a>
      <span class="priceinfo">$440</span>
      <div class="meta-line">
        <span class="result-location">city of toronto</span>
      </div>
    </div>
    '''


def _html(*cards: str) -> str:
    return ("<html><body><div class='search-results'>"
            + "".join(cards) + "</div></body></html>")


@pytest.fixture(scope="module")
def scraper():
    return _import_scraper("craigslist").CraigslistScraper()


class TestListingIdParsing:
    def test_data_pid_is_preferred(self, scraper):
        got = scraper.parse_search_results(
            _html(_card(MODERN_URL, pid="7927849512")))
        assert [l.listing_id for l in got] == ["7927849512"]

    def test_url_token_is_used_when_data_pid_is_absent(self, scraper):
        """The regression: this used to yield an empty id."""
        got = scraper.parse_search_results(_html(_card(MODERN_URL)))
        assert [l.listing_id for l in got] == [TOKEN]

    def test_legacy_url_pattern_still_works_as_a_last_resort(self, scraper):
        got = scraper.parse_search_results(_html(_card(LEGACY_URL)))
        assert [l.listing_id for l in got] == ["7951348061"]

    def test_two_idless_cards_get_distinct_ids(self, scraper):
        """They must not collapse onto one blank key."""
        got = scraper.parse_search_results(
            _html(_card(MODERN_URL), _card(OTHER_URL)))
        assert len(got) == 2
        assert len({l.listing_id for l in got}) == 2
        assert all(l.listing_id for l in got)
