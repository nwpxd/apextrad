"""
Trading engine. Connects all modules. Runs the loop.

Features:
  - Multi-timeframe analysis (1h + 4h)
  - Market regime awareness
  - Circuit breaker integration
  - Correlation-based position limits
  - Balance reconciliation (live mode)
  - Separate order logging
"""

import asyncio
import logging
from collections import deque
from decimal import Decimal
from datetime import datetime, date

from bot.data import Market
from bot.portfolio import Portfolio
from bot.tracker import Tracker
from bot.risk import (
    position_size_atr,
    check_correlation,
    CircuitBreaker,
)
from bot import strategy

log = logging.getLogger("engine")


class Engine:
    def __init__(self, config: dict):
        self.config = config
        self.market = Market(
            api_key=config.get("binance_api_key", ""),
            secret=config.get("binance_secret", ""),
            live=config.get("live", False),
            config=config,
        )
        self.portfolio = Portfolio(config["capital"])
        self.tracker = Tracker()
        self.circuit_breaker = CircuitBreaker(
            max_consecutive=config.get("circuit_breaker", 3),
        )
        self.running = False
        self.live = config.get("live", False)

        # State
        self.daily_pnl = Decimal("0")
        self.trades_today = 0
        self._last_reset = date.today()
        self._last_reconcile = datetime.now()
        self.current_regime = "unknown"

        # Signal log for dashboard
        self.signals: deque = deque(maxlen=50)

        # HTF data cache
        self._htf_cache: dict[str, dict] = {}

    async def start(self):
        self.running = True
        await self.market.connect()

        mode = "LIVE" if self.live else "PAPER"
        log.info(f"Engine started [{mode}]")

        if self.live and not self.config.get("binance_api_key"):
            log.error("LIVE mode requires BINANCE_API_KEY and BINANCE_SECRET in .env")
            self.running = False
            return

        tasks = [
            asyncio.create_task(self._loop()),
            asyncio.create_task(self._stop_checker()),
            asyncio.create_task(self._monitor()),
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass

    async def stop(self):
        self.running = False
        await self.market.close()
        log.info("Engine stopped")

    async def _loop(self):
        interval = self.config.get("scan_interval", 60)
        symbols = self.config["symbols"]

        while self.running:
            self._reset_daily()

            # Daily loss limit
            max_loss = Decimal(str(self.config["capital"])) * Decimal(str(self.config.get("max_daily_loss", 0.05)))
            if self.daily_pnl < -max_loss:
                log.warning(f"Daily loss limit hit (${self.daily_pnl:,.2f}). Pausing 1h.")
                await asyncio.sleep(3600)
                continue

            # Circuit breaker
            if self.circuit_breaker.is_paused():
                remaining = self.circuit_breaker.remaining_pause
                log.warning(f"Circuit breaker active. Resuming in {remaining}.")
                await asyncio.sleep(60)
                continue

            log.info(f"Scanning {len(symbols)} symbols...")

            for symbol in symbols:
                if not self.running:
                    break
                try:
                    await self._evaluate(symbol)
                except Exception as e:
                    log.warning(f"{symbol}: {e}")

            # Record equity
            self.portfolio.record_equity()

            # Reconcile with Binance periodically
            await self._maybe_reconcile()

            await asyncio.sleep(interval)

    async def _stop_checker(self):
        """Check stop-losses every 15 seconds."""
        while self.running:
            for symbol in list(self.portfolio.positions.keys()):
                try:
                    price_now = await self.market.price(symbol)
                    if price_now is None:
                        continue
                    price = Decimal(str(price_now))
                    self.portfolio.update_price(symbol, price)

                    stop_reason = self.portfolio.check_stops(symbol, price)
                    if stop_reason:
                        await self._execute_sell(symbol, price, stop_reason)
                except Exception as e:
                    log.debug(f"Stop check error {symbol}: {e}")
            await asyncio.sleep(15)

    async def _evaluate(self, symbol: str):
        tf = self.config.get("timeframe", "1h")
        htf = self.config.get("htf", "4h")

        # Fetch current timeframe data
        df = await self.market.ohlcv(symbol, tf, limit=100)
        if df is None or len(df) < 50:
            return

        price_now = await self.market.price(symbol)
        if price_now is None:
            return

        price = Decimal(str(price_now))
        self.portfolio.update_price(symbol, price)

        # Fetch higher timeframe data (cached, refresh every 15 min)
        df_htf = await self._get_htf_data(symbol, htf)

        # Run strategy with regime detection and HTF filter
        sig = strategy.signal(df, config=self.config, df_htf=df_htf)

        # Track regime
        self.current_regime = sig.get("regime", "unknown")

        # Log signal
        self.signals.append({
            "time": datetime.now(),
            "symbol": symbol,
            "action": sig["action"],
            "score": sig["score"],
            "reason": sig["reason"],
            "regime": sig.get("regime", ""),
        })

        # BUY
        if sig["action"] == "BUY" and symbol not in self.portfolio.positions:
            if self.portfolio.is_on_cooldown(symbol):
                return
            # Correlation check
            if not check_correlation(
                symbol,
                self.portfolio.positions,
                self.config.get("max_correlated", 2),
            ):
                log.debug(f"{symbol}: blocked by correlation limit")
                return
            await self._execute_buy(symbol, price, sig)

        # SELL (strategy says sell)
        elif sig["action"] == "SELL" and symbol in self.portfolio.positions:
            await self._execute_sell(symbol, price, sig["reason"])

    async def _get_htf_data(self, symbol: str, htf: str) -> pd.DataFrame | None:
        """Get higher timeframe data with 15-min cache."""
        cached = self._htf_cache.get(symbol)
        now = datetime.now()

        if cached and (now - cached["fetched"]).total_seconds() < 900:
            return cached["df"]

        df_htf = await self.market.ohlcv(symbol, htf, limit=100)
        if df_htf is not None:
            self._htf_cache[symbol] = {"df": df_htf, "fetched": now}
        return df_htf

    async def _execute_buy(self, symbol: str, price: Decimal, sig: dict):
        atr_val = sig.get("atr", float(price) * 0.02)

        # ATR-based position sizing
        size = position_size_atr(
            cash=self.portfolio.cash,
            risk_usd=self.config.get("risk_per_trade_usd", 15),
            atr_value=atr_val,
            price=float(price),
            atr_sl_mult=self.config.get("atr_sl_mult", 2.0),
        )
        if size is None:
            return

        # Check max positions
        if len(self.portfolio.positions) >= self.config.get("max_positions", 5):
            return

        # Validate order against exchange limits
        quantity = float(size / price)
        valid, msg = self.market.validate_order(symbol, quantity, float(price))
        if not valid:
            log.debug(f"{symbol}: order rejected - {msg}")
            return

        ok = self.portfolio.buy(symbol, price, size, atr=atr_val, config=self.config)
        if not ok:
            return

        self.trades_today += 1

        # Execute real order in live mode
        if self.live:
            pos = self.portfolio.positions.get(symbol)
            if pos:
                qty = float(pos["quantity"])
                if self.config.get("use_limit_orders", False):
                    offset = 1 - self.config.get("limit_order_offset_pct", 0.05) / 100
                    limit_price = float(price) * offset
                    await self.market.limit_buy(symbol, qty, limit_price)
                else:
                    await self.market.market_buy(symbol, qty)

    async def _execute_sell(self, symbol: str, price: Decimal, reason: str):
        pos = self.portfolio.positions.get(symbol)
        if not pos:
            return

        quantity = float(pos["quantity"])
        entry = float(pos["entry"])

        pnl = self.portfolio.sell(symbol, price, reason)
        self.daily_pnl += pnl
        self.trades_today += 1

        # Circuit breaker tracking
        if reason == "stop_loss":
            self.circuit_breaker.record_stop_loss()
        elif pnl > 0:
            self.circuit_breaker.record_win()

        self.tracker.record(
            symbol=symbol,
            entry=entry,
            exit_price=float(price),
            pnl=float(pnl),
            reason=reason,
        )

        # Execute real order in live mode
        if self.live:
            if self.config.get("use_limit_orders", False):
                offset = 1 + self.config.get("limit_order_offset_pct", 0.05) / 100
                limit_price = float(price) * offset
                await self.market.limit_sell(symbol, quantity, limit_price)
            else:
                await self.market.market_sell(symbol, quantity)

    async def _maybe_reconcile(self):
        """Reconcile internal state with Binance balances."""
        if not self.live:
            return

        interval = self.config.get("reconcile_interval", 3600)
        elapsed = (datetime.now() - self._last_reconcile).total_seconds()
        if elapsed < interval:
            return

        self._last_reconcile = datetime.now()
        balance = await self.market.fetch_balance()
        if not balance:
            return

        usdt_free = balance["free"].get("USDT", 0)
        usdt_total = balance["total"].get("USDT", 0)
        log.info(f"RECONCILE | Binance USDT: free={usdt_free:.2f} total={usdt_total:.2f} | Internal cash: ${self.portfolio.cash:,.2f}")

        # Log any significant discrepancy
        diff = abs(float(self.portfolio.cash) - usdt_free)
        if diff > 1.0:
            log.warning(f"RECONCILE | Cash discrepancy: ${diff:.2f}")

    def _reset_daily(self):
        today = date.today()
        if today != self._last_reset:
            log.info("New day - counters reset")
            self.daily_pnl = Decimal("0")
            self.trades_today = 0
            self._last_reset = today

    async def _monitor(self):
        while self.running:
            await asyncio.sleep(300)
            v = self.portfolio.value
            n = len(self.portfolio.positions)
            regime = self.current_regime
            cb = f" | CB pause {self.circuit_breaker.remaining_pause}" if self.circuit_breaker.is_paused() else ""
            log.info(
                f"Value: ${v:,.2f} | Positions: {n} | "
                f"Daily PnL: ${self.daily_pnl:+,.2f} | "
                f"Regime: {regime}{cb}"
            )
