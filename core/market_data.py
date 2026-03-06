"""
MarketDataHub - Unified market data for crypto, stocks, memecoins.
Uses ccxt for crypto (public API, no key needed for data), yfinance for stocks.
"""

import asyncio
import logging
import pandas as pd
from typing import Optional, Dict, Any
from config.settings import Settings

log = logging.getLogger("MarketData")


class MarketDataHub:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._cache: Dict[str, Any] = {}
        self._exchange = None

    async def connect(self):
        """Connect to Binance. No API key needed for public market data."""
        try:
            import ccxt.async_support as ccxt
            opts = {
                "enableRateLimit": True,
                "options": {"adjustForTimeDifference": True},
            }
            # Only pass keys if we have them (needed for trading, not for data)
            if self.settings.binance_api_key:
                opts["apiKey"] = self.settings.binance_api_key
                opts["secret"] = self.settings.binance_secret
            self._exchange = ccxt.binance(opts)
            # Test connectivity with a simple public call
            await self._exchange.load_markets()
            log.info(f"[OK] Binance connected ({len(self._exchange.markets)} markets)")
        except ImportError:
            log.warning("ccxt not installed - pip install ccxt")
        except Exception as e:
            log.warning(f"Binance connection failed: {e} - using offline mock data")
            self._exchange = None

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h", limit: int = 100) -> Optional[pd.DataFrame]:
        if self._is_stock(symbol):
            return await self._get_stock_ohlcv(symbol, limit)

        if self._exchange is None:
            return self._mock_ohlcv(symbol, limit)

        try:
            raw = await self._exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
            return df
        except Exception as e:
            log.debug(f"OHLCV fetch failed {symbol}: {e}")
            return None

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        if self._is_stock(symbol):
            return await self._get_stock_ticker(symbol)

        if self._exchange is None:
            return self._mock_ticker(symbol)

        try:
            return await self._exchange.fetch_ticker(symbol)
        except Exception as e:
            log.debug(f"Ticker fetch failed {symbol}: {e}")
            return None

    async def _get_stock_ohlcv(self, symbol: str, limit: int) -> Optional[pd.DataFrame]:
        try:
            import yfinance as yf
            clean = symbol.replace("/USD", "")
            ticker = yf.Ticker(clean)
            df = ticker.history(period="5d", interval="15m")
            if df is None or df.empty:
                df = ticker.history(period="30d", interval="1h")
            if df is None or df.empty:
                return None
            df.columns = [c.lower() for c in df.columns]
            if "close" not in df.columns:
                return None
            return df.tail(limit)
        except ImportError:
            log.warning("yfinance not installed")
            return None
        except Exception as e:
            log.debug(f"Stock OHLCV error {symbol}: {e}")
            return None

    async def _get_stock_ticker(self, symbol: str) -> Optional[dict]:
        try:
            import yfinance as yf
            clean = symbol.replace("/USD", "")
            info = yf.Ticker(clean).fast_info
            return {
                "last": info.last_price,
                "bid": info.last_price * 0.999,
                "ask": info.last_price * 1.001,
                "quoteVolume": getattr(info, "three_month_average_volume", 1_000_000) * info.last_price,
            }
        except Exception:
            return None

    def _is_stock(self, symbol: str) -> bool:
        return symbol.replace("/USD", "") in self.settings.stock_watchlist

    def _mock_ohlcv(self, symbol: str, limit: int) -> pd.DataFrame:
        """Offline fallback only - prefer real data via ccxt."""
        import numpy as np
        np.random.seed(hash(symbol) % 2**31)
        closes = 100 * np.cumprod(1 + np.random.randn(limit) * 0.01)
        dates = pd.date_range(end=pd.Timestamp.now(), periods=limit, freq="1h")
        return pd.DataFrame({
            "open": closes * 0.999,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.random.randint(100_000, 1_000_000, limit).astype(float),
        }, index=dates)

    def _mock_ticker(self, symbol: str) -> dict:
        """Offline fallback - deterministic price per symbol."""
        import hashlib
        h = int(hashlib.md5(symbol.encode()).hexdigest()[:8], 16)
        price = (h % 100000) / 100.0 + 1.0
        return {"last": price, "bid": price * 0.999, "ask": price * 1.001, "quoteVolume": 5_000_000}

    async def disconnect(self):
        if self._exchange:
            await self._exchange.close()
