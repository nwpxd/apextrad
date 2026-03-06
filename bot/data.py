"""Market data + order execution via Binance."""

import logging
import pandas as pd
import ccxt.async_support as ccxt

log = logging.getLogger("data")


class Market:
    def __init__(self, api_key: str = "", secret: str = "", live: bool = False):
        self._exchange = None
        self._api_key = api_key
        self._secret = secret
        self._live = live

    async def connect(self):
        opts = {
            "enableRateLimit": True,
            "options": {"adjustForTimeDifference": True},
        }
        if self._api_key and self._secret:
            opts["apiKey"] = self._api_key
            opts["secret"] = self._secret
            log.info("Binance: authenticated (live trading enabled)")
        else:
            log.info("Binance: public mode (data only)")

        self._exchange = ccxt.binance(opts)
        await self._exchange.load_markets()
        log.info(f"Connected ({len(self._exchange.markets)} markets)")

    async def ohlcv(self, symbol: str, tf: str = "1h", limit: int = 100) -> pd.DataFrame | None:
        try:
            raw = await self._exchange.fetch_ohlcv(symbol, tf, limit=limit)
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
            df["ts"] = pd.to_datetime(df["ts"], unit="ms")
            df.set_index("ts", inplace=True)
            return df
        except Exception as e:
            log.debug(f"OHLCV error {symbol}: {e}")
            return None

    async def price(self, symbol: str) -> float | None:
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception:
            return None

    async def market_buy(self, symbol: str, quantity: float) -> dict | None:
        """Execute a real market buy order."""
        if not self._live:
            return None
        try:
            order = await self._exchange.create_market_buy_order(symbol, quantity)
            log.info(f"ORDER BUY {symbol} qty={quantity} id={order['id']}")
            return order
        except Exception as e:
            log.error(f"BUY ORDER FAILED {symbol}: {e}")
            return None

    async def market_sell(self, symbol: str, quantity: float) -> dict | None:
        """Execute a real market sell order."""
        if not self._live:
            return None
        try:
            order = await self._exchange.create_market_sell_order(symbol, quantity)
            log.info(f"ORDER SELL {symbol} qty={quantity} id={order['id']}")
            return order
        except Exception as e:
            log.error(f"SELL ORDER FAILED {symbol}: {e}")
            return None

    async def close(self):
        if self._exchange:
            await self._exchange.close()
