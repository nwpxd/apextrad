"""
Market data + order execution via Binance.

Features:
  - Limit orders (configurable) or market orders
  - Minimum notional / step size checks
  - Retry with exponential backoff
  - Balance reconciliation
  - Separate order logging
"""

import asyncio
import logging
from pathlib import Path

import pandas as pd
import ccxt.async_support as ccxt

log = logging.getLogger("data")

# Separate logger for orders
order_log = logging.getLogger("orders")
_order_handler = logging.FileHandler(Path("logs") / "orders.log", encoding="utf-8")
_order_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
order_log.addHandler(_order_handler)
order_log.setLevel(logging.INFO)


class Market:
    def __init__(self, api_key: str = "", secret: str = "", live: bool = False, config: dict | None = None):
        self._exchange: ccxt.binance | None = None
        self._api_key = api_key
        self._secret = secret
        self._live = live
        self._config = config or {}
        self._markets_info: dict = {}

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
        self._markets_info = self._exchange.markets
        log.info(f"Connected ({len(self._markets_info)} markets)")

    async def ohlcv(self, symbol: str, tf: str = "1h", limit: int = 100) -> pd.DataFrame | None:
        return await self._retry(self._fetch_ohlcv, symbol, tf, limit)

    async def _fetch_ohlcv(self, symbol: str, tf: str, limit: int) -> pd.DataFrame | None:
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
        return await self._retry(self._fetch_price, symbol)

    async def _fetch_price(self, symbol: str) -> float | None:
        try:
            ticker = await self._exchange.fetch_ticker(symbol)
            return float(ticker["last"])
        except Exception:
            return None

    async def ohlcv_history(self, symbol: str, tf: str, since_ms: int, limit: int = 1000) -> pd.DataFrame | None:
        """Fetch historical OHLCV data from a specific timestamp. Used by backtester."""
        all_data = []
        current_since = since_ms

        while True:
            try:
                raw = await self._exchange.fetch_ohlcv(symbol, tf, since=current_since, limit=limit)
                if not raw:
                    break
                all_data.extend(raw)
                if len(raw) < limit:
                    break
                current_since = raw[-1][0] + 1
                await asyncio.sleep(0.5)  # rate limit
            except Exception as e:
                log.warning(f"History fetch error {symbol}: {e}")
                break

        if not all_data:
            return None

        df = pd.DataFrame(all_data, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms")
        df.set_index("ts", inplace=True)
        df = df[~df.index.duplicated(keep="first")]
        return df

    def get_min_notional(self, symbol: str) -> float:
        """Get minimum order value for a symbol."""
        info = self._markets_info.get(symbol, {})
        limits = info.get("limits", {})
        cost_limits = limits.get("cost", {})
        return float(cost_limits.get("min", 10.0) or 10.0)

    def get_step_size(self, symbol: str) -> float:
        """Get minimum quantity step for a symbol."""
        info = self._markets_info.get(symbol, {})
        precision = info.get("precision", {})
        amount_precision = precision.get("amount", 8)
        return 10 ** (-amount_precision)

    def get_min_quantity(self, symbol: str) -> float:
        info = self._markets_info.get(symbol, {})
        limits = info.get("limits", {})
        amount_limits = limits.get("amount", {})
        return float(amount_limits.get("min", 0) or 0)

    def validate_order(self, symbol: str, quantity: float, price: float) -> tuple[bool, str]:
        """Validate order against exchange limits."""
        min_notional = self.get_min_notional(symbol)
        notional = quantity * price
        if notional < min_notional:
            return False, f"notional {notional:.2f} < min {min_notional}"

        min_qty = self.get_min_quantity(symbol)
        if quantity < min_qty:
            return False, f"qty {quantity} < min {min_qty}"

        return True, ""

    async def market_buy(self, symbol: str, quantity: float) -> dict | None:
        if not self._live:
            return None
        return await self._retry(self._exec_market_buy, symbol, quantity)

    async def _exec_market_buy(self, symbol: str, quantity: float) -> dict | None:
        try:
            order = await self._exchange.create_market_buy_order(symbol, quantity)
            fill_price = float(order.get("average", order.get("price", 0)))
            order_log.info(f"BUY {symbol} qty={quantity} fill={fill_price} id={order['id']}")
            log.info(f"ORDER BUY {symbol} qty={quantity} fill={fill_price} id={order['id']}")
            return order
        except Exception as e:
            order_log.error(f"BUY FAILED {symbol} qty={quantity}: {e}")
            log.error(f"BUY ORDER FAILED {symbol}: {e}")
            return None

    async def market_sell(self, symbol: str, quantity: float) -> dict | None:
        if not self._live:
            return None
        return await self._retry(self._exec_market_sell, symbol, quantity)

    async def _exec_market_sell(self, symbol: str, quantity: float) -> dict | None:
        try:
            order = await self._exchange.create_market_sell_order(symbol, quantity)
            fill_price = float(order.get("average", order.get("price", 0)))
            order_log.info(f"SELL {symbol} qty={quantity} fill={fill_price} id={order['id']}")
            log.info(f"ORDER SELL {symbol} qty={quantity} fill={fill_price} id={order['id']}")
            return order
        except Exception as e:
            order_log.error(f"SELL FAILED {symbol} qty={quantity}: {e}")
            log.error(f"SELL ORDER FAILED {symbol}: {e}")
            return None

    async def limit_buy(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit buy order slightly below market."""
        if not self._live:
            return None
        try:
            order = await self._exchange.create_limit_buy_order(symbol, quantity, price)
            order_log.info(f"LIMIT BUY {symbol} qty={quantity} price={price} id={order['id']}")
            return order
        except Exception as e:
            order_log.error(f"LIMIT BUY FAILED {symbol}: {e}")
            log.error(f"LIMIT BUY FAILED {symbol}: {e}")
            return None

    async def limit_sell(self, symbol: str, quantity: float, price: float) -> dict | None:
        """Place a limit sell order slightly above market."""
        if not self._live:
            return None
        try:
            order = await self._exchange.create_limit_sell_order(symbol, quantity, price)
            order_log.info(f"LIMIT SELL {symbol} qty={quantity} price={price} id={order['id']}")
            return order
        except Exception as e:
            order_log.error(f"LIMIT SELL FAILED {symbol}: {e}")
            log.error(f"LIMIT SELL FAILED {symbol}: {e}")
            return None

    async def fetch_balance(self) -> dict | None:
        """Fetch real Binance balances for reconciliation."""
        if not self._live or not self._exchange:
            return None
        try:
            balance = await self._exchange.fetch_balance()
            return {
                "free": {k: float(v) for k, v in balance["free"].items() if float(v) > 0},
                "total": {k: float(v) for k, v in balance["total"].items() if float(v) > 0},
            }
        except Exception as e:
            log.warning(f"Balance fetch error: {e}")
            return None

    async def _retry(self, func, *args):
        """Retry with exponential backoff."""
        max_retries = self._config.get("retry_max", 3)
        base_delay = self._config.get("retry_base_delay", 2)

        for attempt in range(max_retries):
            result = await func(*args)
            if result is not None:
                return result
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                log.debug(f"Retry {attempt + 1}/{max_retries} in {delay}s")
                await asyncio.sleep(delay)
        return None

    async def close(self):
        if self._exchange:
            await self._exchange.close()
