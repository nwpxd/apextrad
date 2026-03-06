"""
CryptoConnector - Binance (via ccxt) for spot crypto and memecoins.
Falls back to paper mode if no keys provided.
"""

import logging
from config.settings import Settings

log = logging.getLogger("CryptoConn")


class CryptoConnector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._exchange = None

    async def connect(self):
        if not self.settings.live_mode:
            log.info(" Crypto connector: PAPER mode (no real orders)")
            return

        try:
            import ccxt.async_support as ccxt
            self._exchange = ccxt.binance({
                "apiKey": self.settings.binance_api_key,
                "secret": self.settings.binance_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            })
            await self._exchange.load_markets()
            log.info("[OK] Crypto connector: connected to Binance (LIVE)")
        except Exception as e:
            log.error(f"Binance connection failed: {e}")
            raise

    async def get_balances(self) -> dict:
        if self._exchange is None:
            return {}
        balance = await self._exchange.fetch_balance()
        return {k: v for k, v in balance["total"].items() if v > 0}

    async def market_buy(self, symbol: str, quantity: float) -> dict:
        if self._exchange is None:
            log.info(f"[PAPER] market_buy {quantity:.6f} {symbol}")
            return {"id": "paper", "status": "paper"}
        try:
            order = await self._exchange.create_market_buy_order(symbol, quantity)
            log.info(f"[OK] BUY order placed: {order['id']} for {symbol}")
            return order
        except Exception as e:
            log.error(f"Buy order failed for {symbol}: {e}")
            return None

    async def market_sell(self, symbol: str, quantity: float) -> dict:
        if self._exchange is None:
            log.info(f"[PAPER] market_sell {quantity:.6f} {symbol}")
            return {"id": "paper", "status": "paper"}
        try:
            order = await self._exchange.create_market_sell_order(symbol, quantity)
            log.info(f"[OK] SELL order placed: {order['id']} for {symbol}")
            return order
        except Exception as e:
            log.error(f"Sell order failed for {symbol}: {e}")
            return None

    async def disconnect(self):
        if self._exchange:
            await self._exchange.close()
