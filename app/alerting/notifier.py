"""
Notifier — dispatches deal alerts to Discord/Telegram.

For MVP: Discord webhooks only. Telegram Bot API and Twilio SMS added later.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import requests

from app.models.schema import DealScore, ValuationOutput

logger = logging.getLogger(__name__)


# ── Discord Webhook ────────────────────────────────────────────────────────

class DiscordNotifier:
    """Send deal alerts via Discord webhook."""

    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url

    def send_alert(self, output: ValuationOutput) -> bool:
        """Send a deal alert embed to Discord."""
        if not self.webhook_url:
            logger.warning("No Discord webhook URL configured — alert suppressed")
            return False

        ds = output.deal_score
        rl = output.raw_listing

        # Build embed
        embed = {
            "title": f"💰 {ds.confidence_level.value} — {output.canonical_product.standard_name}",
            "url": rl.url,
            "color": self._color_for_confidence(ds.confidence_level.value),
            "fields": [
                {"name": "Asking Price", "value": f"${rl.price_cad:,.0f}", "inline": True},
                {"name": "Fair Market Value", "value": f"${ds.fmv_cad:,.0f}", "inline": True},
                {"name": "Expected Profit", "value": f"${ds.expected_profit_cad:,.0f} ({ds.margin_pct:.0f}%)", "inline": True},
                {"name": "Deal Score", "value": f"{ds.score:.0f}/100", "inline": True},
                {"name": "Condition", "value": output.fair_value.condition.replace("_", " ").title(), "inline": True},
                {"name": "Location", "value": rl.location or "N/A", "inline": True},
                {"name": "Source", "value": rl.source.value, "inline": True},
                {"name": "Comp Volume", "value": str(output.fair_value.comps_used), "inline": True},
            ],
            "footer": {"text": f"Confidence: {ds.confidence_level.value} | Score: {ds.score:.0f}"},
            "timestamp": ds.computed_at,
        }

        if rl.image_urls:
            embed["thumbnail"] = {"url": rl.image_urls[0]}

        if ds.recommendations:
            embed["fields"].append({
                "name": "Recommendations",
                "value": "\n".join(ds.recommendations),
            })

        payload = {"embeds": [embed]}

        try:
            resp = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            logger.info(f"Discord alert sent for {output.canonical_product.standard_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Discord alert: {e}")
            return False

    def _color_for_confidence(self, level: str) -> int:
        """Return Discord embed color for confidence level."""
        colors = {
            "ALERT_IMMEDIATE": 0x00FF00,  # green
            "ALERT_CAUTION": 0xFFFF00,    # yellow
            "REVIEW": 0xFFA500,           # orange
            "SUPPRESS": 0xFF0000,         # red
        }
        return colors.get(level, 0x808080)


# ── Telegram Notifier (placeholder) ────────────────────────────────────────

class TelegramNotifier:
    """Send deal alerts via Telegram Bot API."""

    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_alert(self, output: ValuationOutput) -> bool:
        """Send a Telegram message."""
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram not configured — alert suppressed")
            return False

        ds = output.deal_score
        rl = output.raw_listing

        text = (
            f"💰 *{ds.confidence_level.value}* — {output.canonical_product.standard_name}\n"
            f" Asking: ${rl.price_cad:,.0f} | FMV: ${ds.fmv_cad:,.0f}\n"
            f" Profit: ${ds.expected_profit_cad:,.0f} ({ds.margin_pct:.0f}%)\n"
            f" Score: {ds.score:.0f}/100 | {rl.location}\n"
            f"{rl.url}"
        )

        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            resp = requests.post(url, json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown",
            }, timeout=10)
            resp.raise_for_status()
            logger.info(f"Telegram alert sent for {output.canonical_product.standard_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False


# ── Composite Notifier ──────────────────────────────────────────────────────

class Notifier:
    """Composite notifier that dispatches to all configured channels."""

    def __init__(self, discord_url: Optional[str] = None, telegram_token: Optional[str] = None, telegram_chat: Optional[str] = None):
        self.discord = DiscordNotifier(discord_url)
        self.telegram = TelegramNotifier(telegram_token, telegram_chat)

    def send_alert(self, output: ValuationOutput) -> bool:
        """Send to all configured channels. Returns True if any succeeded."""
        results = []
        if self.discord.webhook_url:
            results.append(self.discord.send_alert(output))
        if self.telegram.bot_token and self.telegram.chat_id:
            results.append(self.telegram.send_alert(output))
        return any(results)