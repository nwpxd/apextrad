"""Market data via Binance public API. No key needed."""

import logging
import pandas as pd
import ccxt.async_support as ccxt

log = logging.getLogger("data")


class Market:
    def __init__(self):
        self._exchange = None

    async def connect(self):
        self._exchange = ccxt.binance({
            "enableRateLimit": True,
            "options": {"adjustForTimeDifference": True},
        })
        await self._exchange.load_markets()
        log.info(f"Binance connected ({len(self._exchange.markets)} markets)")

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

    async def close(self):
        if self._exchange:
            await self._exchange.close()
