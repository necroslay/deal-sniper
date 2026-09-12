"""
Headless-browser page renderer for Craigslist.

Craigslist search pages are rendered client-side: a plain HTTP GET returns an
~8 KB JavaScript shell with zero result rows, and the legacy ``?format=rss``
feed now returns 404 / 403. The only reliable no-account way to read search
results is to execute the page's JavaScript.

This module wraps Playwright's *sync* API and keeps a single browser process
alive across many page loads so a multi-page scrape doesn't pay the process
start-up cost per request.

Usage::

    with BrowserRenderer() as renderer:
        html = renderer.render("https://toronto.craigslist.org/search/cpd?query=gpu")

The renderer prefers the Chrome already installed on the machine
(``channel="chrome"``) and transparently falls back to Playwright's bundled
Chromium if that channel is unavailable.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class BrowserRenderError(RuntimeError):
    """Raised when Playwright is unavailable or a page cannot be rendered."""


class BrowserRenderer:
    """Renders JavaScript-heavy pages and returns the resulting HTML.

    The browser is started lazily on :meth:`start` (or on first use as a
    context manager) and torn down by :meth:`close`.
    """

    def __init__(
        self,
        headless: bool = True,
        channel: Optional[str] = "chrome",
        timeout_ms: int = 60_000,
        settle_ms: int = 1_500,
        user_agent: str = DEFAULT_UA,
        wait_for: Optional[str] = "div.cl-search-result",
        wait_for_ms: int = 12_000,
        block_images: bool = True,
    ) -> None:
        self.headless = headless
        self.channel = channel
        self.timeout_ms = timeout_ms
        self.settle_ms = settle_ms
        self.user_agent = user_agent
        self.wait_for = wait_for
        self.wait_for_ms = wait_for_ms
        self.block_images = block_images

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def start(self) -> "BrowserRenderer":
        """Launch the browser. Idempotent."""
        if self._browser is not None:
            return self

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - env dependent
            raise BrowserRenderError(
                "Playwright is required for Craigslist scraping. "
                "Install it with: pip install playwright"
            ) from exc

        self._pw = sync_playwright().start()

        launch_kwargs: dict = {"headless": self.headless}
        if self.channel:
            launch_kwargs["channel"] = self.channel

        try:
            self._browser = self._pw.chromium.launch(**launch_kwargs)
        except Exception as exc:
            if not self.channel:
                self.close()
                raise BrowserRenderError(f"Could not launch Chromium: {exc}") from exc
            logger.warning(
                "Browser channel '%s' unavailable (%s); "
                "falling back to bundled Chromium.", self.channel, exc,
            )
            launch_kwargs.pop("channel", None)
            try:
                self._browser = self._pw.chromium.launch(**launch_kwargs)
            except Exception as exc2:
                self.close()
                raise BrowserRenderError(
                    f"Could not launch any Chromium browser: {exc2}. "
                    "Run 'playwright install chromium' to download one."
                ) from exc2

        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            locale="en-CA",
            viewport={"width": 1366, "height": 900},
        )
        if self.block_images:
            # Images are irrelevant for parsing and cost bandwidth/time.
            self._context.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type == "image"
                else route.continue_(),
            )
        self._page = self._context.new_page()
        return self

    def close(self) -> None:
        """Tear down the browser process. Safe to call multiple times."""
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def __enter__(self) -> "BrowserRenderer":
        return self.start()

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ── rendering ────────────────────────────────────────────────────────────

    def render(self, url: str, wait_for: Optional[str] = None) -> Optional[str]:
        """Navigate to *url* and return the rendered HTML.

        Returns ``None`` if the page fails to load, so callers can decide
        whether to skip the page or abort the scrape.
        """
        self.start()
        assert self._page is not None

        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
        except Exception as exc:
            logger.error("Failed to load %s: %s", url, exc)
            return None

        selector = wait_for if wait_for is not None else self.wait_for
        if selector:
            try:
                self._page.wait_for_selector(selector, timeout=self.wait_for_ms)
            except Exception:
                # No matching rows — could legitimately be "no results found".
                logger.debug("No '%s' rows appeared for %s", selector, url)

        if self.settle_ms:
            try:
                self._page.wait_for_timeout(self.settle_ms)
            except Exception:
                pass

        try:
            return self._page.content()
        except Exception as exc:
            logger.error("Failed to read content from %s: %s", url, exc)
            return None
