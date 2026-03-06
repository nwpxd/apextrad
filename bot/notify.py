"""
Discord webhook notifications (optional).

Configure in config.yaml:
  discord_webhook: "https://discord.com/api/webhooks/..."

Sends notifications on:
  - BUY/SELL executed
  - Circuit breaker activated
  - Daily loss limit hit
  - Critical errors
"""

import asyncio
import logging

log = logging.getLogger("notify")

try:
    import aiohttp
    _HAS_AIOHTTP = True
except ImportError:
    _HAS_AIOHTTP = False


class Notifier:
    def __init__(self, config: dict):
        self._webhook_url = config.get("discord_webhook", "")
        self._enabled = bool(self._webhook_url) and _HAS_AIOHTTP
        if self._enabled:
            log.info("Discord notifications enabled")

    async def send(self, title: str, message: str, color: int = 0x3b82f6):
        """Send a Discord embed notification."""
        if not self._enabled:
            return

        payload = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": color,
                "footer": {"text": "Trader Bot"},
            }]
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 204:
                        log.debug(f"Discord webhook returned {resp.status}")
        except Exception as e:
            log.debug(f"Discord notification failed: {e}")

    async def trade_buy(self, symbol: str, price: float, size: float, reason: str):
        await self.send(
            f"BUY {symbol}",
            f"Price: ${price:,.2f}\nSize: ${size:,.2f}\nReason: {reason}",
            color=0x22c55e,  # green
        )

    async def trade_sell(self, symbol: str, price: float, pnl: float, reason: str):
        color = 0x22c55e if pnl >= 0 else 0xef4444
        tag = "WIN" if pnl >= 0 else "LOSS"
        await self.send(
            f"SELL {symbol} — {tag}",
            f"Price: ${price:,.2f}\nP&L: {'+'if pnl>=0 else ''}${pnl:,.2f}\nReason: {reason}",
            color=color,
        )

    async def circuit_breaker(self, consecutive: int, pause_remaining: str):
        await self.send(
            "Circuit Breaker Activated",
            f"{consecutive} consecutive stop-losses\nTrading paused for {pause_remaining}",
            color=0xef4444,
        )

    async def daily_loss_limit(self, daily_pnl: float, limit_pct: float):
        await self.send(
            "Daily Loss Limit Hit",
            f"Daily P&L: ${daily_pnl:,.2f}\nLimit: {limit_pct*100:.0f}%\nTrading paused for 1h",
            color=0xef4444,
        )

    async def error(self, message: str):
        await self.send("Error", message, color=0xef4444)
